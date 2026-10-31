"""scripts/snowflake_check.py runs end to end against a fake Snowflake connector."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from tabletalk.tests.test_plan import project_dir

__all__ = ["project_dir"]  # Re-exported fixture.

SCRIPT = Path(__file__).parents[2] / "scripts" / "snowflake_check.py"


class FakeSession:
    """Keeps session parameters and query history like a Snowflake session would."""

    def __init__(self, bytes_assigned: int | None) -> None:
        self.bytes_assigned = bytes_assigned
        self.parameters = {"QUERY_TAG": "", "STATEMENT_TIMEOUT_IN_SECONDS": "0"}
        self.history: list[dict[str, Any]] = []
        self.description: list[tuple[str]] = []
        self._rows: list[tuple[Any, ...]] = []

    def cursor(self) -> FakeSession:
        return self

    def execute(self, sql: str, params: object = None) -> FakeSession:
        if sql.startswith("ALTER SESSION SET "):
            tag = re.search(r"QUERY_TAG = '((?:[^'\\]|\\.)*)'", sql)
            timeout = re.search(r"STATEMENT_TIMEOUT_IN_SECONDS = (\d+)", sql)
            assert tag and timeout
            self.parameters["QUERY_TAG"] = tag.group(1).replace("\\'", "'")
            self.parameters["STATEMENT_TIMEOUT_IN_SECONDS"] = timeout.group(1)
            self.description, self._rows = [], []
        elif sql.startswith("EXPLAIN USING JSON "):
            plan = {"GlobalStats": {"bytesAssigned": self.bytes_assigned}}
            self.description = [("content",)]
            self._rows = [] if self.bytes_assigned is None else [(json.dumps(plan),)]
        elif sql.startswith("show parameters like"):
            name = sql.split("'")[1]
            self.description = [("key",), ("value",)]
            self._rows = [(name, self.parameters[name])]
        elif "query_history_by_session" in sql:
            self.description = [("QUERY_TEXT",), ("QUERY_TAG",)]
            self._rows = [(item["text"], item["tag"]) for item in reversed(self.history)]
        else:
            self.description = [("TABLETALK_HEALTH",)]
            self._rows = [(1,)]
        self.history.append({"text": sql, "tag": self.parameters["QUERY_TAG"]})
        return self

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


def _run(
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    bytes_assigned: int | None,
) -> tuple[int, str]:
    session = FakeSession(bytes_assigned)
    connector = types.ModuleType("snowflake.connector")
    connector.connect = lambda **kwargs: session  # type: ignore[attr-defined]
    package = types.ModuleType("snowflake")
    package.connector = connector  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "snowflake", package)
    monkeypatch.setitem(sys.modules, "snowflake.connector", connector)
    (project_dir / "profiles.yml").write_text(
        "analytics:\n  target: dev\n  outputs:\n    dev:\n      type: snowflake\n"
        "      account: acme\n      user: analyst\n      authenticator: externalbrowser\n"
        "      warehouse: analytics_wh\n      database: analytics\n      schema: marts\n"
    )
    spec = importlib.util.spec_from_file_location("snowflake_check", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", ["snowflake_check", "--project-folder", str(project_dir)])
    code = module.main()
    return code, capsys.readouterr().out


def test_all_four_checks_pass_on_a_well_behaved_account(
    project_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, output = _run(project_dir, monkeypatch, capsys, 1_200_000)

    assert code == 0, output
    assert [line.split()[0] for line in output.splitlines()] == ["PASS"] * 4
    assert "explain: fct_orders: 1.2 MB" in output
    assert "timeout: session value 37" in output


def test_a_missing_plan_estimate_fails_only_that_check(
    project_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, output = _run(project_dir, monkeypatch, capsys, None)

    assert code == 1
    assert [line.split()[0] for line in output.splitlines()] == ["PASS", "FAIL", "PASS", "PASS"]
