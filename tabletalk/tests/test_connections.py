from __future__ import annotations

import json
import sqlite3
import sys
import types
from pathlib import Path

import pytest

from tabletalk.connections import ReadOnlyConnection, Target, load_profile_target
from tabletalk.factories import get_db_provider
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
            if sql.startswith("ALTER SESSION SET QUERY_TAG"):
                return self  # Covered in test_scan_budget.py.
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
        if uid.startswith("test.analytics.not_null_fct_orders_order_id")
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


def _capture_connect(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    captured: dict[str, object] = {}

    def connect(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    connector = types.ModuleType("snowflake.connector")
    connector.connect = connect  # type: ignore[attr-defined]
    package = types.ModuleType("snowflake")
    package.connector = connector  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "snowflake", package)
    monkeypatch.setitem(sys.modules, "snowflake.connector", connector)
    return captured


def _snowflake_target(tmp_path: Path, auth: str) -> dict[str, object]:
    (tmp_path / "dbt_project.yml").write_text("name: analytics\nprofile: analytics\n")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "profiles.yml").write_text(
        "analytics:\n  target: dev\n  outputs:\n    dev:\n      type: snowflake\n"
        "      account: acme\n      user: analyst\n      role: reporter\n"
        "      warehouse: analytics_wh\n      database: analytics\n      schema: marts\n" + auth
    )
    return load_profile_target(tmp_path, None, profiles).config


def test_sso_authenticator_connects_without_a_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_connect(monkeypatch)
    config = _snowflake_target(tmp_path, "      authenticator: externalbrowser\n")

    get_db_provider(config)

    assert captured["authenticator"] == "externalbrowser"
    assert "password" not in captured


def test_key_pair_maps_dbt_fields_to_the_connector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_connect(monkeypatch)
    monkeypatch.setenv("SNOWFLAKE_KEY_PASSPHRASE", "secret")
    config = _snowflake_target(
        tmp_path,
        "      private_key_path: ~/.ssh/snowflake.p8\n"
        "      private_key_passphrase: \"{{ env_var('SNOWFLAKE_KEY_PASSPHRASE') }}\"\n",
    )

    get_db_provider(config)

    assert captured["authenticator"] == "SNOWFLAKE_JWT"
    assert captured["private_key_file"] == str(Path("~/.ssh/snowflake.p8").expanduser())
    assert captured["private_key_file_pwd"] == "secret"
    assert "password" not in captured


def test_snowflake_needs_some_credential() -> None:
    config = {
        "type": "snowflake",
        "account": "acme",
        "user": "analyst",
        "database": "analytics",
        "warehouse": "analytics_wh",
    }

    with pytest.raises(ValueError, match="password, authenticator, or private_key_path"):
        get_db_provider(config)


def test_credentials_never_reach_the_warehouse_identity(tmp_path: Path) -> None:
    target = Target(
        "analytics",
        "dev",
        "snowflake",
        {"account": "acme", "database": "analytics", "private_key_passphrase": "secret"},
    )

    assert "secret" not in target.identity
