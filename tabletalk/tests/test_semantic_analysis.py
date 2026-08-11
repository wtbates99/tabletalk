from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest

from tabletalk.imports import EvidenceImportError, ImportKind, preview_import
from tabletalk.manifest import Manifest
from tabletalk.schemas import content_digest
from tabletalk.semantic_analysis import (
    FindingCode,
    analyze_semantics,
    conflicting_statements,
)
from tabletalk.semantics import EvidenceKind, EvidenceRef, SemanticStatement, SemanticStatus

EXAMPLE = Path(__file__).parents[2] / "examples" / "dbt-analytics"


@pytest.fixture
def manifest() -> Manifest:
    return Manifest.load(EXAMPLE / "target" / "manifest.json")


def test_analysis_reports_exact_quality_and_prioritizes_questions(manifest: Manifest) -> None:
    node = manifest.nodes["model.analytics.fct_orders"]
    columns = dict(node.columns)
    columns["customer_id"] = replace(
        columns["customer_id"],
        description="Sensitive customer identity used for support.",
        meta={},
    )
    columns["recognized_at"] = replace(columns["order_date"], name="recognized_at")
    scoped = replace(node, columns=columns)
    analysis = analyze_semantics(manifest, (scoped,))
    assert analysis.quality.resources == 1
    assert analysis.quality.columns == len(columns)
    codes = {item.code for item in analysis.gaps}
    assert FindingCode.SUSPECTED_SENSITIVE_DATA in codes
    assert FindingCode.AMBIGUOUS_TIME_BASIS in codes
    questions = analysis.high_value_questions()
    assert len(questions) <= 3
    assert questions == tuple(sorted(questions, key=lambda item: (-item.priority, item.id)))


def test_manifest_normalizes_semantic_resources(tmp_path: Path, manifest: Manifest) -> None:
    payload = dict(manifest.payload)
    payload["metrics"] = {
        "metric.analytics.recognized_revenue": {
            "name": "recognized_revenue",
            "label": "Recognized revenue",
            "description": "Revenue at recognition time.",
            "type": "simple",
            "type_params": {"measure": {"name": "recognized_revenue"}},
            "depends_on": {"nodes": ["model.analytics.fct_orders"]},
        }
    }
    payload["semantic_models"] = {
        "semantic_model.analytics.orders": {
            "name": "orders",
            "description": "Order semantics.",
            "depends_on": {"nodes": ["model.analytics.fct_orders"]},
            "entities": [{"name": "order", "type": "primary", "expr": "order_id"}],
            "dimensions": [{"name": "order_date", "type": "time"}],
            "measures": [{"name": "recognized_revenue", "agg": "sum"}],
        }
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload))
    enriched = Manifest.load(path)
    assert enriched.metrics[0].name == "recognized_revenue"
    assert enriched.semantic_models[0].model_node == "model.analytics.fct_orders"
    analysis = analyze_semantics(enriched, (enriched.nodes["model.analytics.fct_orders"],))
    assert analysis.quality.metrics == 1
    assert analysis.quality.semantic_models == 1


def _source(kind: EvidenceKind, name: str) -> EvidenceRef:
    return EvidenceRef(kind, name, content_digest(name), "/")


def test_conflicting_approved_meanings_are_explicit() -> None:
    first = SemanticStatement(
        "recognized_revenue",
        SemanticStatus.TRUSTED,
        "Use the order date.",
        time_column="order_date",
        evidence=(_source(EvidenceKind.DBT_ARTIFACT, "manifest"),),
    )
    second = SemanticStatement(
        "recognized_revenue",
        SemanticStatus.TRUSTED,
        "Use the recognition date.",
        time_column="recognized_at",
        evidence=(_source(EvidenceKind.APPROVED_SQL, "revenue.sql"),),
    )
    conflicts = conflicting_statements((first, second))
    assert len(conflicts) == 1
    assert conflicts[0].status is SemanticStatus.CONFLICTING
    assert conflicts[0].can_support_answer is False


def test_sql_import_is_safe_attributed_untrusted_and_not_executed(
    tmp_path: Path, manifest: Manifest
) -> None:
    path = tmp_path / "revenue.sql"
    path.write_text(
        "select sum(recognized_revenue) as revenue "
        "from {{ ref('fct_orders') }} where order_date >= '2026-07-01'"
    )
    preview = preview_import(path, ImportKind.SQL, manifest, dialect="duckdb")[0]
    assert preview.status is SemanticStatus.INFERRED
    assert preview.executed is False
    assert preview.resource_ids == ("model.analytics.fct_orders",)
    assert preview.evidence().kind is EvidenceKind.SQL_IMPORT
    assert preview.evidence(approved=True).kind is EvidenceKind.APPROVED_SQL
    assert "2026-07-01" not in preview.redacted_sql
    assert preview.literal_count == 1


def test_import_rejects_writes_and_multiple_statements(tmp_path: Path, manifest: Manifest) -> None:
    path = tmp_path / "unsafe.sql"
    path.write_text("delete from analytics.main.fct_orders")
    with pytest.raises(EvidenceImportError, match="not safe"):
        preview_import(path, ImportKind.SQL, manifest, dialect="duckdb")
    path.write_text("select * from analytics.main.fct_orders; select 1")
    with pytest.raises(EvidenceImportError, match="Exactly one"):
        preview_import(path, ImportKind.SQL, manifest, dialect="duckdb")


def test_bi_and_query_history_imports_require_explicit_files(
    tmp_path: Path, manifest: Manifest
) -> None:
    bi = tmp_path / "bi.json"
    bi.write_text(
        json.dumps(
            {
                "queries": [
                    {
                        "name": "Revenue",
                        "sql": "select recognized_revenue from analytics.main.fct_orders",
                    }
                ]
            }
        )
    )
    assert preview_import(bi, ImportKind.BI_QUERY, manifest, dialect="duckdb")[0].kind is ImportKind.BI_QUERY

    history = tmp_path / "history.csv"
    with history.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["query_text"])
        writer.writeheader()
        writer.writerow(
            {"query_text": "select order_date from analytics.main.fct_orders"}
        )
    previews = preview_import(history, ImportKind.QUERY_HISTORY, manifest, dialect="duckdb")
    assert len(previews) == 1
    assert previews[0].locator == "/0/query_text"
