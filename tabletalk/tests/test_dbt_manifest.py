"""Tests for native dbt manifest context enrichment."""

from __future__ import annotations

import json

import pytest

from tabletalk.dbt_manifest import DbtManifest


@pytest.fixture
def dbt_payload():
    return {
        "metadata": {"dbt_version": "1.10.0"},
        "nodes": {
            "model.shop.fct_orders": {
                "resource_type": "model",
                "name": "fct_orders",
                "alias": "fct_orders",
                "database": "analytics",
                "schema": "main",
                "relation_name": '"analytics"."main"."fct_orders"',
                "description": "One row per order with trusted revenue metrics.",
                "columns": {
                    "order_id": {
                        "name": "order_id",
                        "description": "Stable business key for an order.",
                    },
                    "net_revenue": {
                        "name": "net_revenue",
                        "description": "Revenue after discounts and refunds.",
                    },
                },
                "depends_on": {"nodes": ["source.shop.orders"]},
            },
            "test.shop.unique_fct_orders_order_id": {
                "resource_type": "test",
                "depends_on": {"nodes": ["model.shop.fct_orders"]},
                "test_metadata": {
                    "name": "unique",
                    "kwargs": {"column_name": "order_id"},
                },
            },
        },
        "sources": {
            "source.shop.orders": {
                "resource_type": "source",
                "name": "orders",
                "identifier": "orders",
                "database": "analytics",
                "schema": "raw",
                "relation_name": '"analytics"."raw"."orders"',
                "description": "Raw order events.",
                "columns": {},
                "depends_on": {"nodes": []},
            }
        },
    }


def test_loads_model_semantics_lineage_and_tests(tmp_path, dbt_payload):
    target = tmp_path / "target"
    target.mkdir()
    (target / "manifest.json").write_text(json.dumps(dbt_payload))

    manifest = DbtManifest.load(tmp_path, {"manifest": "target/manifest.json"})
    relation = manifest.relation("main", "fct_orders")

    assert relation is not None
    assert relation.description == "One row per order with trusted revenue metrics."
    assert relation.columns["net_revenue"] == "Revenue after discounts and refunds."
    assert relation.depends_on == ["source.shop.orders"]
    assert relation.tests == ["unique(order_id)"]
    assert "DBT_LINEAGE:" in "\n".join(relation.prompt_lines("main.fct_orders"))


def test_matches_sources_by_schema_and_identifier(tmp_path, dbt_payload):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(dbt_payload))
    manifest = DbtManifest.load(tmp_path, "manifest.json")

    relation = manifest.relation("raw", "orders")

    assert relation is not None
    assert relation.unique_id == "source.shop.orders"


def test_missing_configured_manifest_stops_context_compilation(tmp_path):
    with pytest.raises(FileNotFoundError, match="dbt compile"):
        DbtManifest.load(tmp_path, {"manifest": "target/manifest.json"})


def test_invalid_dbt_configuration_is_rejected(tmp_path):
    with pytest.raises(FileNotFoundError, match="dbt compile"):
        DbtManifest.load(tmp_path, {})


def test_project_and_target_directory_configuration(tmp_path, dbt_payload):
    target = tmp_path / "analytics" / "target"
    target.mkdir(parents=True)
    (target / "manifest.json").write_text(json.dumps(dbt_payload))

    manifest = DbtManifest.load(
        tmp_path,
        {"project_dir": "analytics", "target_dir": "target"},
    )

    assert manifest.relation("main", "fct_orders") is not None
