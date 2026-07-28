from __future__ import annotations

import json
from pathlib import Path

import pytest

from tabletalk.agents import AgentDefinition
from tabletalk.domain import ErrorCode, TableTalkError


def write_agent(path: Path, extra: str = "") -> Path:
    path.write_text(
        """\
kind: Agent
name: sales
description: Answers governed sales questions.
connection: local
relations:
  include:
    - analytics.*
  exclude:
    - analytics.customer_sensitive
semantics:
  metrics:
    recognized_revenue:
      label: Recognized revenue
      description: Revenue after returns and cancellations.
      expression: analytics.orders.net_revenue
      aggregation: sum
      time_dimension: analytics.orders.order_date
      unit: USD
      synonyms: [revenue, sales]
      required_filters:
        - analytics.orders.status != 'cancelled'
  time:
    timezone: America/New_York
    week_start: monday
    default_dimension: analytics.orders.order_date
  rules:
    - State exact dates for relative time periods.
policies:
  read_only: true
  require_evidence: true
  max_rows: 500
  timeout_seconds: 30
  max_repair_attempts: 1
evals:
  - sales_regression
version: "1"
owner: data-team
sample_questions:
  - What was revenue last week?
"""
        + extra
    )
    return path


@pytest.fixture
def schema() -> dict:
    return {
        "analytics.orders": {
            "description": "Curated orders",
            "columns": [
                {"name": "id", "type": "INTEGER", "nullable": False},
                {"name": "net_revenue", "type": "DECIMAL"},
                {"name": "order_date", "type": "DATE"},
                {"name": "status", "type": "TEXT"},
            ],
            "primary_key": ["id"],
        },
        "analytics.products": {
            "columns": [{"name": "id", "type": "INTEGER"}],
            "primary_key": ["id"],
        },
        "analytics.customer_sensitive": {
            "columns": [{"name": "ssn", "type": "TEXT"}],
        },
    }


def test_first_class_agent_compiles_patterns_to_explicit_scope(
    tmp_path: Path,
    schema: dict,
) -> None:
    definition = AgentDefinition.load(write_agent(tmp_path / "sales.yaml"))

    artifact = definition.compile(
        schema,
        connection_type="duckdb",
        dialect="duckdb",
    )
    payload = json.loads(artifact.to_json())
    agent = payload["agent"]

    assert agent["resource_kind"] == "Agent"
    assert agent["format_version"] == "2"
    assert agent["connection"] == "local"
    assert agent["connection_type"] == "duckdb"
    assert agent["dialect"] == "duckdb"
    assert [relation["name"] for relation in agent["relations"]] == [
        "analytics.orders",
        "analytics.products",
    ]
    assert agent["metrics"][0]["name"] == "recognized_revenue"
    assert agent["metrics"][0]["aggregation"] == "sum"
    assert agent["metrics"][0]["unit"] == "USD"
    assert agent["rules"] == ["State exact dates for relative time periods."]
    assert agent["required_evals"] == ["sales_regression"]
    assert agent["policies"] == [
        ["max_repair_attempts", 1],
        ["max_rows", 500],
        ["read_only", True],
        ["require_evidence", True],
        ["timeout_seconds", 30],
    ]
    assert len(agent["source_fingerprints"]) == 2


def test_agent_resource_compilation_is_byte_deterministic(
    tmp_path: Path,
    schema: dict,
) -> None:
    definition = AgentDefinition.load(write_agent(tmp_path / "sales.yaml"))

    first = definition.compile(schema, connection_type="duckdb", dialect="duckdb")
    reordered = dict(reversed(list(schema.items())))
    reordered["analytics.orders"]["columns"].reverse()
    second = definition.compile(
        reordered,
        connection_type="duckdb",
        dialect="duckdb",
    )

    assert first.digest == second.digest
    assert first.to_json() == second.to_json()


def test_agent_requires_kind_connection_and_explicit_relations(
    tmp_path: Path,
) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text(
        """\
name: sales
description: Sales.
connection: local
relations:
  include: [analytics.orders]
"""
    )

    with pytest.raises(TableTalkError) as raised:
        AgentDefinition.load(path)

    assert raised.value.code is ErrorCode.CONFIG_INVALID
    assert "kind: Agent" in raised.value.message


@pytest.mark.parametrize(
    "policy",
    [
        "read_only: false",
        "require_evidence: false",
        "max_rows: 0",
        "timeout_seconds: 5000",
    ],
)
def test_agent_cannot_disable_trust_policies(
    tmp_path: Path,
    policy: str,
) -> None:
    path = write_agent(tmp_path / "invalid.yaml")
    content = path.read_text()
    content = content.replace(
        "  read_only: true\n  require_evidence: true\n  max_rows: 500\n  timeout_seconds: 30",
        "  " + policy,
    )
    path.write_text(content)

    with pytest.raises(TableTalkError) as raised:
        AgentDefinition.load(path)

    assert raised.value.code is ErrorCode.CONFIG_INVALID


def test_relation_pattern_must_match_and_cannot_resolve_empty(
    tmp_path: Path,
    schema: dict,
) -> None:
    path = write_agent(tmp_path / "sales.yaml")
    path.write_text(path.read_text().replace("analytics.*", "finance.*"))
    definition = AgentDefinition.load(path)

    with pytest.raises(TableTalkError) as raised:
        definition.compile(schema, connection_type="sqlite", dialect="sqlite")

    assert raised.value.code is ErrorCode.CONFIG_INVALID
    assert raised.value.details["pattern"] == "finance.*"


def test_secret_bearing_semantics_never_compile(
    tmp_path: Path,
    schema: dict,
) -> None:
    path = write_agent(tmp_path / "sales.yaml")
    path.write_text(
        path.read_text().replace(
            "semantics:\n",
            "semantics:\n  api_key: must-not-compile\n",
        )
    )
    definition = AgentDefinition.load(path)

    with pytest.raises(TableTalkError) as raised:
        definition.compile(schema, connection_type="sqlite", dialect="sqlite")

    assert "must-not-compile" not in raised.value.message
