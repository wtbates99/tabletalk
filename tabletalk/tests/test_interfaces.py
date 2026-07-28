"""Tests for the narrow runtime contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tabletalk.domain import (
    AnswerReceipt,
    EvidenceItem,
    Interpretation,
    QueryAnswer,
    RuntimeIdentity,
    SemanticPlan,
    SourceReference,
    TableTalkError,
    VerificationStatus,
)
from tabletalk.interfaces import LLMProvider, QuerySession


class TextModel(LLMProvider):
    def __init__(self, response: str) -> None:
        super().__init__()
        self.response = response

    def generate_response(self, prompt: str) -> str:
        del prompt
        return self.response


def test_base_model_contract_validates_structured_json() -> None:
    model = TextModel('{"name":"sales"}')

    result = model.generate_structured(
        [{"role": "user", "content": "Name the Agent"}],
        {
            "type": "object",
            "required": ["name"],
            "additionalProperties": False,
            "properties": {"name": {"type": "string"}},
        },
    )

    assert result == {"name": "sales"}


def test_base_model_contract_rejects_malformed_json() -> None:
    with pytest.raises(TableTalkError):
        TextModel("not json").generate_structured(
            [{"role": "user", "content": "test"}],
            {"type": "object"},
        )


def test_query_session_requires_a_project_configuration(tmp_path: Path) -> None:
    with pytest.raises(TableTalkError):
        QuerySession(str(tmp_path))


def test_query_session_requires_an_explicit_llm(tmp_path: Path) -> None:
    (tmp_path / "tabletalk.yaml").write_text("connections: {}\n")

    with pytest.raises(TableTalkError):
        QuerySession(str(tmp_path))


def _verified_answer() -> QueryAnswer:
    source = SourceReference("main.orders", ("revenue",), (0,))
    return QueryAnswer(
        status=VerificationStatus.VERIFIED,
        direct_answer="Revenue was 100.",
        interpretation=Interpretation(
            question="password=very-secret revenue?",
            intent="revenue",
        ),
        plan=SemanticPlan(()),
        sql="SELECT 100 AS revenue LIMIT 1",
        sources=(source,),
        evidence=(EvidenceItem("row-0", source, ({"revenue": 100},)),),
        receipt=AnswerReceipt(
            artifact_digest="a" * 64,
            eval_receipt_digest=None,
            runtime=RuntimeIdentity("ollama", "gemma4:31b-cloud"),
            database_type="sqlite",
            database_identity="analytics.db",
        ),
        data=({"revenue": 100},),
    )


def test_invocation_history_redacts_credential_shaped_question(
    tmp_path: Path,
) -> None:
    session = QuerySession.__new__(QuerySession)
    session.project_folder = str(tmp_path)
    answer = _verified_answer()

    session._write_invocation(
        "sales",
        "password=very-secret revenue?",
        answer,
    )

    record_path = next((tmp_path / ".tabletalk" / "history").rglob("*.json"))
    raw = record_path.read_text()
    record: dict[str, Any] = json.loads(raw)
    assert "very-secret" not in raw
    assert record["question"] == "password=[REDACTED] revenue?"
    assert "data" not in record


def test_database_initialization_failure_is_typed_and_never_uses_model(
    tmp_path: Path,
) -> None:
    session = QuerySession.__new__(QuerySession)
    session.project_folder = str(tmp_path)
    session.config = {
        "connections": {
            "local": {
                "type": "sqlite",
                "path": str(tmp_path / "missing.db"),
                "read_only": True,
            }
        }
    }
    session._active_connection_name = "local"
    session._db_provider = None
    session._db_connection_name = None

    with patch(
        "tabletalk.factories.get_db_provider",
        side_effect=OSError("offline"),
    ):
        with pytest.raises(TableTalkError) as raised:
            session._database()

    assert raised.value.code.value == "database_unavailable"
