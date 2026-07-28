"""
router.py — LLM complexity-based router (item 29).

Routes queries to a fast/cheap model or a powerful/expensive model based on
estimated query complexity.  Complexity is scored by a lightweight heuristic
(question length, keyword signals) so no extra LLM call is needed.

Configure in tabletalk.yaml:

  llm:
    provider: openai
    model: gpt-4o             # default / complex model
    fast_model: gpt-4o-mini   # cheap model for simple queries
    router:
      enabled: true
      threshold: 0.5          # 0–1; queries above this go to the powerful model

Usage (internal — called by QuerySession):
    from tabletalk.router import score_complexity, route_model

    score = score_complexity("total revenue by month")   # → 0.2 (simple)
    model = route_model(config, score)                    # → "gpt-4o-mini"
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

# ── Complexity signals ────────────────────────────────────────────────────────

# Keywords that suggest a complex analytical query requiring a powerful model
_COMPLEX_KEYWORDS = frozenset(
    [
        "year-over-year",
        "month-over-month",
        "cohort",
        "funnel",
        "retention",
        "percentile",
        "median",
        "stddev",
        "variance",
        "rolling",
        "window",
        "rank",
        "dense_rank",
        "ntile",
        "lead",
        "lag",
        "pivot",
        "unpivot",
        "recursive",
        "cte",
        "subquery",
        "lateral",
        "unnest",
        "cross join",
        "self-join",
        "anti-join",
        "semi-join",
        "multiple tables",
        "join.*join",
        "forecast",
        "prediction",
        "anomaly",
        "outlier",
    ]
)

# Keywords that strongly suggest a simple lookup / aggregation
_SIMPLE_KEYWORDS = frozenset(
    [
        "count",
        "total",
        "sum",
        "average",
        "how many",
        "list",
        "show me",
        "top 10",
        "top 5",
        "latest",
        "recent",
        "last 7 days",
        "last 30 days",
        "yesterday",
        "today",
    ]
)


def score_complexity(question: str) -> float:
    """
    Return a complexity score in [0, 1] for the given natural-language question.
    0 = very simple, 1 = very complex.

    Heuristics used:
    - Length of question (longer → more complex)
    - Number of complex keyword matches
    - Penalty for simple keyword matches
    - Number of tables / joins implied (by comma-separated noun phrases)
    """
    q = question.lower()
    score = 0.0

    # Length heuristic: normalize to 0–0.3
    length_score = min(len(question) / 400, 0.3)
    score += length_score

    # Complex keyword hits (each hit adds 0.15, capped at 0.6)
    complex_hits = sum(1 for kw in _COMPLEX_KEYWORDS if re.search(kw, q))
    score += min(complex_hits * 0.15, 0.6)

    # Simple keyword hits reduce the score (each hit subtracts 0.1)
    simple_hits = sum(1 for kw in _SIMPLE_KEYWORDS if kw in q)
    score -= min(simple_hits * 0.1, 0.3)

    # Number of "and"s / commas as proxy for multi-dimensional queries
    conjunction_count = q.count(" and ") + q.count(",")
    score += min(conjunction_count * 0.05, 0.2)

    return max(0.0, min(1.0, score))


def route_model(
    llm_config: Dict[str, Any],
    complexity_score: float,
) -> str:
    """
    Return the model name to use for a query with the given complexity score.

    Decision logic:
    - If routing is disabled (or not configured): return the default model.
    - If score >= threshold: use the default (powerful) model.
    - Otherwise: use fast_model if configured, else fall back to default model.
    """
    router_cfg = llm_config.get("router", {})
    if not router_cfg.get("enabled", False):
        return llm_config.get("model", "")

    threshold: float = float(router_cfg.get("threshold", 0.5))
    default_model: str = llm_config.get("model", "")
    fast_model: str = llm_config.get("fast_model", default_model)

    if complexity_score >= threshold:
        return default_model
    return fast_model


def explain_routing(
    question: str,
    llm_config: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Return a dict describing the routing decision for debugging / observability.

    Example:
        {
            "question": "total revenue by month",
            "complexity_score": 0.18,
            "threshold": 0.5,
            "routed_model": "gpt-4o-mini",
            "fast_model": "gpt-4o-mini",
            "default_model": "gpt-4o",
            "routing_enabled": true
        }
    """
    score = score_complexity(question)
    model = route_model(llm_config, score)
    router_cfg = llm_config.get("router", {})
    return {
        "question": question,
        "complexity_score": round(score, 4),
        "threshold": float(router_cfg.get("threshold", 0.5)),
        "routed_model": model,
        "fast_model": llm_config.get("fast_model", llm_config.get("model", "")),
        "default_model": llm_config.get("model", ""),
        "routing_enabled": bool(router_cfg.get("enabled", False)),
    }
