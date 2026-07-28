from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml

from tabletalk.agents import AgentDefinition
from tabletalk.evals import load_eval_suite
from tabletalk.utils import initialize_project


def test_initialize_project_creates_a_complete_free_starter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    initialize_project()

    config = yaml.safe_load((tmp_path / "tabletalk.yaml").read_text())
    assert config["llm"]["provider"] == "ollama"
    assert config["llm"]["model"] == "gemma4:31b-cloud"
    assert config["connections"]["local"]["read_only"] is True
    assert (tmp_path / "data" / "starter.db").is_file()
    assert AgentDefinition.load(tmp_path / "agents" / "sales.yaml").name == "sales"
    assert (
        load_eval_suite(tmp_path / "evals" / "starter.yaml").name
        == "starter-regression"
    )


def test_initialize_project_seeds_the_documented_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    initialize_project()

    connection = sqlite3.connect(tmp_path / "data" / "starter.db")
    try:
        value = connection.execute(
            """
            SELECT SUM(recognized_revenue)
            FROM orders
            WHERE order_date >= '2026-01-01'
              AND order_date < '2026-02-01'
              AND status != 'cancelled'
            """
        ).fetchone()[0]
    finally:
        connection.close()

    assert value == 200


def test_initialize_project_is_idempotent(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    initialize_project()
    original = (tmp_path / "tabletalk.yaml").read_bytes()

    initialize_project()

    assert (tmp_path / "tabletalk.yaml").read_bytes() == original
    assert "Already initialized" in capsys.readouterr().out
