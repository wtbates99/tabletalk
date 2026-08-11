"""Optional model hypotheses for unresolved semantic gaps."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tabletalk.interfaces import LLMProvider
from tabletalk.schemas import content_digest
from tabletalk.semantic_analysis import SemanticAnalysis
from tabletalk.semantics import EvidenceKind, EvidenceRef

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answers"],
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["gap_id", "value", "definition"],
                "properties": {
                    "gap_id": {"type": "string"},
                    "value": {"type": "string"},
                    "definition": {"type": "string"},
                },
            },
        }
    },
}


@dataclass(frozen=True)
class InferredSemanticAnswer:
    gap_id: str
    value: str
    definition: str
    evidence: EvidenceRef


def infer_semantic_answers(
    analysis: SemanticAnalysis,
    llm: LLMProvider,
    *,
    model_identity: str,
    limit: int = 3,
) -> dict[str, InferredSemanticAnswer]:
    """Request a small set of hypotheses and keep every result untrusted."""
    questions = analysis.high_value_questions(limit)
    if not questions:
        return {}
    allowed = {item.id for item in questions}
    prompt = {
        "instruction": (
            "Propose one possible answer for each semantic question. "
            "Use only the supplied dbt evidence. These answers remain untrusted."
        ),
        "questions": [item.to_dict() for item in questions],
    }
    output = llm.generate_structured(
        [
            {"role": "system", "content": str(prompt["instruction"])},
            {"role": "user", "content": str(prompt["questions"])},
        ],
        _SCHEMA,
    )
    digest = content_digest(output)
    evidence = EvidenceRef(
        EvidenceKind.LLM_INFERENCE,
        model_identity,
        digest,
        "/answers",
    )
    result = {}
    for raw in output.get("answers") or ():
        gap_id = str(raw.get("gap_id") or "")
        if gap_id not in allowed:
            raise ValueError(f"The model answered an unknown semantic gap: {gap_id}")
        if gap_id in result:
            raise ValueError(f"The model answered semantic gap '{gap_id}' more than once")
        value = " ".join(str(raw.get("value") or "").split())
        definition = " ".join(str(raw.get("definition") or "").split())
        if not value or not definition:
            raise ValueError(f"The model returned an empty answer for semantic gap '{gap_id}'")
        result[gap_id] = InferredSemanticAnswer(
            gap_id,
            value,
            definition,
            evidence,
        )
    return result
