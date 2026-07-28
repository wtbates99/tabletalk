"""Artifact-linked eval receipts used by the apply gate."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tabletalk.domain import RuntimeIdentity, canonical_digest, canonical_json
from tabletalk.evals.models import EvalSuite, SuiteResult


@dataclass(frozen=True)
class EvalReceiptBody:
    format_version: str
    suite_name: str
    suite_digest: str
    artifact_digests: tuple[tuple[str, str], ...]
    passed: bool
    score: float
    started_at: str
    completed_at: str
    runtime: RuntimeIdentity
    result_run_id: str


@dataclass(frozen=True)
class EvalReceipt:
    digest: str
    receipt: EvalReceiptBody

    def to_json(self) -> str:
        return canonical_json(self)


def _artifact_digests(suite: EvalSuite, project_folder: str | Path) -> tuple[tuple[str, str], ...]:
    manifest_dir = Path(project_folder) / "manifest"
    values: dict[str, str] = {}
    for case in suite.cases:
        if not case.manifest:
            continue
        name = Path(case.manifest).stem
        artifact_path = manifest_dir / f"{name}.agent.json"
        if not artifact_path.is_file():
            continue
        payload = json.loads(artifact_path.read_text())
        digest = payload.get("digest")
        if isinstance(digest, str):
            values[name] = digest
    return tuple(sorted(values.items()))


def create_eval_receipt(
    result: SuiteResult,
    suite: EvalSuite,
    project_folder: str | Path,
) -> EvalReceipt:
    suite_digest = hashlib.sha256(suite.source_path.read_bytes()).hexdigest()
    evaluated_agent = result.metadata.get("agent")
    evaluated_digest = result.metadata.get("artifact_digest")
    artifact_digests = (
        ((str(evaluated_agent), str(evaluated_digest)),)
        if evaluated_agent and evaluated_digest
        else _artifact_digests(suite, project_folder)
    )
    body = EvalReceiptBody(
        format_version="1",
        suite_name=result.suite_name,
        suite_digest=suite_digest,
        artifact_digests=artifact_digests,
        passed=result.passed,
        score=result.score,
        started_at=result.started_at,
        completed_at=result.completed_at,
        runtime=RuntimeIdentity(
            provider=str(result.metadata.get("llm_provider") or "unknown"),
            model=str(result.metadata.get("model") or "unknown"),
        ),
        result_run_id=result.run_id,
    )
    return EvalReceipt(digest=canonical_digest(body), receipt=body)


def write_eval_receipt(receipt: EvalReceipt, project_folder: str | Path) -> Path:
    directory = Path(project_folder) / ".tabletalk" / "eval-receipts"
    directory.mkdir(parents=True, exist_ok=True)
    safe_suite = "".join(
        character if character.isalnum() or character in {"-", "_"} else "-"
        for character in receipt.receipt.suite_name
    )
    path = directory / f"{safe_suite}-{receipt.digest}.json"
    path.write_text(receipt.to_json() + "\n")
    return path


def matching_eval_receipt(
    project_folder: str | Path,
    suite_name: str,
    artifact_name: str,
    artifact_digest: str,
    suite_digest: str | None = None,
) -> dict[str, Any] | None:
    directory = Path(project_folder) / ".tabletalk" / "eval-receipts"
    if not directory.is_dir():
        return None
    for path in sorted(directory.glob("*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text())
            receipt = payload["receipt"]
            artifacts = dict(receipt["artifact_digests"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if payload.get("digest") != canonical_digest(receipt):
            continue
        if (
            receipt.get("suite_name") == suite_name
            and receipt.get("passed") is True
            and artifacts.get(artifact_name) == artifact_digest
            and (
                suite_digest is None
                or receipt.get("suite_digest") == suite_digest
            )
        ):
            return payload
    return None
