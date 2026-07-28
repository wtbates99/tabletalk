"""Stable public domain objects for trustworthy data-agent answers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    VERIFIED_WITH_WARNINGS = "verified_with_warnings"
    AMBIGUOUS = "ambiguous"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True)
class RuntimeIdentity:
    """Exact non-secret model identity used for an invocation."""

    provider: str
    model: str
    base_url: str | None = None


@dataclass(frozen=True)
class SourceReference:
    relation: str
    columns: tuple[str, ...] = ()
    row_indices: tuple[int, ...] = ()


@dataclass(frozen=True)
class CalculationStep:
    calculation_id: str
    label: str
    formula: str
    inputs: tuple[tuple[str, Any], ...]
    result: Any


@dataclass(frozen=True)
class EvidenceItem:
    evidence_id: str
    source: SourceReference
    values: tuple[dict[str, Any], ...] = ()
    calculation: CalculationStep | None = None


@dataclass(frozen=True)
class ClaimEvidence:
    claim: str
    evidence_ids: tuple[str, ...]
    supported: bool
    calculation_ids: tuple[str, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class VerificationCheck:
    name: str
    passed: bool
    warning: str | None = None


@dataclass(frozen=True)
class RepairAttempt:
    attempt: int
    failed_sql: str
    error_code: str
    error_message: str
    repaired_sql: str


@dataclass(frozen=True)
class Interpretation:
    question: str
    intent: str
    metrics: tuple[str, ...] = ()
    dimensions: tuple[str, ...] = ()
    filters: tuple[str, ...] = ()
    start_date: str | None = None
    end_date: str | None = None
    timezone: str | None = None
    assumptions: tuple[str, ...] = ()
    ambiguity: str | None = None


@dataclass(frozen=True)
class PlanOperation:
    operation: str
    relation: str
    detail: str


@dataclass(frozen=True)
class SemanticPlan:
    operations: tuple[PlanOperation, ...]
    joins: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnswerReceipt:
    artifact_digest: str
    eval_receipt_digest: str | None
    runtime: RuntimeIdentity
    database_type: str
    database_identity: str


@dataclass(frozen=True)
class QueryAnswer:
    status: VerificationStatus
    direct_answer: str | None
    interpretation: Interpretation
    plan: SemanticPlan
    sql: str | None
    sources: tuple[SourceReference, ...] = ()
    evidence: tuple[EvidenceItem, ...] = ()
    calculations: tuple[CalculationStep, ...] = ()
    claims: tuple[ClaimEvidence, ...] = ()
    verification: tuple[VerificationCheck, ...] = ()
    data: tuple[dict[str, Any], ...] = ()
    receipt: AnswerReceipt | None = None
    repairs: tuple[RepairAttempt, ...] = ()
    technical_details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status in {
            VerificationStatus.VERIFIED,
            VerificationStatus.VERIFIED_WITH_WARNINGS,
        }:
            if not self.sql or not self.receipt or not self.evidence:
                raise ValueError(
                    "A verified answer requires SQL, a receipt, and evidence."
                )
            unsupported = [claim.claim for claim in self.claims if not claim.supported]
            if unsupported:
                raise ValueError(
                    "A verified answer cannot contain unsupported claims: " + ", ".join(unsupported)
                )
