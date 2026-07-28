"""First-class declarative Agent resource loading and normalization."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from tabletalk.compiler import CompiledArtifact, compile_agent
from tabletalk.domain import ErrorCode, RuntimeStage, TableTalkError


def _text(value: Any, field: str, *, required: bool = False) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise TableTalkError(
            ErrorCode.CONFIG_INVALID,
            RuntimeStage.CONFIGURATION,
            f"{field} must be {'a non-empty ' if required else 'a '}string.",
        )
    return " ".join(value.split())


def _string_list(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise TableTalkError(
            ErrorCode.CONFIG_INVALID,
            RuntimeStage.CONFIGURATION,
            f"{field} must be a list of non-empty strings.",
        )
    return tuple(value)


@dataclass(frozen=True)
class RelationSelection:
    include: tuple[str, ...]
    exclude: tuple[str, ...] = ()

    def resolve(self, available: tuple[str, ...]) -> tuple[str, ...]:
        normalized = {name.lower(): name for name in available}
        selected: set[str] = set()
        for pattern in self.include:
            matches = [
                original
                for lowered, original in normalized.items()
                if fnmatch.fnmatchcase(lowered, pattern.lower())
            ]
            if not matches:
                raise TableTalkError(
                    ErrorCode.CONFIG_INVALID,
                    RuntimeStage.COMPILATION,
                    f"Agent relation pattern '{pattern}' matched no relations.",
                    details={"pattern": pattern},
                )
            selected.update(matches)
        for pattern in self.exclude:
            selected = {
                name
                for name in selected
                if not fnmatch.fnmatchcase(name.lower(), pattern.lower())
            }
        if not selected:
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.COMPILATION,
                "Agent relation selection resolved to an empty scope.",
            )
        return tuple(sorted(selected))


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    description: str
    connection: str
    relations: RelationSelection
    semantics: dict[str, Any]
    policies: dict[str, Any]
    evals: tuple[str, ...]
    version: str = "1"
    owner: str | None = None
    sample_questions: tuple[str, ...] = ()

    @classmethod
    def load(cls, path: str | Path) -> AgentDefinition:
        source = Path(path)
        try:
            payload = yaml.safe_load(source.read_text())
        except (OSError, yaml.YAMLError) as error:
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                f"Agent definition '{source.name}' could not be loaded.",
            ) from error
        if not isinstance(payload, dict):
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                f"Agent definition '{source.name}' must be a mapping.",
            )
        if payload.get("kind") != "Agent":
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                f"Agent definition '{source.name}' requires kind: Agent.",
            )
        relations = payload.get("relations")
        if not isinstance(relations, dict):
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                "relations must contain explicit include and optional exclude lists.",
            )
        include = _string_list(relations.get("include"), "relations.include")
        if not include:
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                "relations.include must declare at least one relation or pattern.",
            )
        semantics = payload.get("semantics") or {}
        policies = payload.get("policies") or {}
        if not isinstance(semantics, dict) or not isinstance(policies, dict):
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                "semantics and policies must be mappings.",
            )
        if policies.get("read_only") is False:
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                "Agent policy read_only cannot be disabled.",
            )
        if policies.get("require_evidence") is False:
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                "Agent policy require_evidence cannot be disabled.",
            )
        for field, upper in (("max_rows", 10_000), ("timeout_seconds", 3600)):
            value = policies.get(field)
            if value is not None and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 1 <= value <= upper
            ):
                raise TableTalkError(
                    ErrorCode.CONFIG_INVALID,
                    RuntimeStage.CONFIGURATION,
                    f"policies.{field} must be an integer between 1 and {upper}.",
                )
        name = _text(payload.get("name"), "name", required=True)
        if re.fullmatch(r"[a-z][a-z0-9_-]*", name) is None:
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                "Agent name must use lowercase letters, numbers, underscores, or hyphens.",
            )
        return cls(
            name=name,
            description=_text(payload.get("description"), "description", required=True),
            connection=_text(payload.get("connection"), "connection", required=True),
            relations=RelationSelection(
                include=include,
                exclude=_string_list(relations.get("exclude"), "relations.exclude"),
            ),
            semantics=semantics,
            policies={"read_only": True, "require_evidence": True, **policies},
            evals=_string_list(payload.get("evals"), "evals"),
            version=_text(payload.get("version") or "1", "version", required=True),
            owner=_text(payload.get("owner"), "owner") or None,
            sample_questions=_string_list(
                payload.get("sample_questions"), "sample_questions"
            ),
        )

    def compile(
        self,
        schema_snapshot: dict[str, Any],
        *,
        connection_type: str,
        dialect: str,
    ) -> CompiledArtifact:
        resolved = self.relations.resolve(tuple(schema_snapshot))
        datasets: dict[str, list[str]] = {}
        for relation in resolved:
            if "." not in relation:
                raise TableTalkError(
                    ErrorCode.CONFIG_INVALID,
                    RuntimeStage.COMPILATION,
                    f"Resolved relation '{relation}' must be schema-qualified.",
                )
            schema, table = relation.rsplit(".", 1)
            datasets.setdefault(schema, []).append(table)

        raw_metrics = self.semantics.get("metrics") or {}
        if not isinstance(raw_metrics, dict):
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.COMPILATION,
                "semantics.metrics must be a mapping keyed by metric name.",
            )
        metrics = []
        for name, raw in raw_metrics.items():
            if not isinstance(raw, dict):
                raise TableTalkError(
                    ErrorCode.CONFIG_INVALID,
                    RuntimeStage.COMPILATION,
                    f"Metric '{name}' must be a mapping.",
                )
            metrics.append({"name": str(name), **raw})

        time_semantics = self.semantics.get("time") or {}
        rules = self.semantics.get("rules") or []
        relationships = self.semantics.get("relationships") or []
        if not isinstance(time_semantics, dict):
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.COMPILATION,
                "semantics.time must be a mapping.",
            )
        if not isinstance(rules, list) or not all(
            isinstance(rule, str) and rule.strip() for rule in rules
        ):
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.COMPILATION,
                "semantics.rules must be a list of non-empty strings.",
            )
        if not isinstance(relationships, list):
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.COMPILATION,
                "semantics.relationships must be a list.",
            )
        context = {
            "name": self.name,
            "description": self.description,
            "datasets": [
                {"name": schema, "tables": sorted(tables)}
                for schema, tables in sorted(datasets.items())
            ],
            "metrics": metrics,
            "time_semantics": time_semantics,
            "rules": rules,
            "relationships": relationships,
            "policies": self.policies,
            "required_evals": self.evals,
        }
        agent = {
            "kind": "Agent",
            "name": self.name,
            "description": self.description,
            "connection": self.connection,
            "version": self.version,
            "owner": self.owner,
            "sample_questions": self.sample_questions,
            "relations": {
                "include": self.relations.include,
                "exclude": self.relations.exclude,
            },
            "semantics": self.semantics,
            "policies": self.policies,
            "required_evals": self.evals,
        }
        scoped_snapshot = {name: schema_snapshot[name] for name in resolved}
        return compile_agent(
            agent,
            context,
            scoped_snapshot,
            connection_type=connection_type,
            dialect=dialect,
            resource_kind="Agent",
        )
