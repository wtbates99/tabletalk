"""Public project lifecycle centered on first-class Agent resources."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, overload

import yaml

from tabletalk.agents import AgentDefinition
from tabletalk.compiler import CompiledArtifact, semantic_changes
from tabletalk.dbt_manifest import DbtManifest
from tabletalk.domain import (
    ErrorCode,
    QueryAnswer,
    RuntimeStage,
    TableTalkError,
    to_primitive,
)
from tabletalk.factories import get_db_provider


@dataclass(frozen=True)
class ProjectPlan:
    agent: str
    candidate_digest: str | None
    applied_digest: str | None
    changes: tuple[str, ...]

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)


@dataclass(frozen=True)
class AppliedAgent:
    name: str
    artifact_digest: str
    eval_receipts: tuple[str, ...]
    previous_artifact_digest: str | None
    applied_at: str


class Project:
    """A TableTalk project loaded from declarative source files."""

    def __init__(self, root: Path, config: dict[str, Any]) -> None:
        self.root = root
        self.config = config

    @classmethod
    def load(cls, path: str | Path = ".") -> Project:
        root = Path(path).resolve()
        config_path = root / "tabletalk.yaml"
        if not config_path.is_file():
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                f"TableTalk project configuration was not found at '{config_path}'.",
            )
        try:
            config = yaml.safe_load(config_path.read_text())
        except (OSError, yaml.YAMLError) as error:
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                "tabletalk.yaml could not be loaded.",
            ) from error
        if not isinstance(config, dict):
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                "tabletalk.yaml must be a mapping.",
            )
        return cls(root, config)

    @property
    def agents_directory(self) -> Path:
        configured = self.config.get("agents", "agents")
        if not isinstance(configured, str) or not configured:
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                "tabletalk.yaml agents must be a directory path.",
            )
        return self.root / configured

    def agents(self) -> tuple[AgentDefinition, ...]:
        folder = self.agents_directory
        if not folder.is_dir():
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                f"Agent source directory '{folder}' does not exist.",
            )
        definitions = tuple(
            AgentDefinition.load(path)
            for path in sorted((*folder.glob("*.yaml"), *folder.glob("*.yml")))
        )
        names = [definition.name for definition in definitions]
        duplicate = next(
            (name for name in sorted(set(names)) if names.count(name) > 1),
            None,
        )
        if duplicate:
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                f"Agent '{duplicate}' is defined more than once.",
            )
        if not definitions:
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                f"No Agent resources were found in '{folder}'.",
            )
        return definitions

    def agent(self, name: str) -> AgentDefinition:
        matches = [definition for definition in self.agents() if definition.name == name]
        if len(matches) != 1:
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                f"Agent source '{name}' was not found.",
                details={"agent": name},
            )
        return matches[0]

    def connection_config(self, name: str) -> dict[str, Any]:
        connections = self.config.get("connections")
        if isinstance(connections, dict) and isinstance(connections.get(name), dict):
            value = dict(connections[name])
        else:
            single = self.config.get("connection")
            if (
                isinstance(single, dict)
                and str(single.get("name") or "default") == name
            ):
                value = dict(single)
            elif name == "default" and isinstance(self.config.get("provider"), dict):
                value = dict(self.config["provider"])
            else:
                raise TableTalkError(
                    ErrorCode.CONFIG_INVALID,
                    RuntimeStage.CONFIGURATION,
                    f"Connection '{name}' was not found in tabletalk.yaml.",
                    details={"connection": name},
                )
        if "path" in value and "database_path" not in value:
            value["database_path"] = value.pop("path")
        value.pop("name", None)
        database_path = value.get("database_path")
        if (
            isinstance(database_path, str)
            and database_path != ":memory:"
            and not Path(database_path).is_absolute()
        ):
            value["database_path"] = str((self.root / database_path).resolve())
        return value

    @staticmethod
    def _schemas_for(definition: AgentDefinition) -> tuple[str, ...]:
        schemas = set()
        for pattern in (*definition.relations.include, *definition.relations.exclude):
            if "." not in pattern:
                raise TableTalkError(
                    ErrorCode.CONFIG_INVALID,
                    RuntimeStage.CONFIGURATION,
                    f"Relation pattern '{pattern}' must be schema-qualified.",
                )
            schema = pattern.rsplit(".", 1)[0]
            if any(character in schema for character in "*?["):
                raise TableTalkError(
                    ErrorCode.CONFIG_INVALID,
                    RuntimeStage.CONFIGURATION,
                    "Schema wildcards are not supported; name each allowed schema explicitly.",
                    details={"pattern": pattern},
                )
            schemas.add(schema)
        return tuple(sorted(schemas))

    def _schema_snapshot(
        self,
        definition: AgentDefinition,
    ) -> tuple[dict[str, Any], str, str]:
        connection = self.connection_config(definition.connection)
        connection_type = str(connection.get("type") or "")
        dialect = {
            "sqlite": "sqlite",
            "duckdb": "duckdb",
            "snowflake": "snowflake",
        }.get(connection_type)
        if dialect is None:
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                f"Connection '{definition.connection}' uses unsupported type "
                f"'{connection_type}'.",
            )
        schemas = self._schemas_for(definition)
        try:
            provider = get_db_provider(connection)
            compact = [
                table
                for schema in schemas
                for table in provider.get_compact_tables(schema)
            ]
        except TableTalkError:
            raise
        except Exception as error:
            raise TableTalkError(
                ErrorCode.DATABASE_UNAVAILABLE,
                RuntimeStage.COMPILATION,
                f"Connection '{definition.connection}' could not be introspected.",
                details={
                    "connection": definition.connection,
                    "database_type": connection_type,
                },
            ) from error

        snapshot: dict[str, Any] = {}
        dbt_manifest = None
        dbt_config = self.config.get("dbt")
        if dbt_config:
            try:
                dbt_manifest = DbtManifest.load(self.root, dbt_config)
            except (OSError, ValueError) as error:
                raise TableTalkError(
                    ErrorCode.CONFIG_INVALID,
                    RuntimeStage.COMPILATION,
                    "Configured dbt artifacts could not be loaded.",
                    details={"dbt_configured": True},
                ) from error
        for table in compact:
            raw_name = str(table.get("t") or "")
            if not raw_name:
                continue
            matching_schema = next(
                (
                    schema
                    for schema in schemas
                    if raw_name.lower().startswith(f"{schema.lower()}.")
                ),
                None,
            )
            if matching_schema is None:
                if len(schemas) != 1:
                    raise TableTalkError(
                        ErrorCode.CONFIG_INVALID,
                        RuntimeStage.COMPILATION,
                        f"Unqualified relation '{raw_name}' is ambiguous.",
                    )
                relation_name = f"{schemas[0]}.{raw_name}"
            else:
                relation_name = raw_name
            fields = table.get("f") or []
            relation_schema, relation_table = relation_name.rsplit(".", 1)
            dbt_relation = (
                dbt_manifest.relation(relation_schema, relation_table)
                if dbt_manifest is not None
                else None
            )
            snapshot[relation_name] = {
                "description": (
                    dbt_relation.description
                    if dbt_relation and dbt_relation.description
                    else str(table.get("d") or "")
                ),
                "columns": [
                    {
                        "name": str(field.get("n") or ""),
                        "data_type": str(field.get("t") or "unknown"),
                        "description": (
                            dbt_relation.columns.get(str(field.get("n") or ""), "")
                            if dbt_relation
                            else str(field.get("d") or "")
                        )
                        or str(field.get("d") or ""),
                        "provenance": (
                            "dbt_column_description"
                            if dbt_relation
                            and dbt_relation.columns.get(
                                str(field.get("n") or ""), ""
                            )
                            else "database_metadata"
                        ),
                    }
                    for field in fields
                    if isinstance(field, dict) and field.get("n")
                ],
                "primary_key": [
                    str(field["n"])
                    for field in fields
                    if isinstance(field, dict) and field.get("n") and field.get("pk")
                ],
                "foreign_keys": [
                    {
                        "column": str(field["n"]),
                        "references": (
                            str(field["fk"])
                            if "." in str(field["fk"]).rsplit(".", 1)[0]
                            else f"{relation_schema}.{field['fk']}"
                        ),
                        "provenance": "database_constraint",
                    }
                    for field in fields
                    if isinstance(field, dict)
                    and field.get("n")
                    and field.get("fk")
                ],
                "dbt": (
                    {
                        "node": dbt_relation.unique_id,
                        "resource_type": dbt_relation.resource_type,
                        "lineage": dbt_relation.depends_on,
                        "tests": dbt_relation.tests,
                        "materialized": dbt_relation.materialized,
                        "tags": dbt_relation.tags,
                        "group": dbt_relation.group,
                        "owner": dbt_relation.owner,
                        "manifest_digest": hashlib.sha256(
                            dbt_manifest.path.read_bytes()
                        ).hexdigest(),
                    }
                    if dbt_relation and dbt_manifest is not None
                    else {}
                ),
            }
        return snapshot, connection_type, dialect

    def _write_candidate(self, artifact: CompiledArtifact) -> Path:
        folder = self.root / ".tabletalk" / "artifacts" / artifact.agent.name
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{artifact.digest}.json"
        content = artifact.to_json() + "\n"
        if path.is_file() and path.read_text() != content:
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.COMPILATION,
                "An artifact digest collision or mutation was detected.",
                details={"artifact_digest": artifact.digest},
            )
        if not path.exists():
            temporary = path.with_suffix(".tmp")
            temporary.write_text(content)
            os.replace(temporary, path)
        return path

    @overload
    def compile(self, agent: str) -> CompiledArtifact: ...

    @overload
    def compile(self, agent: None = None) -> tuple[CompiledArtifact, ...]: ...

    def compile(
        self,
        agent: str | None = None,
    ) -> CompiledArtifact | tuple[CompiledArtifact, ...]:
        definitions = (self.agent(agent),) if agent else self.agents()
        artifacts = []
        for definition in definitions:
            snapshot, connection_type, dialect = self._schema_snapshot(definition)
            artifact = definition.compile(
                snapshot,
                connection_type=connection_type,
                dialect=dialect,
            )
            self._write_candidate(artifact)
            artifacts.append(artifact)
        if agent:
            return artifacts[0]
        return tuple(artifacts)

    def _applied_state(self) -> dict[str, dict[str, Any]]:
        state_path = self.root / ".tabletalk" / "state.json"
        if not state_path.is_file():
            return {}
        try:
            state = json.loads(state_path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                "Applied state could not be loaded.",
            ) from error
        if not isinstance(state, dict) or state.get("schema_version") != 2:
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                "Applied state uses an unsupported schema version.",
            )
        agents = state.get("agents")
        if not isinstance(agents, dict) or not all(
            isinstance(name, str) and isinstance(entry, dict)
            for name, entry in agents.items()
        ):
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                "Applied state agents must be a mapping.",
            )
        return {str(name): dict(entry) for name, entry in agents.items()}

    def _applied_artifact(
        self,
        name: str,
        entry: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        digest = entry.get("artifact_digest")
        if not isinstance(digest, str) or not digest:
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                f"Applied Agent '{name}' has no artifact digest.",
            )
        path = self.root / ".tabletalk" / "artifacts" / name / f"{digest}.json"
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                f"Applied Agent '{name}' artifact is unreadable.",
            ) from error
        agent = payload.get("agent") if isinstance(payload, dict) else None
        if not isinstance(agent, dict) or payload.get("digest") != digest:
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                f"Applied Agent '{name}' artifact is malformed.",
            )
        return digest, agent

    def plan(self, candidate: CompiledArtifact) -> ProjectPlan:
        entry = self._applied_state().get(candidate.agent.name)
        applied_digest: str | None = None
        applied_agent: dict[str, Any] | None = None
        if entry is not None:
            applied_digest, applied_agent = self._applied_artifact(
                candidate.agent.name,
                entry,
            )
        return ProjectPlan(
            agent=candidate.agent.name,
            candidate_digest=candidate.digest,
            applied_digest=applied_digest,
            changes=semantic_changes(applied_agent, to_primitive(candidate.agent)),
        )

    def plans(
        self,
        candidates: tuple[CompiledArtifact, ...],
        *,
        include_removals: bool,
    ) -> tuple[ProjectPlan, ...]:
        results = [self.plan(candidate) for candidate in candidates]
        if include_removals:
            candidate_names = {candidate.agent.name for candidate in candidates}
            for name, entry in sorted(self._applied_state().items()):
                if name in candidate_names:
                    continue
                digest, applied_agent = self._applied_artifact(name, entry)
                results.append(
                    ProjectPlan(
                        agent=name,
                        candidate_digest=None,
                        applied_digest=digest,
                        changes=semantic_changes(applied_agent, None),
                    )
                )
        return tuple(sorted(results, key=lambda item: item.agent))

    def evaluate(self, candidate: CompiledArtifact):
        """Run every eval declared by the candidate and write exact receipts."""
        from tabletalk.evals import (
            EvalRunner,
            create_eval_receipt,
            load_eval_suite,
            write_eval_receipt,
        )

        reports = []
        eval_directory = self.root / str(self.config.get("evals", "evals"))
        suites = {}
        if eval_directory.is_dir():
            for path in sorted(
                (*eval_directory.glob("*.yaml"), *eval_directory.glob("*.yml"))
            ):
                suite = load_eval_suite(path)
                if suite.name in suites:
                    raise TableTalkError(
                        ErrorCode.CONFIG_INVALID,
                        RuntimeStage.CONFIGURATION,
                        f"Eval suite '{suite.name}' is defined more than once.",
                    )
                suites[suite.name] = suite
        for required in candidate.agent.required_evals:
            selected_suite = suites.get(required)
            if selected_suite is None:
                raise TableTalkError(
                    ErrorCode.REQUIRED_EVAL_MISSING,
                    RuntimeStage.CONFIGURATION,
                    f"Required eval suite '{required}' was not found.",
                    details={"agent": candidate.agent.name, "suite": required},
                )
            if selected_suite.agent != candidate.agent.name:
                raise TableTalkError(
                    ErrorCode.CONFIG_INVALID,
                    RuntimeStage.CONFIGURATION,
                    f"Eval suite '{required}' targets agent '{selected_suite.agent}', not "
                    f"'{candidate.agent.name}'.",
                )
            result = EvalRunner(
                selected_suite,
                project_folder=str(self.root),
                candidate=candidate,
            ).run()
            write_eval_receipt(
                create_eval_receipt(result, selected_suite, self.root),
                self.root,
            )
            reports.append(result)
        return tuple(reports)

    def _receipt_digests(
        self,
        candidate: CompiledArtifact,
    ) -> tuple[str, ...]:
        from tabletalk.evals import load_eval_suite, matching_eval_receipt

        artifact_path = (
            self.root
            / ".tabletalk"
            / "artifacts"
            / candidate.agent.name
            / f"{candidate.digest}.json"
        )
        if (
            not artifact_path.is_file()
            or artifact_path.read_text() != candidate.to_json() + "\n"
        ):
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.COMPILATION,
                "Candidate artifact is missing or differs from its content-addressed file.",
                details={
                    "agent": candidate.agent.name,
                    "artifact_digest": candidate.digest,
                },
            )
        receipt_digests: list[str] = []
        eval_directory = self.root / str(self.config.get("evals", "evals"))
        suite_paths = sorted(
            (*eval_directory.glob("*.yaml"), *eval_directory.glob("*.yml"))
        )
        suites_by_name: dict[str, list[Path]] = {}
        for path in suite_paths:
            suite = load_eval_suite(path)
            suites_by_name.setdefault(suite.name, []).append(path)
        for suite_name in candidate.agent.required_evals:
            suite_matches = suites_by_name.get(suite_name, [])
            if len(suite_matches) != 1:
                raise TableTalkError(
                    ErrorCode.REQUIRED_EVAL_MISSING,
                    RuntimeStage.CONFIGURATION,
                    f"Required eval suite '{suite_name}' was not found exactly once.",
                )
            suite_digest = hashlib.sha256(suite_matches[0].read_bytes()).hexdigest()
            receipt = matching_eval_receipt(
                self.root,
                suite_name,
                candidate.agent.name,
                candidate.digest,
                suite_digest,
            )
            if receipt is None:
                raise TableTalkError(
                    ErrorCode.REQUIRED_EVAL_MISSING,
                    RuntimeStage.CONFIGURATION,
                    f"Required eval '{suite_name}' has no passing receipt for the "
                    "candidate artifact.",
                    details={
                        "agent": candidate.agent.name,
                        "suite": suite_name,
                        "artifact_digest": candidate.digest,
                    },
                )
            receipt_digests.append(str(receipt["digest"]))
        return tuple(sorted(receipt_digests))

    def apply_many(
        self,
        candidates: tuple[CompiledArtifact, ...],
        *,
        remove_absent: bool = False,
    ) -> tuple[AppliedAgent, ...]:
        """Atomically activate an evaluated candidate set."""
        names = [candidate.agent.name for candidate in candidates]
        if len(names) != len(set(names)):
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.COMPILATION,
                "Candidate set contains duplicate Agent names.",
            )
        receipts = {
            candidate.agent.name: self._receipt_digests(candidate)
            for candidate in candidates
        }
        state_path = self.root / ".tabletalk" / "state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        agents = self._applied_state()
        if remove_absent:
            agents = {
                name: entry for name, entry in agents.items() if name in names
            }
        applied_at = datetime.now(timezone.utc).isoformat()
        applied_results = []
        for candidate in candidates:
            previous = agents.get(candidate.agent.name)
            previous_digest = (
                str(previous.get("artifact_digest"))
                if isinstance(previous, dict) and previous.get("artifact_digest")
                else None
            )
            if previous_digest == candidate.digest and isinstance(previous, dict):
                existing_receipts = previous.get("eval_receipts") or []
                existing_applied_at = str(previous.get("applied_at") or applied_at)
                applied_results.append(
                    AppliedAgent(
                        name=candidate.agent.name,
                        artifact_digest=candidate.digest,
                        eval_receipts=tuple(str(item) for item in existing_receipts),
                        previous_artifact_digest=(
                            str(previous.get("previous_artifact_digest"))
                            if previous.get("previous_artifact_digest")
                            else None
                        ),
                        applied_at=existing_applied_at,
                    )
                )
                continue
            agents[candidate.agent.name] = {
                "artifact_digest": candidate.digest,
                "eval_receipts": list(receipts[candidate.agent.name]),
                "previous_artifact_digest": previous_digest,
                "applied_at": applied_at,
            }
            applied_results.append(
                AppliedAgent(
                    name=candidate.agent.name,
                    artifact_digest=candidate.digest,
                    eval_receipts=receipts[candidate.agent.name],
                    previous_artifact_digest=previous_digest,
                    applied_at=applied_at,
                )
            )
        new_state = {
            "schema_version": 2,
            "agents": {name: agents[name] for name in sorted(agents)},
        }
        temporary = state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(new_state, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, state_path)
        return tuple(applied_results)

    def apply(self, candidate: CompiledArtifact) -> AppliedAgent:
        """Atomically mark one exact, evaluated artifact as active."""
        return self.apply_many((candidate,))[0]

    def ask(self, agent: str, question: str) -> QueryAnswer:
        from tabletalk.interfaces import QuerySession

        return QuerySession(str(self.root)).ask(agent, question)
