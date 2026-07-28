from __future__ import annotations

import json

import pytest

from tabletalk.compiler import compile_agent
from tabletalk.domain import ErrorCode, TableTalkError


def _agent() -> dict:
    return {
        "name": "sales_analyst",
        "context": "sales",
        "version": "1",
        "owner": "data",
        "sample_questions": ["Revenue?", "Orders?"],
        "required_evals": ["sales_regression"],
        "policies": {"read_only": True},
    }


def _context() -> dict:
    return {
        "name": "sales",
        "datasets": [
            {
                "name": "main",
                "tables": [
                    {"name": "orders", "description": "Trusted orders"},
                    "customers",
                ],
            }
        ],
        "metrics": [
            {
                "name": "revenue",
                "expression": "sum(orders.total)",
                "relation": "main.orders",
            }
        ],
        "relationships": [
            {
                "name": "orders_customer",
                "source": "main.orders",
                "target": "main.customers",
                "on": "orders.customer_id = customers.id",
                "cardinality": "many_to_one",
            }
        ],
        "time_semantics": {"timezone": "UTC", "week_start": "monday"},
    }


def _schema() -> dict:
    return {
        "main.orders": {
            "columns": [
                {"name": "total", "type": "DECIMAL", "nullable": False},
                {"name": "id", "type": "INTEGER", "nullable": False},
            ],
            "primary_key": ["id"],
        },
        "main.customers": {
            "description": "Customer accounts",
            "columns": [{"name": "id", "type": "INTEGER"}],
            "primary_key": ["id"],
        },
    }


def test_compilation_is_deterministic_across_input_order() -> None:
    first = compile_agent(_agent(), _context(), _schema())
    context = _context()
    context["datasets"][0]["tables"].reverse()
    context["relationships"].reverse()
    schema = dict(reversed(list(_schema().items())))
    schema["main.orders"]["columns"].reverse()

    second = compile_agent(_agent(), context, schema)

    assert first.digest == second.digest
    assert first.to_json() == second.to_json()


def test_semantic_change_changes_digest() -> None:
    original = compile_agent(_agent(), _context(), _schema())
    context = _context()
    context["metrics"][0]["expression"] = "sum(orders.net_total)"

    changed = compile_agent(_agent(), context, _schema())

    assert original.digest != changed.digest


def test_artifact_has_version_and_exact_required_evals() -> None:
    artifact = compile_agent(_agent(), _context(), _schema())
    payload = json.loads(artifact.to_json())

    assert payload["agent"]["format_version"] == "2"
    assert payload["agent"]["required_evals"] == ["sales_regression"]
    assert payload["digest"] == artifact.digest


def test_secret_bearing_fields_are_rejected() -> None:
    agent = _agent()
    agent["api_key"] = "must-not-compile"

    with pytest.raises(TableTalkError) as raised:
        compile_agent(agent, _context(), _schema())

    assert raised.value.code is ErrorCode.CONFIG_INVALID
    assert "must-not-compile" not in str(raised.value)


def test_duplicate_relation_is_rejected() -> None:
    context = _context()
    context["datasets"][0]["tables"].append("orders")

    with pytest.raises(TableTalkError, match="declared more than once"):
        compile_agent(_agent(), context, _schema())


def test_declared_relation_missing_from_snapshot_is_rejected() -> None:
    schema = _schema()
    del schema["main.customers"]

    with pytest.raises(TableTalkError, match="missing from the schema snapshot"):
        compile_agent(_agent(), _context(), schema)
