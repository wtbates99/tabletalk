"""Deterministic JSON, Markdown, and JUnit reports for eval runs."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from tabletalk.diagnostics import FailureDiagnosis, diagnose_results
from tabletalk.evals import EvalCase, SuiteResult
from tabletalk.metrics import (
    BaselineRecord,
    MetricSummary,
    PairedComparison,
    compare_paired,
    observations,
    summarize,
)
from tabletalk.schemas import REPORT_SCHEMA, content_digest


@dataclass(frozen=True)
class ReportPaths:
    directory: Path
    json: Path
    markdown: Path
    junit: Path


@dataclass(frozen=True)
class EvaluationReport:
    results: tuple[SuiteResult, ...]
    summary: MetricSummary
    diagnoses: tuple[FailureDiagnosis, ...]
    comparison: PairedComparison | None = None
    baseline_name: str | None = None

    @classmethod
    def create(
        cls,
        results: tuple[SuiteResult, ...],
        suites: dict[str, tuple[EvalCase, ...]],
        baseline: BaselineRecord | None = None,
    ) -> EvaluationReport:
        current = observations(results)
        diagnoses = diagnose_results(suites, results)
        if baseline:
            baseline_by_key = {item.pair_key: item for item in baseline.observations}
            candidate_by_key = {item.pair_key: item for item in current}
            diagnoses = tuple(
                replace(
                    item,
                    baseline_value=baseline_by_key[key].passed,
                    candidate_value=candidate_by_key[key].passed,
                )
                if (key := (item.suite, item.case, item.trial)) in baseline_by_key
                and key in candidate_by_key
                else item
                for item in diagnoses
            )
        return cls(
            results,
            summarize(current),
            diagnoses,
            compare_paired(baseline.observations, current) if baseline else None,
            baseline.name if baseline else None,
        )

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.results)

    def identities(self) -> dict[str, list[str]]:
        traces = tuple(
            case.trace
            for result in self.results
            for case in result.cases
            if case.trace is not None
        )
        values = {
            "models": {trace.model_identity for trace in traces},
            "agents": {trace.agent for trace in traces},
            "agent_digests": {trace.agent_digest for trace in traces},
            "manifests": {trace.dbt_context.manifest_digest for trace in traces},
            "semantic_knowledge": {
                trace.semantic_digest for trace in traces if trace.semantic_digest
            },
            "prompts": {trace.prompt_version for trace in traces if trace.prompt_version},
            "runtimes": {trace.runtime_version for trace in traces if trace.runtime_version},
            "warehouses": {trace.warehouse_identity for trace in traces},
            "inference_fingerprints": {
                trace.inference_fingerprint
                for trace in traces
                if trace.inference_fingerprint
            },
        }
        return {key: sorted(value) for key, value in values.items()}

    def content_payload(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema_version": REPORT_SCHEMA,
            "passed": self.passed,
            "summary": self.summary.to_dict(),
            "identities": self.identities(),
            "diagnoses": [item.to_dict() for item in self.diagnoses],
            "results": [result.to_dict() for result in self.results],
        }
        if self.comparison:
            value["baseline"] = self.baseline_name
            value["comparison"] = self.comparison.to_dict()
        return value

    @property
    def digest(self) -> str:
        return content_digest(self.content_payload())

    @property
    def id(self) -> str:
        return "rpt_" + self.digest.removeprefix("sha256:")[:16]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "digest": self.digest, **self.content_payload()}

    def json_text(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    def markdown_text(self) -> str:
        interval = self.summary.pass_rate_interval
        lines = [
            "# TableTalk evaluation report",
            "",
            f"Result: **{'PASS' if self.passed else 'FAIL'}**",
            "",
            "| Measure | Value |",
            "|---|---:|",
            f"| Eval trials | {self.summary.trials} |",
            f"| Passed | {self.summary.passed} |",
            f"| Pass rate | {self.summary.pass_rate:.1%} |",
            f"| 95% confidence interval | {interval[0]:.1%} to {interval[1]:.1%} |",
            f"| Mean latency | {_number(self.summary.latency_mean_ms, ' ms')} |",
            f"| 95th percentile latency | {_number(self.summary.latency_p95_ms, ' ms')} |",
            f"| Prompt tokens | {self.summary.prompt_tokens} |",
            f"| Completion tokens | {self.summary.completion_tokens} |",
            f"| Estimated cost | {_money(self.summary.estimated_cost)} |",
        ]
        if self.comparison:
            comparison = self.comparison
            lines.extend(
                [
                    "",
                    f"## Paired comparison with `{self.baseline_name}`",
                    "",
                    "| Measure | Value |",
                    "|---|---:|",
                    f"| Paired observations | {comparison.pairs} |",
                    f"| Baseline pass rate | {comparison.baseline_pass_rate:.1%} |",
                    f"| Candidate pass rate | {comparison.candidate_pass_rate:.1%} |",
                    f"| Pass-rate effect | {comparison.pass_rate_effect:+.1%} |",
                    f"| McNemar p-value | {comparison.mcnemar_p_value:.4g} |",
                    f"| Median latency effect | {_number(comparison.latency_effect_ms, ' ms')} |",
                    f"| Wilcoxon p-value | {_number(comparison.wilcoxon_p_value)} |",
                ]
            )
        warnings = (*self.summary.warnings, *(self.comparison.warnings if self.comparison else ()))
        if warnings:
            lines.extend(("", "## Warnings", ""))
            lines.extend(f"- {item}" for item in warnings)
        lines.extend(("", "## Failures", ""))
        if not self.diagnoses:
            lines.append("No failures.")
        else:
            lines.extend(
                f"- `{item.code.value}` in `{item.suite}/{item.case}`. "
                f"Owner: `{item.owner.value}`. {item.corrective_action}"
                + (
                    f" Baseline: `{item.baseline_value}`. "
                    f"Candidate: `{item.candidate_value}`."
                    if item.baseline_value is not None
                    and item.candidate_value is not None
                    else ""
                )
                for item in self.diagnoses
            )
        lines.extend(("", "## Reproduction identities", ""))
        for name, values in self.identities().items():
            lines.append(f"- {name}: {', '.join(f'`{item}`' for item in values) or 'not recorded'}")
        return "\n".join(lines) + "\n"

    def junit_text(self) -> str:
        cases = tuple((result, case) for result in self.results for case in result.cases)
        root = ET.Element(
            "testsuite",
            {
                "name": "tabletalk",
                "tests": str(len(cases)),
                "failures": str(sum(not case.passed for _, case in cases)),
                "errors": "0",
            },
        )
        diagnosis_map: dict[tuple[str, str, int], list[FailureDiagnosis]] = {}
        for diagnosis in self.diagnoses:
            diagnosis_map.setdefault(
                (diagnosis.suite, diagnosis.case, diagnosis.trial), []
            ).append(diagnosis)
        for result, case in cases:
            element = ET.SubElement(
                root,
                "testcase",
                {
                    "classname": result.name,
                    "name": f"{case.name}[trial={result.trial}]",
                    "time": f"{(case.trace.usage.latency_ms if case.trace else 0) / 1000:.6f}",
                },
            )
            if not case.passed:
                diagnoses = diagnosis_map.get((result.name, case.name, result.trial), [])
                failure = ET.SubElement(
                    element,
                    "failure",
                    {
                        "type": diagnoses[0].code.value if diagnoses else "eval_failure",
                        "message": diagnoses[0].corrective_action if diagnoses else "Eval failed",
                    },
                )
                failure.text = "\n".join(
                    evidence for item in diagnoses for evidence in item.evidence
                ) or (case.error or "One or more hard checks failed.")
            output = ET.SubElement(element, "system-out")
            output.text = json.dumps(
                {
                    "inference_fingerprint": (
                        case.trace.inference_fingerprint if case.trace else None
                    ),
                    "checks": [vars(check) for check in case.checks],
                },
                sort_keys=True,
            )
        ET.indent(root, space="  ")
        return ET.tostring(root, encoding="unicode", xml_declaration=True) + "\n"

    def write(self, directory: str | Path) -> ReportPaths:
        root = Path(directory) / self.id
        root.mkdir(parents=True, exist_ok=True)
        json_path = root / "report.json"
        markdown_path = root / "report.md"
        junit_path = root / "junit.xml"
        values = {
            json_path: self.json_text(),
            markdown_path: self.markdown_text(),
            junit_path: self.junit_text(),
        }
        for path, rendered in values.items():
            if path.exists() and path.read_text() != rendered:
                raise ValueError(f"Report path has different content: {path}")
            path.write_text(rendered)
        return ReportPaths(root, json_path, markdown_path, junit_path)


def _number(value: float | None, suffix: str = "") -> str:
    return f"{value:.3f}{suffix}" if value is not None else "not recorded"


def _money(value: float | None) -> str:
    return f"${value:.6f}" if value is not None else "not recorded"
