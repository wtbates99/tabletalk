from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tabletalk.agents import Agent
from tabletalk.manifest import Manifest, Node
from tabletalk.runtime import Runtime
from tabletalk.semantic_guard import SemanticGuard, SemanticSupportError
from tabletalk.semantics import SemanticStatement, SemanticStatus
from tabletalk.traces import Interpretation

EXAMPLE = Path(__file__).parents[2] / "examples" / "dbt-analytics"


def guard(
    *,
    statements: tuple[SemanticStatement, ...] = (),
) -> tuple[SemanticGuard, Node]:
    manifest = Manifest.load(EXAMPLE / "target" / "manifest.json")
    agent = Agent("revenue", "Revenue", ("group:finance",)).resolve(manifest)
    return SemanticGuard(agent, statements), manifest.nodes["model.analytics.fct_orders"]


def test_missing_column_meaning_blocks_an_answer() -> None:
    semantic_guard, raw_node = guard()
    node = raw_node
    columns = dict(node.columns)
    columns["recognized_revenue"] = replace(
        columns["recognized_revenue"],
        description="",
    )
    node = replace(node, columns=columns)
    with pytest.raises(SemanticSupportError, match="no trusted business description"):
        semantic_guard.validate(
            "What is recognized revenue?",
            Interpretation(intent="aggregate", metrics=("recognized_revenue",)),
            (node,),
            ("recognized_revenue",),
        )


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (SemanticStatus.INFERRED, "inferred and untrusted"),
        (SemanticStatus.MISSING, "missing business meaning"),
        (SemanticStatus.CONFLICTING, "conflicting approved meanings"),
    ],
)
def test_untrusted_semantic_status_blocks_an_answer(
    status: SemanticStatus,
    message: str,
) -> None:
    statement = SemanticStatement("recognized_revenue", status)
    semantic_guard, node = guard(statements=(statement,))
    with pytest.raises(SemanticSupportError, match=message):
        semantic_guard.validate(
            "What is recognized revenue?",
            Interpretation(intent="aggregate", metrics=("recognized_revenue",)),
            (node,),
            ("recognized_revenue",),
        )


def test_ambiguous_time_basis_requires_a_named_or_approved_field() -> None:
    semantic_guard, raw_node = guard()
    node = raw_node
    columns = dict(node.columns)
    columns["booked_date"] = replace(columns["order_date"], name="booked_date")
    node = replace(node, columns=columns)
    with pytest.raises(SemanticSupportError, match="time basis is ambiguous"):
        semantic_guard.validate(
            "Show recognized revenue by month.",
            Interpretation(intent="aggregate", metrics=("recognized_revenue",)),
            (node,),
            ("order_date", "recognized_revenue"),
        )
    semantic_guard.validate(
        "Show recognized revenue by order date month.",
        Interpretation(intent="aggregate", metrics=("recognized_revenue",)),
        (node,),
        ("order_date", "recognized_revenue"),
    )


def test_ambiguous_measure_basis_cannot_be_resolved_by_model_interpretation() -> None:
    semantic_guard, node = guard()
    with pytest.raises(SemanticSupportError, match="revenue basis is ambiguous"):
        semantic_guard.validate(
            "What was revenue in July?",
            Interpretation(intent="aggregate", metrics=("recognized_revenue",)),
            (node,),
            ("order_date", "recognized_revenue"),
        )
    semantic_guard.validate(
        "What was recognized revenue by order date in July?",
        Interpretation(intent="aggregate", metrics=("recognized_revenue",)),
        (node,),
        ("order_date", "recognized_revenue"),
    )


def test_query_prompt_includes_only_trusted_semantic_statements() -> None:
    manifest = Manifest.load(EXAMPLE / "target" / "manifest.json")
    agent = Agent(
        "revenue",
        "Revenue",
        ("group:finance",),
        supported_questions=("Aggregate recognized revenue.",),
        unsupported_questions=("Customer identity is outside scope.",),
    ).resolve(manifest)
    runtime = Runtime(
        manifest,
        agent,
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        model_identity="test:none",
        semantic_statements=(
            SemanticStatement(
                "recognized_revenue",
                SemanticStatus.TRUSTED,
                "Recognized revenue excludes cancelled orders.",
                expression="sum(recognized_revenue)",
                time_column="order_date",
            ),
            SemanticStatement(
                "late_order",
                SemanticStatus.INFERRED,
                "Do not expose this model hypothesis.",
            ),
        ),
    )
    prompt = runtime._query_prompt()
    assert "Aggregate recognized revenue." in prompt
    assert "Customer identity is outside scope." in prompt
    assert "Recognized revenue excludes cancelled orders." in prompt
    assert "Expression: sum(recognized_revenue)" in prompt
    assert "Time column: order_date" in prompt
    assert "Do not expose this model hypothesis." not in prompt
