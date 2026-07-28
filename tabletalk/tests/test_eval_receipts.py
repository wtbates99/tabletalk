from __future__ import annotations

import json
from pathlib import Path

from tabletalk.evals import (
    create_eval_receipt,
    load_eval_suite,
    matching_eval_receipt,
    write_eval_receipt,
)
from tabletalk.evals.models import SuiteResult


def _suite(path: Path):
    path.write_text(
        """\
kind: EvalSuite
name: customer-regression
agent: customers
cases:
  - name: customer-count
    question: Count customers
    expect:
      result:
        comparison: scalar
        value: 5
"""
    )
    return load_eval_suite(path)


def _result(suite_name: str, *, passed: bool, digest: str) -> SuiteResult:
    return SuiteResult(
        run_id="run-1",
        suite_name=suite_name,
        started_at="2026-07-27T12:00:00+00:00",
        completed_at="2026-07-27T12:00:01+00:00",
        cases=[],
        score=1.0 if passed else 0.0,
        passed=passed,
        metadata={
            "llm_provider": "ollama",
            "model": "gemma4:31b-cloud",
            "agent": "customers",
            "artifact_digest": digest,
        },
    )


def test_receipt_matches_only_exact_candidate_and_suite(
    tmp_path: Path,
) -> None:
    suite = _suite(tmp_path / "suite.yaml")
    artifact_digest = "a" * 64
    receipt = create_eval_receipt(
        _result(suite.name, passed=True, digest=artifact_digest),
        suite,
        tmp_path,
    )
    write_eval_receipt(receipt, tmp_path)

    assert matching_eval_receipt(
        tmp_path,
        suite.name,
        "customers",
        artifact_digest,
        receipt.receipt.suite_digest,
    )
    assert not matching_eval_receipt(
        tmp_path,
        suite.name,
        "customers",
        "different",
        receipt.receipt.suite_digest,
    )
    assert not matching_eval_receipt(
        tmp_path,
        suite.name,
        "customers",
        artifact_digest,
        "changed-suite",
    )


def test_failed_or_tampered_receipt_never_authorizes_apply(
    tmp_path: Path,
) -> None:
    suite = _suite(tmp_path / "suite.yaml")
    artifact_digest = "a" * 64
    failed = create_eval_receipt(
        _result(suite.name, passed=False, digest=artifact_digest),
        suite,
        tmp_path,
    )
    failed_path = write_eval_receipt(failed, tmp_path)
    assert not matching_eval_receipt(
        tmp_path,
        suite.name,
        "customers",
        artifact_digest,
        failed.receipt.suite_digest,
    )

    passed = create_eval_receipt(
        _result(suite.name, passed=True, digest=artifact_digest),
        suite,
        tmp_path,
    )
    path = write_eval_receipt(passed, tmp_path)
    payload = json.loads(path.read_text())
    payload["receipt"]["artifact_digests"][0][1] = "tampered"
    path.write_text(json.dumps(payload))
    failed_path.unlink()

    assert not matching_eval_receipt(
        tmp_path,
        suite.name,
        "customers",
        "tampered",
        passed.receipt.suite_digest,
    )
