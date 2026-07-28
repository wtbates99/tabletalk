from __future__ import annotations

import pytest

from tabletalk.domain import (
    AnswerReceipt,
    ClaimEvidence,
    ErrorCode,
    EvidenceItem,
    Interpretation,
    PlanOperation,
    QueryAnswer,
    RuntimeIdentity,
    RuntimeStage,
    SemanticPlan,
    SourceReference,
    TableTalkError,
    VerificationStatus,
    canonical_digest,
    canonical_json,
    model_request_error,
)


def _interpretation() -> Interpretation:
    return Interpretation(
        question="How many customers are there?",
        intent="count customers",
    )


def _plan() -> SemanticPlan:
    return SemanticPlan(
        operations=(
            PlanOperation(
                operation="aggregate",
                relation="main.customers",
                detail="count rows",
            ),
        )
    )


def test_canonical_domain_serialization_is_deterministic() -> None:
    left = Interpretation(
        question="Revenue?",
        intent="sum revenue",
        filters=("status = paid",),
        assumptions=("USD",),
    )
    right = Interpretation(
        question="Revenue?",
        intent="sum revenue",
        filters=("status = paid",),
        assumptions=("USD",),
    )

    assert canonical_json(left) == canonical_json(right)
    assert canonical_digest(left) == canonical_digest(right)


def test_canonical_mapping_order_does_not_change_digest() -> None:
    assert canonical_digest({"b": 2, "a": 1}) == canonical_digest({"a": 1, "b": 2})


def test_verified_answer_requires_execution_evidence_and_receipt() -> None:
    with pytest.raises(ValueError, match="requires SQL, a receipt, and evidence"):
        QueryAnswer(
            status=VerificationStatus.VERIFIED,
            direct_answer="There are 3 customers.",
            interpretation=_interpretation(),
            plan=_plan(),
            sql="SELECT COUNT(*) FROM main.customers",
        )


def test_verified_answer_rejects_unsupported_claim() -> None:
    source = SourceReference("main.customers", ("count",), (0,))
    with pytest.raises(ValueError, match="unsupported claims"):
        QueryAnswer(
            status=VerificationStatus.VERIFIED,
            direct_answer="There are 3 customers.",
            interpretation=_interpretation(),
            plan=_plan(),
            sql="SELECT COUNT(*) AS count FROM main.customers",
            sources=(source,),
            evidence=(EvidenceItem("e1", source, ({"count": 3},)),),
            claims=(ClaimEvidence("There are 3 customers.", (), False),),
            receipt=AnswerReceipt(
                artifact_digest="a" * 64,
                eval_receipt_digest=None,
                runtime=RuntimeIdentity("ollama", "gemma4:31b-cloud"),
                database_type="sqlite",
                database_identity="fixture.db",
            ),
        )


def test_verification_statuses_are_explicit_and_non_probabilistic() -> None:
    assert {status.value for status in VerificationStatus} == {
        "verified",
        "verified_with_warnings",
        "ambiguous",
        "insufficient_evidence",
    }


def test_typed_error_serializes_without_hidden_exception_state() -> None:
    error = TableTalkError(
        ErrorCode.MODEL_UNAVAILABLE,
        RuntimeStage.GENERATION,
        "Configured model is unavailable.",
        retryable=True,
        details={"provider": "ollama", "model": "gemma4:31b-cloud"},
    )

    assert error.to_dict() == {
        "code": "model_unavailable",
        "stage": "generation",
        "message": "Configured model is unavailable.",
        "retryable": True,
        "details": {"provider": "ollama", "model": "gemma4:31b-cloud"},
    }


def test_model_error_translation_does_not_expose_raw_secret_message() -> None:
    error = model_request_error(
        RuntimeError("request failed with api_key=do-not-leak"),
        provider="openai-compatible",
        model="production-model",
    )

    serialized = canonical_json(error.to_dict())
    assert error.code is ErrorCode.MODEL_REQUEST_FAILED
    assert "do-not-leak" not in serialized
