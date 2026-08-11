from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from click.testing import CliRunner

from tabletalk.cli import cli
from tabletalk.diagnostics import FailureCode, FailureOwner
from tabletalk.evals import (
    CaseResult,
    EvalCase,
    SuiteResult,
    suite_result_from_dict,
)
from tabletalk.metrics import BaselineRecord, observations
from tabletalk.project import Project
from tabletalk.reports import EvaluationReport
from tabletalk.schemas import content_digest
from tabletalk.traces import (
    Answer,
    DbtContext,
    Interpretation,
    ResultTrace,
    SQLTrace,
    Trace,
    Usage,
    Verification,
)


def trace() -> Trace:
    return Trace(
        question="What is revenue?",
        interpretation=Interpretation(intent="aggregate"),
        dbt_context=DbtContext(
            "manifest-one",
            None,
            ("model.analytics.orders",),
            ("revenue",),
        ),
        sql=SQLTrace("select revenue", "select revenue limit 1000", "duckdb"),
        result=ResultTrace(({"revenue": 100},), 1),
        answer=Answer("Revenue is 100.", ()),
        verification=(Verification("claims_supported", True),),
        agent="revenue",
        agent_digest="agent-one",
        model_identity="ollama:gemma4:31b-cloud",
        warehouse_identity="duckdb",
        usage=Usage(125, 100, 20, 0.001),
        inference_fingerprint="fingerprint-one",
        semantic_digest="semantic-one",
        prompt_version="tabletalk.prompt/v2",
        runtime_version="tabletalk.runtime/v1",
        created_at="2026-08-10T18:42:11Z",
    )


def test_report_formats_are_deterministic_and_ci_compatible(tmp_path: Path) -> None:
    case = EvalCase(
        "monthly",
        "What is revenue?",
        required_models=("model.analytics.expected",),
    )
    result = SuiteResult(
        "revenue-suite",
        "revenue",
        (
            CaseResult(
                "monthly",
                False,
                (Verification("required_models", False, "missing expected model"),),
                trace(),
            ),
        ),
        "suite-one",
    )
    report = EvaluationReport.create((result,), {"revenue-suite": (case,)})
    assert report.json_text() == report.json_text()
    assert report.markdown_text() == report.markdown_text()
    assert report.junit_text() == report.junit_text()
    payload = json.loads(report.json_text())
    assert payload["schema_version"] == "tabletalk.report/v1"
    assert payload["summary"]["pass_rate_interval"]
    assert payload["diagnoses"][0]["code"] == FailureCode.WRONG_MODEL_SELECTION.value
    assert payload["diagnoses"][0]["owner"] == FailureOwner.AGENT_DEFINITION.value
    junit = ET.fromstring(report.junit_text())
    assert junit.attrib == {
        "name": "tabletalk",
        "tests": "1",
        "failures": "1",
        "errors": "0",
    }
    assert junit.find("testcase/failure") is not None
    paths = report.write(tmp_path)
    assert paths.json.read_text() == report.json_text()
    assert paths.markdown.read_text() == report.markdown_text()
    assert paths.junit.read_text() == report.junit_text()


def test_report_marks_mixed_trials_as_flaky() -> None:
    case = EvalCase("monthly", "What is revenue?")
    passing = SuiteResult(
        "revenue-suite",
        "revenue",
        (CaseResult("monthly", True, (Verification("result_match", True),), trace()),),
        "suite-one",
        1,
        2,
    )
    failing = SuiteResult(
        "revenue-suite",
        "revenue",
        (
            CaseResult(
                "monthly",
                False,
                (Verification("result_match", False, "expected 100; got 90"),),
                trace(),
            ),
        ),
        "suite-one",
        2,
        2,
    )
    report = EvaluationReport.create(
        (passing, failing),
        {"revenue-suite": (case,)},
    )
    codes = {item.code for item in report.diagnoses}
    assert FailureCode.RESULT_MISMATCH in codes
    assert FailureCode.FLAKY_OR_INCONSISTENT_OUTCOME in codes


def test_report_failure_shows_baseline_and_candidate_values() -> None:
    case = EvalCase(
        "monthly",
        "What is revenue?",
        required_models=("model.analytics.expected",),
    )
    passing = SuiteResult(
        "revenue-suite",
        "revenue",
        (CaseResult("monthly", True, (Verification("required_models", True),), trace()),),
        "suite-one",
    )
    failing = SuiteResult(
        "revenue-suite",
        "revenue",
        (
            CaseResult(
                "monthly",
                False,
                (Verification("required_models", False, "missing expected model"),),
                trace(),
            ),
        ),
        "suite-one",
    )
    baseline = BaselineRecord(
        "revenue-v1",
        observations((passing,)),
        "test-user",
        "2026-08-10T18:42:11Z",
        content_digest(b"baseline report"),
    )
    report = EvaluationReport.create(
        (failing,),
        {"revenue-suite": (case,)},
        baseline,
    )
    diagnosis = report.diagnoses[0]
    assert diagnosis.baseline_value is True
    assert diagnosis.candidate_value is False
    assert "Baseline: `True`. Candidate: `False`." in report.markdown_text()


def test_eval_result_round_trip_keeps_every_trial() -> None:
    result = SuiteResult(
        "revenue-suite",
        "revenue",
        (CaseResult("monthly", True, (Verification("result_match", True),), trace()),),
        "suite-one",
        2,
        3,
    )
    loaded = suite_result_from_dict(result.to_dict())
    assert loaded == result


def test_metrics_recompute_from_saved_result_without_inference() -> None:
    case = EvalCase("monthly", "What is revenue?")
    original = SuiteResult(
        "revenue-suite",
        "revenue",
        (CaseResult("monthly", True, (Verification("result_match", True),), trace()),),
        "suite-one",
    )
    saved = json.loads(json.dumps(original.to_dict()))
    restored = suite_result_from_dict(saved)
    report = EvaluationReport.create(
        (restored,),
        {"revenue-suite": (case,)},
    )
    assert report.summary.passed == 1
    assert report.summary.prompt_tokens == 100
    assert report.identities()["inference_fingerprints"] == ["fingerprint-one"]


def test_failure_taxonomy_values_are_stable() -> None:
    assert {item.value for item in FailureCode} == {
        "unsupported_question",
        "ambiguity_not_detected",
        "insufficient_clarification",
        "wrong_model_selection",
        "wrong_column_selection",
        "incorrect_join",
        "incorrect_filter",
        "incorrect_time_boundary",
        "incorrect_aggregation_or_grain",
        "unsafe_or_out_of_scope_access",
        "sql_validation_failure",
        "execution_failure",
        "result_mismatch",
        "unsupported_claim",
        "missing_requested_output",
        "latency_budget_violation",
        "token_budget_violation",
        "cost_budget_violation",
        "flaky_or_inconsistent_outcome",
        "invalid_or_ambiguous_ground_truth",
    }


def test_cli_baseline_approval_binds_exact_report_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = EvalCase("monthly", "What is revenue?")
    result = SuiteResult(
        "revenue-suite",
        "revenue",
        (CaseResult("monthly", True, (Verification("result_match", True),), trace()),),
        "suite-one",
    )
    report = EvaluationReport.create((result,), {"revenue-suite": (case,)})
    report_path = report.write(tmp_path / "reports").json
    project = object.__new__(Project)
    project.root = tmp_path
    project.config = {}
    monkeypatch.setattr("tabletalk.cli._project", lambda path: project)
    digest = content_digest(report_path.read_bytes())
    runner = CliRunner()
    missing_digest = runner.invoke(
        cli,
        [
            "baseline",
            "approve",
            "revenue-v1",
            "--from",
            str(report_path),
            "--yes",
            "--project-folder",
            str(tmp_path),
        ],
    )
    assert missing_digest.exit_code == 4
    approved = runner.invoke(
        cli,
        [
            "baseline",
            "approve",
            "revenue-v1",
            "--from",
            str(report_path),
            "--yes",
            "--digest",
            digest,
            "--actor",
            "test-user",
            "--project-folder",
            str(tmp_path),
        ],
    )
    assert approved.exit_code == 0, approved.output
    baseline = BaselineRecord.load(tmp_path / "baselines" / "revenue-v1.json")
    assert baseline.source_digest == digest
    assert len(baseline.observations) == 1
