from __future__ import annotations

from pathlib import Path

import pytest

from tabletalk.evals import CaseResult, SuiteResult, suite_result_from_dict
from tabletalk.metrics import (
    BaselineRecord,
    TrialObservation,
    compare_paired,
    observations,
    summarize,
    wilson_interval,
)
from tabletalk.schemas import SchemaVersionError, content_digest
from tabletalk.traces import Usage, Verification


def observation(
    trial: int,
    passed: bool,
    latency_ms: float,
    *,
    suite_digest: str = "suite-one",
) -> TrialObservation:
    return TrialObservation(
        "revenue",
        suite_digest,
        "monthly-revenue",
        trial,
        passed,
        f"fingerprint-{trial}",
        latency_ms,
        100,
        20,
        0.001,
    )


def test_summary_reports_wilson_uncertainty_and_small_sample_warning() -> None:
    values = tuple(observation(index + 1, index < 5, 100 + index) for index in range(10))
    summary = summarize(values)
    assert summary.trials == 10
    assert summary.passed == 5
    assert summary.pass_rate == 0.5
    assert summary.pass_rate_interval == pytest.approx((0.236593, 0.763407), abs=1e-6)
    assert summary.latency_p95_ms == 109
    assert summary.prompt_tokens == 1000
    assert summary.completion_tokens == 200
    assert summary.estimated_cost == pytest.approx(0.01)
    assert "at least 20" in summary.warnings[0]
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_non_answer_trial_preserves_usage_and_fingerprint() -> None:
    result = SuiteResult(
        "safety",
        "revenue",
        (
            CaseResult(
                "refuse-sensitive",
                True,
                (Verification("expected_outcome", True),),
                error="Question rejected",
                usage=Usage(125, 80, 12, 0.002, 1, ("retry",)),
                inference_fingerprint="fingerprint-refusal",
            ),
        ),
        "suite-one",
    )
    restored = suite_result_from_dict(result.to_dict())
    assert restored == result
    value = observations((restored,))[0]
    assert value.latency_ms == 125
    assert value.prompt_tokens == 80
    assert value.completion_tokens == 12
    assert value.estimated_cost == 0.002
    assert value.inference_fingerprint == "fingerprint-refusal"


def test_paired_comparison_uses_mcnemar_and_wilcoxon() -> None:
    baseline = tuple(
        observation(index + 1, index >= 10, 200 + index) for index in range(20)
    )
    candidate = tuple(observation(index + 1, True, 190 + index) for index in range(20))
    comparison = compare_paired(baseline, candidate)
    assert comparison.pairs == 20
    assert comparison.baseline_pass_rate == 0.5
    assert comparison.candidate_pass_rate == 1.0
    assert comparison.pass_rate_effect == 0.5
    assert comparison.mcnemar_p_value == pytest.approx(0.001953125)
    assert comparison.latency_effect_ms == -10
    assert comparison.wilcoxon_p_value is not None
    assert comparison.wilcoxon_p_value < 0.001
    assert not comparison.warnings


def test_paired_comparison_warns_about_small_samples_and_changed_evals() -> None:
    baseline = (observation(1, True, 100),)
    candidate = (observation(1, False, 110, suite_digest="suite-two"),)
    comparison = compare_paired(baseline, candidate)
    assert len(comparison.warnings) == 2
    assert "at least 20" in comparison.warnings[0]
    assert "eval definition changed" in comparison.warnings[1]


def test_baseline_is_versioned_content_addressed_and_immutable(tmp_path: Path) -> None:
    baseline = BaselineRecord(
        "revenue-v1",
        (observation(1, True, 100),),
        "test-user",
        "2026-08-10T18:42:11Z",
        content_digest(b"report"),
    )
    path = baseline.write(tmp_path / "revenue-v1.json")
    assert BaselineRecord.load(path) == baseline
    path.write_text(path.read_text().replace('"latency_ms": 100', '"latency_ms": 101'))
    with pytest.raises(ValueError, match="content digest"):
        BaselineRecord.load(path)


def test_stale_baseline_has_a_regeneration_message(tmp_path: Path) -> None:
    path = tmp_path / "stale.json"
    path.write_text(
        '{"schema_version":"tabletalk.baseline/v0","observations":[]}\n'
    )
    with pytest.raises(SchemaVersionError, match="immutable artifact cannot migrate"):
        BaselineRecord.load(path)


def test_paired_comparison_rejects_duplicate_trial_keys() -> None:
    duplicate = observation(1, True, 10)
    with pytest.raises(ValueError, match="duplicate paired observation keys"):
        compare_paired((duplicate, duplicate), (duplicate,))


def test_pass_rate_effect_interval_stays_in_probability_bounds() -> None:
    baseline = tuple(observation(index, False, 10) for index in (1, 2))
    candidate = tuple(observation(index, True, 10) for index in (1, 2))
    comparison = compare_paired(baseline, candidate)
    assert comparison.pass_rate_effect_interval == (1.0, 1.0)
