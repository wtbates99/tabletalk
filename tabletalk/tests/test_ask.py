from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tabletalk.domain import (
    ErrorCode,
    RuntimeIdentity,
    TableTalkError,
    VerificationStatus,
    canonical_digest,
)
from tabletalk.interfaces import LLMProvider, QuerySession


class AskModel(LLMProvider):
    model = "gemma4:31b-cloud"
    base_url = "http://localhost:11434/v1"

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def generate_response(self, prompt: str) -> str:
        raise AssertionError("ask must use structured output")

    def generate_structured(
        self,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
    ) -> dict[str, Any]:
        del messages, json_schema
        self.calls += 1
        if self.calls == 1:
            return {
                "interpretation": {
                    "intent": "List customers",
                    "metrics": [],
                    "dimensions": ["customer"],
                    "filters": [],
                    "start_date": None,
                    "end_date": None,
                    "timezone": "UTC",
                    "assumptions": [],
                },
                "plan": [
                    {
                        "operation": "select",
                        "relation": "main.customers",
                        "detail": "List customer names",
                    }
                ],
                "sql": "SELECT name FROM customers",
                "ambiguity": None,
            }
        return {
            "direct_answer": "Alice is a customer.",
            "calculations": [],
            "claims": [
                {
                    "claim": "Alice is a customer.",
                    "evidence_ids": ["row-0"],
                    "calculation_ids": [],
                }
            ],
        }


def write_applied_state(project: Path, *, corrupt: bool = False) -> dict[str, Any]:
    agent = {
        "name": "customer_analyst",
        "connection": "local",
        "relations": [
            {
                "name": "main.customers",
                "columns": [{"name": "id"}, {"name": "name"}],
            }
        ],
    }
    digest = canonical_digest(agent)
    artifact = {"digest": "bad" if corrupt else digest, "agent": agent}
    state_dir = project / ".tabletalk"
    state_dir.mkdir()
    artifact_dir = state_dir / "artifacts" / "customers"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / f"{digest}.json").write_text(json.dumps(artifact))
    (state_dir / "state.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "agents": {
                    "customers": {
                        "artifact_digest": digest,
                        "eval_receipts": ["e" * 64],
                    }
                },
            }
        )
    )
    return artifact


def bare_session(project: Path, model: AskModel) -> QuerySession:
    session = QuerySession.__new__(QuerySession)
    session.project_folder = str(project)
    session.config = {
        "llm": {"provider": "ollama"},
        "connections": {
            "local": {
                "type": "sqlite",
                "path": "customers.db",
                "read_only": True,
            }
        },
    }
    session.llm_provider = model
    session._active_connection_name = None
    session._db_provider = None
    session._db_connection_name = None
    return session


def test_ask_uses_applied_artifact_and_returns_verified_answer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = write_applied_state(tmp_path)
    model = AskModel()
    session = bare_session(tmp_path, model)
    executed: list[tuple[str, dict[str, Any] | None]] = []

    def execute(
        sql: str,
        *,
        artifact: dict[str, Any] | None = None,
        **_policy: Any,
    ):
        executed.append((sql, artifact))
        return [{"name": "Alice"}]

    monkeypatch.setattr(session, "execute_sql", execute)

    answer = session.ask("customers", "Who is a customer?")

    assert answer.status is VerificationStatus.VERIFIED
    assert answer.receipt is not None
    assert answer.receipt.runtime == RuntimeIdentity(
        "ollama",
        "gemma4:31b-cloud",
        "http://localhost:11434/v1",
    )
    assert executed == [("SELECT name FROM customers LIMIT 500", artifact)]


def test_ask_rejects_mutated_applied_artifact(tmp_path: Path) -> None:
    write_applied_state(tmp_path, corrupt=True)
    session = bare_session(tmp_path, AskModel())

    with pytest.raises(TableTalkError) as raised:
        session.ask("customers", "Who is a customer?")

    assert raised.value.code is ErrorCode.CONFIG_INVALID


def test_ask_requires_applied_state(tmp_path: Path) -> None:
    session = bare_session(tmp_path, AskModel())

    with pytest.raises(TableTalkError) as raised:
        session.ask("customers", "Who is a customer?")

    assert raised.value.code is ErrorCode.CONFIG_INVALID
