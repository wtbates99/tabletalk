from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
)


def verified_answer() -> QueryAnswer:
    source = SourceReference(
        relation="main.customers",
        columns=("name",),
        row_indices=(0,),
    )
    return QueryAnswer(
        status=VerificationStatus.VERIFIED,
        direct_answer="Alice is a customer.",
        interpretation=Interpretation(
            question="Who is a customer?",
            intent="List customers",
            dimensions=("customer",),
            timezone="UTC",
        ),
        plan=SemanticPlan(
            (
                PlanOperation(
                    operation="select",
                    relation="main.customers",
                    detail="List customer names",
                ),
            )
        ),
        sql="SELECT name FROM customers",
        sources=(source,),
        evidence=(
            EvidenceItem(
                evidence_id="row-0",
                source=SourceReference("query_result", ("name",), (0,)),
                values=({"name": "Alice"},),
            ),
        ),
        claims=(
            ClaimEvidence(
                claim="Alice is a customer.",
                evidence_ids=("row-0",),
                supported=True,
            ),
        ),
        data=({"name": "Alice"},),
        receipt=AnswerReceipt(
            artifact_digest="a" * 64,
            eval_receipt_digest="e" * 64,
            runtime=RuntimeIdentity(
                provider="ollama",
                model="gemma4:31b-cloud",
                base_url="http://localhost:11434/v1",
            ),
            database_type="sqlite",
            database_identity="customers.db",
        ),
    )


class FakeSession:
    def __init__(self, response: QueryAnswer | Exception | None = None) -> None:
        self.config = {
            "llm": {
                "provider": "ollama",
                "api_key": "must-not-leak",
            }
        }
        self.llm_provider = SimpleNamespace(
            model="gemma4:31b-cloud",
            base_url="http://localhost:11434/v1",
        )
        self.response = response or verified_answer()
        self.calls: list[tuple[str, str]] = []

    def ask(self, agent: str, question: str) -> QueryAnswer:
        self.calls.append((agent, question))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.fixture
def flask_app(tmp_path: Path):
    import tabletalk.app as app_module

    state_dir = tmp_path / ".tabletalk"
    state_dir.mkdir()
    artifact_dir = state_dir / "artifacts" / "customers"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / f"{'a' * 64}.json").write_text(
        json.dumps(
            {
                "digest": "a" * 64,
                "agent": {
                    "name": "customer_analyst",
                    "description": "Answers customer questions.",
                    "relations": [{"name": "main.customers"}],
                },
            }
        )
    )
    (state_dir / "state.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "agents": {
                    "customers": {
                        "artifact_digest": "a" * 64,
                        "eval_receipts": ["e" * 64],
                    }
                },
            }
        )
    )
    fake = FakeSession()
    app_module.project_folder = str(tmp_path)
    app_module._qs = fake
    app_module.app.config.update(TESTING=True)
    with app_module.app.test_client() as client:
        yield client, fake, app_module
    app_module._qs = None


def test_web_shell_is_centered_on_trusted_answer_sections(flask_app) -> None:
    client, _fake, _module = flask_app

    response = client.get("/")

    assert response.status_code == 200
    html = response.data.decode()
    for section in (
        "INTERPRETATION",
        "VERIFICATION",
        "EVIDENCE",
        "DATA",
        "SQL and sources",
        "Technical receipt",
    ):
        assert section in html
    assert "No model or database fallback is enabled." in html


def test_security_headers_are_set(flask_app) -> None:
    client, _fake, _module = flask_app

    response = client.get("/")

    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]


def test_health_reports_applied_agent_count(flask_app) -> None:
    client, _fake, _module = flask_app

    assert client.get("/health").get_json() == {
        "status": "ready",
        "applied_agents": 1,
    }


def test_agent_list_comes_only_from_applied_state(flask_app) -> None:
    client, _fake, _module = flask_app

    payload = client.get("/api/agents").get_json()

    assert payload["agents"] == [
        {
            "name": "customers",
            "agent_name": "customer_analyst",
            "description": "Answers customer questions.",
            "artifact_digest": "a" * 64,
            "eval_receipts": ["e" * 64],
            "relation_count": 1,
        }
    ]


def test_runtime_config_exposes_identity_but_never_api_key(flask_app) -> None:
    client, _fake, _module = flask_app

    response = client.get("/api/config")
    serialized = response.get_data(as_text=True)

    assert response.get_json() == {
        "provider": "ollama",
        "model": "gemma4:31b-cloud",
        "endpoint": "http://localhost:11434/v1",
        "fallback": "disabled",
    }
    assert "must-not-leak" not in serialized


def test_ask_returns_complete_structured_answer_after_verification(flask_app) -> None:
    client, fake, _module = flask_app

    response = client.post(
        "/api/ask",
        json={"agent": "customers", "question": "Who is a customer?"},
    )
    answer = response.get_json()["answer"]

    assert response.status_code == 200
    assert answer["status"] == "verified"
    assert answer["sql"] == "SELECT name FROM customers"
    assert answer["sources"][0]["relation"] == "main.customers"
    assert answer["claims"][0]["evidence_ids"] == ["row-0"]
    assert answer["receipt"]["runtime"]["model"] == "gemma4:31b-cloud"
    assert fake.calls == [("customers", "Who is a customer?")]


def test_typed_runtime_failure_is_explicit_and_has_no_answer(flask_app) -> None:
    client, fake, _module = flask_app
    fake.response = TableTalkError(
        ErrorCode.MODEL_UNAVAILABLE,
        RuntimeStage.GENERATION,
        "Configured model is unavailable.",
    )

    response = client.post(
        "/api/ask",
        json={"agent": "customers", "question": "Who is a customer?"},
    )
    payload = response.get_json()

    assert response.status_code == 422
    assert payload["failure"]["code"] == "model_unavailable"
    assert "answer" not in payload


def test_unexpected_failure_does_not_expose_exception_or_secret(flask_app) -> None:
    client, fake, _module = flask_app
    fake.response = RuntimeError("password=must-not-leak")

    response = client.post(
        "/api/ask",
        json={"agent": "customers", "question": "Who is a customer?"},
    )
    serialized = response.get_data(as_text=True)

    assert response.status_code == 500
    assert "must-not-leak" not in serialized
    assert response.get_json()["failure"]["code"] == "unexpected_failure"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"agent": "", "question": "Question"},
        {"agent": "customers", "question": ""},
        {"agent": "customers", "question": "x" * 4001},
    ],
)
def test_invalid_ask_requests_do_not_reach_runtime(
    flask_app,
    payload: dict[str, Any] | None,
) -> None:
    client, fake, _module = flask_app
    kwargs = {"json": payload} if payload is not None else {}

    response = client.post("/api/ask", **kwargs)

    assert response.status_code == 400
    assert response.get_json()["failure"]["code"] == "invalid_request"
    assert fake.calls == []


@pytest.mark.parametrize(
    "path",
    ["/chat/stream", "/fix/stream", "/favorites", "/webhooks", "/cache/stats"],
)
def test_superseded_legacy_routes_are_absent(flask_app, path: str) -> None:
    client, _fake, _module = flask_app

    assert client.get(path).status_code == 404
