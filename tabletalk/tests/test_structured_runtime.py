from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

import pytest

from tabletalk.domain import (
    ErrorCode,
    RuntimeIdentity,
    TableTalkError,
    VerificationStatus,
)
from tabletalk.interfaces import LLMProvider
from tabletalk.runtime import StructuredQueryRuntime


class ScriptedModel(LLMProvider):
    def __init__(self, responses: list[dict[str, Any] | Exception]) -> None:
        super().__init__()
        self.responses = responses
        self.calls = 0

    def generate_response(self, prompt: str) -> str:
        raise AssertionError("The structured runtime must use structured model calls.")

    def generate_structured(
        self,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
    ) -> dict[str, Any]:
        del messages, json_schema
        response = self.responses[self.calls]
        self.calls += 1
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def artifact() -> dict[str, Any]:
    return {
        "digest": "a" * 64,
        "agent": {
            "relations": [
                {
                    "name": "main.customers",
                    "columns": [{"name": "id"}, {"name": "name"}],
                }
            ]
        },
    }


def query_payload(
    *,
    sql: str | None = "SELECT id, name FROM customers",
    ambiguity: str | None = None,
) -> dict[str, Any]:
    return {
        "interpretation": {
            "intent": "Find customers",
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
                "detail": "Return matching customers",
            }
        ],
        "sql": sql,
        "ambiguity": ambiguity,
    }


def answer_payload(
    evidence_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "direct_answer": "Alice is a customer.",
        "calculations": [],
        "claims": [
            {
                "claim": "Alice is a customer.",
                "evidence_ids": evidence_ids or ["row-0"],
                "calculation_ids": [],
            }
        ],
    }


def runtime(
    model: ScriptedModel,
    execute: Callable[[str], list[dict[str, Any]]],
    artifact: dict[str, Any],
) -> StructuredQueryRuntime:
    return StructuredQueryRuntime(
        model=model,
        execute=execute,
        artifact=artifact,
        runtime_identity=RuntimeIdentity(
            provider="ollama",
            model="gemma4:31b-cloud",
            base_url="http://localhost:11434/v1",
        ),
        database_type="sqlite",
        database_identity="customers.db",
        dialect="sqlite",
        eval_receipt_digest="b" * 64,
    )


def test_verified_answer_is_linked_to_executed_rows(
    artifact: dict[str, Any],
) -> None:
    model = ScriptedModel([query_payload(), answer_payload()])
    executed: list[str] = []

    answer = runtime(
        model,
        lambda sql: executed.append(sql) or [{"id": 1, "name": "Alice"}],
        artifact,
    ).invoke("Who is a customer?")

    assert answer.status is VerificationStatus.VERIFIED
    assert answer.direct_answer == "Alice is a customer."
    assert answer.evidence[0].evidence_id == "row-0"
    assert answer.claims[0].supported is True
    assert answer.receipt is not None
    assert answer.receipt.runtime.model == "gemma4:31b-cloud"
    assert model.calls == 2
    assert executed == ["SELECT id, name FROM customers LIMIT 500"]


def test_database_failure_never_calls_model_for_an_answer(
    artifact: dict[str, Any],
) -> None:
    model = ScriptedModel([query_payload(), answer_payload()])

    def fail(_sql: str) -> list[dict[str, Any]]:
        raise OSError("database is offline")

    with pytest.raises(TableTalkError) as raised:
        runtime(model, fail, artifact).invoke("Who is a customer?")

    assert raised.value.code is ErrorCode.DATABASE_QUERY_FAILED
    assert model.calls == 1


def test_empty_database_result_never_calls_model_for_an_answer(
    artifact: dict[str, Any],
) -> None:
    model = ScriptedModel([query_payload(), answer_payload()])

    answer = runtime(model, lambda _sql: [], artifact).invoke("Who is a customer?")

    assert answer.status is VerificationStatus.INSUFFICIENT_EVIDENCE
    assert answer.direct_answer is None
    assert answer.evidence == ()
    assert model.calls == 1


def test_model_failure_never_queries_database(artifact: dict[str, Any]) -> None:
    model = ScriptedModel([ConnectionError("Ollama unavailable")])
    executed: list[str] = []

    with pytest.raises(TableTalkError) as raised:
        runtime(
            model,
            lambda sql: executed.append(sql) or [],
            artifact,
        ).invoke("Who is a customer?")

    assert raised.value.code is ErrorCode.MODEL_UNAVAILABLE
    assert executed == []


def test_out_of_scope_sql_never_queries_database(
    artifact: dict[str, Any],
) -> None:
    payload = query_payload(sql="SELECT salary FROM payroll")
    model = ScriptedModel([payload])
    executed: list[str] = []

    with pytest.raises(TableTalkError) as raised:
        runtime(
            model,
            lambda sql: executed.append(sql) or [],
            artifact,
        ).invoke("What are salaries?")

    assert raised.value.code is ErrorCode.SQL_OUT_OF_SCOPE
    assert executed == []
    assert model.calls == 1


def test_ambiguity_returns_clarification_without_querying_database(
    artifact: dict[str, Any],
) -> None:
    model = ScriptedModel(
        [query_payload(sql=None, ambiguity="Which customer segment do you mean?")]
    )
    executed: list[str] = []

    answer = runtime(
        model,
        lambda sql: executed.append(sql) or [],
        artifact,
    ).invoke("Show the best customers.")

    assert answer.status is VerificationStatus.AMBIGUOUS
    assert answer.direct_answer == "Which customer segment do you mean?"
    assert answer.sql is None
    assert executed == []
    assert model.calls == 1


def test_unsupported_claim_is_not_exposed_as_a_direct_answer(
    artifact: dict[str, Any],
) -> None:
    model = ScriptedModel([query_payload(), answer_payload(["missing-row"])])

    answer = runtime(
        model,
        lambda _sql: [{"id": 1, "name": "Alice"}],
        artifact,
    ).invoke("Who is a customer?")

    assert answer.status is VerificationStatus.INSUFFICIENT_EVIDENCE
    assert answer.direct_answer is None
    assert answer.claims[0].supported is False


def test_fabricated_numeric_claim_is_withheld(
    artifact: dict[str, Any],
) -> None:
    answer = runtime(
        ScriptedModel(
            [
                query_payload(),
                {
                    "direct_answer": "There are 999 customers.",
                    "calculations": [],
                    "claims": [
                        {
                            "claim": "There are 999 customers.",
                            "evidence_ids": ["row-0"],
                            "calculation_ids": [],
                        }
                    ],
                },
            ]
        ),
        lambda _sql: [{"id": 1, "name": "Alice"}],
        artifact,
    ).invoke("How many customers are there?")

    assert answer.status is VerificationStatus.INSUFFICIENT_EVIDENCE
    assert answer.direct_answer is None
    assert answer.claims[0].supported is False


def test_material_calculation_is_reproduced_from_evidence(
    artifact: dict[str, Any],
) -> None:
    model = ScriptedModel(
        [
            query_payload(
                sql=(
                    "SELECT id AS current_value, id AS previous_value "
                    "FROM customers"
                )
            ),
            {
                "direct_answer": "Revenue increased 50%.",
                "calculations": [
                    {
                        "calculation_id": "change",
                        "label": "Revenue percentage change",
                        "operation": "percent_change",
                        "inputs": [
                            {
                                "evidence_id": "row-0",
                                "column": "current_value",
                            },
                            {
                                "evidence_id": "row-0",
                                "column": "previous_value",
                            },
                        ],
                    }
                ],
                "claims": [
                    {
                        "claim": "Revenue increased 50%.",
                        "evidence_ids": [],
                        "calculation_ids": ["change"],
                    }
                ],
            },
        ]
    )

    answer = runtime(
        model,
        lambda _sql: [{"current_value": 150, "previous_value": 100}],
        artifact,
    ).invoke("How did revenue change?")

    assert answer.status is VerificationStatus.VERIFIED
    assert answer.calculations[0].result == 0.5
    assert answer.calculations[0].formula == "(input_1 - input_2) / input_2"
    assert answer.claims[0].calculation_ids == ("change",)


def test_malformed_structured_output_fails_closed(
    artifact: dict[str, Any],
) -> None:
    payload = query_payload()
    payload["interpretation"]["metrics"] = "not-an-array"
    model = ScriptedModel([payload])

    with pytest.raises(TableTalkError) as raised:
        runtime(model, lambda _sql: [], artifact).invoke("Who is a customer?")

    assert raised.value.code is ErrorCode.MODEL_OUTPUT_MALFORMED


def test_undeclared_metric_fails_before_execution(
    artifact: dict[str, Any],
) -> None:
    payload = query_payload()
    payload["interpretation"]["metrics"] = ["fabricated_revenue"]
    executed: list[str] = []

    with pytest.raises(TableTalkError) as raised:
        runtime(
            ScriptedModel([payload]),
            lambda sql: executed.append(sql) or [],
            artifact,
        ).invoke("What was revenue?")

    assert raised.value.code is ErrorCode.SEMANTIC_INVALID
    assert executed == []


def test_plan_relation_outside_scope_fails_before_execution(
    artifact: dict[str, Any],
) -> None:
    payload = query_payload()
    payload["plan"][0]["relation"] = "main.payroll"
    executed: list[str] = []

    with pytest.raises(TableTalkError) as raised:
        runtime(
            ScriptedModel([payload]),
            lambda sql: executed.append(sql) or [],
            artifact,
        ).invoke("Who is a customer?")

    assert raised.value.code is ErrorCode.SEMANTIC_INVALID
    assert executed == []


def test_invalid_date_boundaries_fail_before_execution(
    artifact: dict[str, Any],
) -> None:
    payload = query_payload()
    payload["interpretation"]["start_date"] = "2026-02-01"
    payload["interpretation"]["end_date"] = "2026-01-01"

    with pytest.raises(TableTalkError) as raised:
        runtime(
            ScriptedModel([payload]),
            lambda _sql: [],
            artifact,
        ).invoke("Who was a customer last month?")

    assert raised.value.code is ErrorCode.SEMANTIC_INVALID


def test_configured_timezone_cannot_drift(
    artifact: dict[str, Any],
) -> None:
    governed = deepcopy(artifact)
    governed["agent"]["time_semantics"] = [["timezone", "America/New_York"]]

    with pytest.raises(TableTalkError) as raised:
        runtime(
            ScriptedModel([query_payload()]),
            lambda _sql: [],
            governed,
        ).invoke("Who is a customer?")

    assert raised.value.code is ErrorCode.SEMANTIC_INVALID


def test_ambiguous_interpretation_cannot_include_sql(
    artifact: dict[str, Any],
) -> None:
    payload = query_payload(ambiguity="Which customer segment?")

    with pytest.raises(TableTalkError) as raised:
        runtime(
            ScriptedModel([payload]),
            lambda _sql: [],
            artifact,
        ).invoke("Show the best customers.")

    assert raised.value.code is ErrorCode.MODEL_OUTPUT_MALFORMED


def test_repair_after_database_sql_error_is_bounded_and_disclosed(
    artifact: dict[str, Any],
) -> None:
    model = ScriptedModel(
        [
            query_payload(sql="SELECT UNSUPPORTED(name) FROM customers"),
            {"sql": "SELECT name FROM customers"},
            answer_payload(),
        ]
    )
    executed: list[str] = []

    def execute(sql: str) -> list[dict[str, Any]]:
        executed.append(sql)
        if len(executed) == 1:
            raise RuntimeError("no such function: UNSUPPORTED")
        return [{"name": "Alice"}]

    answer = runtime(model, execute, artifact).invoke("Who is a customer?")

    assert answer.status is VerificationStatus.VERIFIED_WITH_WARNINGS
    assert any(
        check.name == "query_repaired" and not check.passed
        for check in answer.verification
    )
    assert executed == [
        "SELECT UNSUPPORTED(name) FROM customers LIMIT 500",
        "SELECT name FROM customers LIMIT 500",
    ]
    assert len(answer.repairs) == 1
    assert answer.repairs[0].attempt == 1
    assert (
        answer.repairs[0].failed_sql
        == "SELECT UNSUPPORTED(name) FROM customers LIMIT 500"
    )
    assert answer.repairs[0].error_code == ErrorCode.DATABASE_QUERY_FAILED.value
    assert answer.repairs[0].repaired_sql == "SELECT name FROM customers"
    assert model.calls == 3


def test_repaired_sql_runs_all_scope_validation_again(
    artifact: dict[str, Any],
) -> None:
    model = ScriptedModel(
        [
            query_payload(sql="SELECT name FROM customers"),
            {"sql": "SELECT salary FROM payroll"},
        ]
    )
    executions = 0

    def execute(_sql: str) -> list[dict[str, Any]]:
        nonlocal executions
        executions += 1
        raise RuntimeError("no such function: unsupported")

    with pytest.raises(TableTalkError) as raised:
        runtime(model, execute, artifact).invoke("Who is a customer?")

    assert raised.value.code is ErrorCode.SQL_OUT_OF_SCOPE
    assert executions == 1
    assert model.calls == 2


def test_repair_limit_prevents_infinite_model_loop(
    artifact: dict[str, Any],
) -> None:
    model = ScriptedModel(
        [
            query_payload(sql="SELECT name FROM customers"),
            {"sql": "SELECT name FROM customers"},
            answer_payload(),
        ]
    )
    executions = 0

    def execute(_sql: str) -> list[dict[str, Any]]:
        nonlocal executions
        executions += 1
        raise RuntimeError("syntax error near FROM")

    with pytest.raises(TableTalkError) as raised:
        runtime(model, execute, artifact).invoke("Who is a customer?")

    assert raised.value.code is ErrorCode.DATABASE_QUERY_FAILED
    assert raised.value.details["repair_attempts"] == 1
    assert executions == 2
    assert model.calls == 2


def test_connection_failure_is_never_sent_to_model_for_repair(
    artifact: dict[str, Any],
) -> None:
    model = ScriptedModel([query_payload(), {"sql": "SELECT name FROM customers"}])

    def execute(_sql: str) -> list[dict[str, Any]]:
        raise ConnectionError("warehouse unavailable")

    with pytest.raises(TableTalkError) as raised:
        runtime(model, execute, artifact).invoke("Who is a customer?")

    assert raised.value.code is ErrorCode.DATABASE_QUERY_FAILED
    assert model.calls == 1


def test_database_error_in_repair_record_redacts_secret_shaped_values(
    artifact: dict[str, Any],
) -> None:
    model = ScriptedModel(
        [
            query_payload(),
            {"sql": "SELECT name FROM customers"},
            answer_payload(),
        ]
    )
    executions = 0

    def execute(_sql: str) -> list[dict[str, Any]]:
        nonlocal executions
        executions += 1
        if executions == 1:
            raise RuntimeError(
                "syntax error password=do-not-leak token:also-secret"
            )
        return [{"name": "Alice"}]

    answer = runtime(model, execute, artifact).invoke("Who is a customer?")

    serialized = str(answer.repairs)
    assert "do-not-leak" not in serialized
    assert "also-secret" not in serialized
    assert "[REDACTED]" in serialized
