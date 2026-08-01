from __future__ import annotations

import json
import sqlite3
import sys
import types
from pathlib import Path

import pytest

from tabletalk.connections import ReadOnlyConnection, Target, load_profile_target
from tabletalk.manifest import Manifest
from tabletalk.providers.snowflake_provider import SnowflakeProvider
from tabletalk.providers.sqlite_provider import SQLiteProvider


def test_sqlite_adapter_is_physically_read_only(tmp_path: Path) -> None:
    path = tmp_path / "warehouse.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("create table orders(id integer)")
    connection.execute("insert into orders values (1)")
    connection.commit()
    connection.close()
    provider = SQLiteProvider(str(path), read_only=True)
    adapter = ReadOnlyConnection(
        Target("analytics", "dev", "sqlite", {"database_path": str(path)}), provider
    )
    assert adapter.execute("select count(*) as count from orders", 5) == ({"count": 1},)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        provider.execute_query("delete from orders")


def test_dbt_profile_target_resolves_env_vars_without_persisting_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "dbt_project.yml").write_text("name: analytics\nprofile: analytics\n")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "profiles.yml").write_text(
        "analytics:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n"
        "      path: \"{{ env_var('WAREHOUSE_PATH') }}\"\n"
    )
    monkeypatch.setenv("WAREHOUSE_PATH", "data/analytics.duckdb")
    target = load_profile_target(tmp_path, None, profiles)
    assert target.adapter == "duckdb"
    assert target.config["read_only"] is True
    assert target.config["database_path"] == str(tmp_path / "data" / "analytics.duckdb")


def test_snowflake_connector_contract_uses_mapping_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    class Cursor:
        description = [("REVENUE",)]

        def execute(self, sql: str, params=None):
            assert sql == "select 184.25 as revenue"
            return self

        def fetchall(self):
            return [(184.25,)]

    class Connection:
        def cursor(self):
            return Cursor()

    connector = types.ModuleType("snowflake.connector")
    connector.connect = lambda **kwargs: Connection()  # type: ignore[attr-defined]
    package = types.ModuleType("snowflake")
    package.connector = connector  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "snowflake", package)
    monkeypatch.setitem(sys.modules, "snowflake.connector", connector)
    provider = SnowflakeProvider("account", "user", "password", "db", "warehouse")
    assert provider.execute_query("select 184.25 as revenue") == [{"REVENUE": 184.25}]


def test_optional_catalog_and_run_results_only_enrich_metadata(tmp_path: Path) -> None:
    example = Path(__file__).parents[2] / "examples" / "dbt-analytics" / "target"
    manifest_payload = json.loads((example / "manifest.json").read_text())
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_payload))
    catalog = {
        "nodes": {
            "model.analytics.fct_orders": {
                "columns": {
                    "recognized_revenue": {
                        "name": "recognized_revenue",
                        "type": "DECIMAL(18,2)",
                    }
                }
            }
        }
    }
    (tmp_path / "catalog.json").write_text(json.dumps(catalog))
    test_id = next(
        uid
        for uid in manifest_payload["nodes"]
        if uid.startswith("test.analytics.not_null_fct_orders_order_date")
    )
    (tmp_path / "run_results.json").write_text(
        json.dumps({"results": [{"unique_id": test_id, "status": "pass"}]})
    )
    index = Manifest.load(manifest_path)
    node = index.nodes["model.analytics.fct_orders"]
    assert node.columns["recognized_revenue"].data_type == "decimal(18,2)"
    assert any(test.status == "pass" for test in node.tests)
    without_enrichment = Manifest(manifest_path, manifest_payload)
    assert {node.unique_id for node in index.queryable_nodes} == {
        node.unique_id for node in without_enrichment.queryable_nodes
    }
