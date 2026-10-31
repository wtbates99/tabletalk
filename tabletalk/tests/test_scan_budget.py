"""Snowflake hardening: scan budget, server-side timeout, and query tags (mocked connector)."""

from __future__ import annotations

import json
import sys
import types
from dataclasses import replace
from typing import Any

import pytest

from tabletalk.agents import Agent, AgentError
from tabletalk.connections import ReadOnlyConnection, Target
from tabletalk.manifest import Manifest
from tabletalk.providers.snowflake_provider import SnowflakeProvider
from tabletalk.scan_budget import ScanBudgetError, check_scan_budget, format_bytes, query_tag
from tabletalk.tests.test_dbt_native import EXAMPLE, make_runtime

QUESTION = "What was recognized revenue in July 2026?"


class FakeSnowflake:
    """Records every statement and answers EXPLAIN with a fixed byte estimate."""

    def __init__(self, bytes_assigned: int | None = 1_200_000) -> None:
        self.bytes_assigned = bytes_assigned
        self.statements: list[str] = []
        self._last = ""

    def cursor(self) -> FakeSnowflake:
        return self

    @property
    def description(self) -> list[tuple[str]]:
        return [("REVENUE",)]

    def execute(self, sql: str, params: object = None) -> FakeSnowflake:
        self.statements.append(sql)
        self._last = sql
        return self

    def fetchone(self) -> tuple[str] | None:
        if self.bytes_assigned is None:
            return None
        plan = {"GlobalStats": {"partitionsTotal": 4, "bytesAssigned": self.bytes_assigned}}
        return (json.dumps(plan),)

    def fetchall(self) -> list[tuple[float]]:
        return [(184.25,)]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeSnowflake:
    server = FakeSnowflake()
    connector = types.ModuleType("snowflake.connector")
    connector.connect = lambda **kwargs: server  # type: ignore[attr-defined]
    package = types.ModuleType("snowflake")
    package.connector = connector  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "snowflake", package)
    monkeypatch.setitem(sys.modules, "snowflake.connector", connector)
    return server


def _connection() -> ReadOnlyConnection:
    target = Target("analytics", "prod", "snowflake", {"account": "acme", "database": "db"})
    return ReadOnlyConnection(target, SnowflakeProvider("acme", "user", "pw", "db", "wh"))


def test_every_query_sets_its_tag_and_a_server_side_timeout(fake: FakeSnowflake) -> None:
    connection = _connection()

    connection.execute("select 1", 30, query_tag='{"agent":"revenue"}')
    connection.execute("select 2", 60)

    assert fake.statements == [
        """ALTER SESSION SET QUERY_TAG = '{"agent":"revenue"}', STATEMENT_TIMEOUT_IN_SECONDS = 30""",
        "select 1",
        # An untagged query clears the previous tag instead of inheriting it.
        "ALTER SESSION SET QUERY_TAG = '', STATEMENT_TIMEOUT_IN_SECONDS = 60",
        "select 2",
    ]


def test_query_tags_cannot_break_out_of_the_string_literal(fake: FakeSnowflake) -> None:
    _connection().execute("select 1", 5, query_tag="x'; drop table t; --\\")

    assert fake.statements[0].startswith(r"ALTER SESSION SET QUERY_TAG = 'x\'; drop table t; --\\'")


def test_scan_within_budget_passes_and_reports_the_estimate(fake: FakeSnowflake) -> None:
    check = check_scan_budget(_connection(), "select 1", 10_000_000_000)

    assert fake.statements == ["EXPLAIN USING JSON select 1"]
    assert check.passed
    assert check.message == "Estimated scan 1.2 MB of 10.0 GB allowed"


def test_scan_over_budget_is_blocked_before_execution(fake: FakeSnowflake) -> None:
    fake.bytes_assigned = 2_500_000_000_000

    with pytest.raises(ScanBudgetError, match=r"estimates 2\.5 TB scanned, above .* 1\.0 GB"):
        check_scan_budget(_connection(), "select 1", 1_000_000_000)
    assert fake.statements == ["EXPLAIN USING JSON select 1"]


def test_guard_fails_closed_when_snowflake_cannot_estimate(fake: FakeSnowflake) -> None:
    fake.bytes_assigned = None

    with pytest.raises(ScanBudgetError, match="could not estimate"):
        check_scan_budget(_connection(), "select 1", 1_000_000_000)


def test_runtime_records_the_budget_check_and_tags_the_query() -> None:
    manifest = Manifest.load(EXAMPLE / "target" / "manifest.json")
    runtime = make_runtime(manifest)
    runtime.agent = replace(
        runtime.agent, source=replace(runtime.agent.source, max_scan_bytes=1_000_000)
    )
    tags: list[str | None] = []
    execute = runtime.connection.execute

    def spy(sql: str, timeout_seconds: int, *, query_tag: str | None = None) -> Any:
        tags.append(query_tag)
        return execute(sql, timeout_seconds, query_tag=query_tag)

    runtime.connection.execute = spy  # type: ignore[method-assign]

    trace = runtime.answer(QUESTION)

    [budget] = [check for check in trace.verification if check.name == "scan_budget"]
    assert budget.passed and "budget not applied" in budget.message  # Local DuckDB.
    assert tags == [query_tag(trace.agent, trace.inference_fingerprint)]


def test_max_scan_bytes_is_part_of_the_agent_and_validated(tmp_path: Any) -> None:
    path = tmp_path / "agent.yaml"
    body = "schema_version: tabletalk.agent/v1\nname: revenue\ndescription: Revenue\nselect: [a]\n"
    path.write_text(body + "max_scan_bytes: 10_000_000_000\n")
    agent = Agent.load(path)

    assert agent.max_scan_bytes == 10_000_000_000
    assert "max_scan_bytes: 10000000000" in agent.dump()
    assert agent.digest != replace(agent, max_scan_bytes=None).digest
    path.write_text(body + "max_scan_bytes: 10GB\n")
    with pytest.raises(AgentError, match="positive integer number of bytes"):
        Agent.load(path)


@pytest.mark.parametrize(
    ("size", "text"),
    [
        (0, "0 B"),
        (999, "999 B"),
        (1_000, "1.0 KB"),
        (1_234_567, "1.2 MB"),
        (5 * 10**15, "5000.0 TB"),
    ],
)
def test_format_bytes_uses_decimal_units(size: int, text: str) -> None:
    assert format_bytes(size) == text
