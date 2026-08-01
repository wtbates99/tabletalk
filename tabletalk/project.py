"""One project entry point for manifest, agents, live runs, and eval runs."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from tabletalk.agents import Agent, ResolvedAgent, load_agents
from tabletalk.connections import ReadOnlyConnection, load_profile_target
from tabletalk.factories import get_llm_provider
from tabletalk.manifest import Manifest
from tabletalk.runtime import Runtime
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
                f"tabletalk.yaml not found in {root} or {root / 'tabletalk'}; run 'tabletalk init'"
            )
        try:
            config = yaml.safe_load(config_file.read_text())
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(f"Could not load {config_file}: {exc}") from exc
        if not isinstance(config, dict):
            raise ValueError("tabletalk.yaml must be a mapping")
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

    def target(self):
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

    def runtime(self, agent_name: str) -> Runtime:
        llm_config = self.config.get("llm")
        if not isinstance(llm_config, dict):
            raise ValueError("tabletalk.yaml requires an llm mapping")
        model = str(llm_config.get("model") or "unknown")
        provider = str(llm_config.get("provider") or "unknown")
        return Runtime(
            self.manifest,
            self.resolve_agent(agent_name),
            self.connection(),
            get_llm_provider(llm_config),
            model_identity=f"{provider}:{model}",
        )

    def answer(self, agent_name: str, question: str) -> Trace:
        runtime = self.runtime(agent_name)
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
                        "; ".join(failures) if failures else "Approved eval matched",
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
