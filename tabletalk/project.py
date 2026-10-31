"""One project entry point for manifest, agents, live runs, and eval runs."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from tabletalk.agents import Agent, ResolvedAgent, load_agents
from tabletalk.connections import ReadOnlyConnection, Target, load_profile_target
from tabletalk.factories import get_llm_provider
from tabletalk.inference import CachePolicy, InferenceStore
from tabletalk.interfaces import LLMProvider
from tabletalk.manifest import Manifest
from tabletalk.receipts import RECEIPTS_DIRECTORY, Receipt, build_receipt, write_receipt
from tabletalk.runtime import Runtime
from tabletalk.schemas import PROJECT_SCHEMA, content_digest, require_schema_version
from tabletalk.semantics import SemanticKnowledge, SemanticStatement
from tabletalk.traces import Trace, Verification


class Project:
    def __init__(self, root: Path, config: dict[str, Any]) -> None:
        self.root = root
        self.config = config
        dbt = config.get("dbt")
        if not isinstance(dbt, dict):
            raise ValueError("tabletalk.yaml requires a dbt mapping")
        project_dir = Path(str(dbt.get("project_dir") or ".")).expanduser()
        self.dbt_project_dir = (
            project_dir.resolve() if project_dir.is_absolute() else (root / project_dir).resolve()
        )
        manifest_path = Path(str(dbt.get("manifest") or "target/manifest.json")).expanduser()
        if not manifest_path.is_absolute():
            manifest_path = self.dbt_project_dir / manifest_path

        def optional_artifact(name: str) -> Path | None:
            configured = dbt.get(name)
            if not configured:
                return None
            path = Path(str(configured)).expanduser()
            return path if path.is_absolute() else self.dbt_project_dir / path

        self.manifest = Manifest.load(
            manifest_path,
            catalog_path=optional_artifact("catalog"),
            run_results_path=optional_artifact("run_results"),
            sources_path=optional_artifact("sources"),
        )

    @classmethod
    def load(cls, path: str | Path = ".") -> Project:
        root = Path(path).expanduser().resolve()
        config_file = root / "tabletalk.yaml"
        nested_config = root / "tabletalk" / "tabletalk.yaml"
        if not config_file.is_file() and nested_config.is_file():
            root = nested_config.parent
            config_file = nested_config
        if not config_file.is_file():
            raise ValueError(
                f"tabletalk.yaml not found in {root} or {root / 'tabletalk'}. Run 'tabletalk init'."
            )
        try:
            config = yaml.safe_load(config_file.read_text())
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(f"Could not load {config_file}: {exc}") from exc
        if not isinstance(config, dict):
            raise ValueError("tabletalk.yaml must be a mapping")
        require_schema_version(config, PROJECT_SCHEMA, resource=str(config_file))
        if "connections" in config or "state" in config:
            raise ValueError(
                "tabletalk.yaml uses removed connection/applied-state configuration. "
                "Run 'tabletalk init' "
                "to use a dbt profile target directly."
            )
        return cls(root, config)

    @property
    def agents_directory(self) -> Path:
        return self.root / str(self.config.get("agents_dir") or "agents")

    @property
    def evals_directory(self) -> Path:
        return self.root / str(self.config.get("evals_dir") or "evals")

    @property
    def proposals_directory(self) -> Path:
        return self.root / ".tabletalk" / "proposals"

    def proposal_path(self, proposal_id: str) -> Path:
        return self.proposals_directory / f"{proposal_id}.json"

    def agents(self) -> tuple[Agent, ...]:
        return load_agents(self.agents_directory)

    def agent(self, name: str) -> Agent:
        matches = [agent for agent in self.agents() if agent.name == name]
        if not matches:
            raise ValueError(f"Agent '{name}' was not found in {self.agents_directory}")
        if len(matches) > 1:
            raise ValueError(f"Agent name '{name}' is duplicated")
        return matches[0]

    def resolve_agent(self, name: str) -> ResolvedAgent:
        return self.agent(name).resolve(self.manifest)

    def target(self) -> Target:
        dbt = self.config["dbt"]
        profiles_dir = dbt.get("profiles_dir")
        if profiles_dir:
            profiles_path = Path(str(profiles_dir)).expanduser()
            if not profiles_path.is_absolute():
                profiles_path = self.root / profiles_path
        else:
            profiles_path = None
        return load_profile_target(self.dbt_project_dir, dbt.get("target"), profiles_path)

    def connection(self) -> ReadOnlyConnection:
        return ReadOnlyConnection(self.target())

    def _semantic_context(
        self,
        agent: Agent,
    ) -> tuple[str | None, tuple[SemanticStatement, ...]]:
        values = []
        statements: list[SemanticStatement] = []
        for reference in agent.knowledge:
            path = (self.root / reference).resolve()
            try:
                path.relative_to(self.root)
            except ValueError as exc:
                raise ValueError(f"Agent knowledge path leaves the project: {reference}") from exc
            if not path.is_file():
                raise ValueError(f"Agent knowledge file was not found: {reference}")
            values.append({"path": reference, "sha256": content_digest(path.read_bytes())})
            statements.extend(SemanticKnowledge.load(path).statements)
        return (content_digest(values) if values else None, tuple(statements))

    def runtime(
        self,
        agent_name: str,
        *,
        cache_policy: CachePolicy | None = None,
    ) -> Runtime:
        llm_config = self.config.get("llm")
        if not isinstance(llm_config, dict):
            raise ValueError("tabletalk.yaml requires an llm mapping")
        model = str(llm_config.get("model") or "unknown")
        provider = str(llm_config.get("provider") or "unknown")
        agent = self.agent(agent_name)
        inference_config = self._inference_config()
        selected_policy = cache_policy or CachePolicy(
            str(inference_config.get("cache_policy") or CachePolicy.DISABLED.value)
        )
        semantic_digest, semantic_statements = self._semantic_context(agent)
        return Runtime(
            self.manifest,
            agent.resolve(self.manifest),
            self.connection(),
            get_llm_provider(llm_config),
            model_identity=f"{provider}:{model}",
            inference_store=InferenceStore(self._inference_directory(inference_config)),
            cache_policy=selected_policy,
            model_config=dict(llm_config),
            semantic_digest=semantic_digest,
            persist_prompts=bool(inference_config.get("persist_prompts", False)),
            semantic_statements=semantic_statements,
        )

    def _inference_config(self) -> dict[str, Any]:
        inference_config = self.config.get("inference") or {}
        if not isinstance(inference_config, dict):
            raise ValueError("tabletalk.yaml inference must be a mapping")
        return inference_config

    def receipt(self, trace: Trace) -> Receipt:
        """Build the answer receipt for a trace and store it under .tabletalk/receipts."""
        store = InferenceStore(self._inference_directory(self._inference_config()))
        fingerprint = trace.inference_fingerprint
        receipt = build_receipt(
            trace,
            self.manifest,
            recorded=bool(fingerprint) and store.contains(str(fingerprint)),
            inherited_descriptions=self.resolve_agent(trace.agent).inherited_column_descriptions,
        )
        write_receipt(self.root / RECEIPTS_DIRECTORY, receipt)
        return receipt

    def _inference_directory(self, inference_config: dict[str, Any]) -> Path:
        """Return the inference store directory; ``inference.store`` may commit it to git."""
        configured = inference_config.get("store")
        if not configured:
            return self.root / ".tabletalk" / "inference"
        path = (self.root / str(configured)).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"inference.store must stay inside the project: {configured}")
        return path

    def llm_provider(self) -> LLMProvider:
        llm_config = self.config.get("llm")
        if not isinstance(llm_config, dict):
            raise ValueError("tabletalk.yaml requires an llm mapping")
        return get_llm_provider(llm_config)

    def answer(
        self, agent_name: str, question: str, *, cache_policy: CachePolicy | None = None
    ) -> Trace:
        runtime = self.runtime(agent_name, cache_policy=cache_policy)
        trace = runtime.answer(question)
        checks: list[Verification] = []
        matched_digest: str | None = None
        normalized_question = " ".join(question.split()).casefold()

        from tabletalk.evals import EvalRunner, load_eval_suite

        paths = sorted((*self.evals_directory.glob("*.yaml"), *self.evals_directory.glob("*.yml")))
        for path in paths:
            suite = load_eval_suite(path)
            if suite.agent != agent_name:
                continue
            for case in suite.cases:
                if case.expected_outcome != "answer" or not case.verifies_result:
                    continue
                if " ".join(case.question.split()).casefold() != normalized_question:
                    continue
                result = EvalRunner(suite, runtime).evaluate_trace(case, trace)
                failures = [
                    f"{check.name}: {check.message or 'failed'}"
                    for check in result.checks
                    if not check.passed
                ]
                checks.append(
                    Verification(
                        f"correctness_eval:{suite.name}/{case.name}",
                        result.passed,
                        ". ".join(failures) if failures else "Approved eval matched",
                    )
                )
                matched_digest = suite.digest
        if not checks:
            checks.append(
                Verification(
                    "correctness_eval_coverage",
                    False,
                    "No approved eval case exactly matches this question",
                )
            )
        trace = replace(
            trace,
            verification=trace.verification + tuple(checks),
            eval_suite_digest=matched_digest,
        )
        trace.write(self.root / ".tabletalk" / "runs")
        return trace

    ask = answer
