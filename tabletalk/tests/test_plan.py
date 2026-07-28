from __future__ import annotations

from tabletalk.compiler import semantic_changes


def test_semantic_plan_is_deterministic() -> None:
    before = {
        "description": "Sales",
        "relations": [{"name": "main.orders", "columns": []}],
        "metrics": [{"name": "revenue", "expression": "sum(total)"}],
    }
    after = {
        "description": "Trusted sales",
        "relations": [
            {"name": "main.customers", "columns": []},
            {"name": "main.orders", "columns": []},
        ],
        "metrics": [{"name": "revenue", "expression": "sum(net_total)"}],
    }

    assert semantic_changes(before, after) == (
        "change description",
        "add relation main.customers",
        "change metric revenue",
    )


def test_semantic_plan_ignores_mapping_order() -> None:
    before = {"name": "sales", "policies": [["read_only", True]]}
    after = {"policies": [["read_only", True]], "name": "sales"}

    assert semantic_changes(before, after) == ()
