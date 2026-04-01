"""Tests for tabletalk/router.py"""
from __future__ import annotations

import pytest

from tabletalk.router import explain_routing, route_model, score_complexity


class TestScoreComplexity:
    def test_simple_question_low_score(self):
        score = score_complexity("How many orders are there?")
        assert score < 0.5

    def test_complex_question_high_score(self):
        score = score_complexity(
            "Calculate year-over-year revenue growth using a recursive CTE "
            "with window functions, ranking by percentile across multiple regions"
        )
        assert score > 0.3

    def test_empty_question_zero_score(self):
        assert score_complexity("") == 0.0

    def test_score_in_range(self):
        for q in ["total revenue", "year-over-year cohort analysis with lag and lead"]:
            s = score_complexity(q)
            assert 0.0 <= s <= 1.0

    def test_simple_keywords_reduce_score(self):
        # A question with only "list" and "show me" should score lower than a complex one
        simple_score = score_complexity("Show me the top 5 customers")
        complex_score = score_complexity("Compute cohort retention with rolling window functions")
        assert simple_score < complex_score

    def test_long_question_scores_higher_than_short(self):
        short = score_complexity("total")
        long = score_complexity(
            "What is the total revenue trend analysis including year-over-year "
            "percentage changes by region and product category for the last three years?"
        )
        assert long > short


class TestRouteModel:
    def _cfg(self, model="gpt-4o", fast_model="gpt-4o-mini", enabled=True, threshold=0.5):
        return {
            "model": model,
            "fast_model": fast_model,
            "router": {"enabled": enabled, "threshold": threshold},
        }

    def test_routing_disabled_returns_default(self):
        cfg = self._cfg(enabled=False)
        assert route_model(cfg, 0.1) == "gpt-4o"

    def test_low_complexity_routes_to_fast(self):
        cfg = self._cfg()
        assert route_model(cfg, 0.1) == "gpt-4o-mini"

    def test_high_complexity_routes_to_default(self):
        cfg = self._cfg()
        assert route_model(cfg, 0.9) == "gpt-4o"

    def test_at_threshold_uses_default(self):
        cfg = self._cfg(threshold=0.5)
        assert route_model(cfg, 0.5) == "gpt-4o"

    def test_no_fast_model_falls_back_to_default(self):
        cfg = {"model": "gpt-4o", "router": {"enabled": True, "threshold": 0.5}}
        assert route_model(cfg, 0.1) == "gpt-4o"

    def test_no_router_config_returns_default(self):
        assert route_model({"model": "gpt-4o"}, 0.1) == "gpt-4o"


class TestExplainRouting:
    def test_returns_all_fields(self):
        cfg = {
            "model": "gpt-4o",
            "fast_model": "gpt-4o-mini",
            "router": {"enabled": True, "threshold": 0.5},
        }
        result = explain_routing("count orders", cfg)
        assert "question" in result
        assert "complexity_score" in result
        assert "threshold" in result
        assert "routed_model" in result
        assert "fast_model" in result
        assert "default_model" in result
        assert "routing_enabled" in result

    def test_routing_enabled_true(self):
        cfg = {"model": "gpt-4o", "router": {"enabled": True}}
        result = explain_routing("x", cfg)
        assert result["routing_enabled"] is True

    def test_routing_disabled(self):
        cfg = {"model": "gpt-4o", "router": {"enabled": False}}
        result = explain_routing("x", cfg)
        assert result["routing_enabled"] is False

    def test_complexity_score_rounded(self):
        cfg = {"model": "gpt-4o", "router": {"enabled": False}}
        result = explain_routing("x", cfg)
        # complexity_score should be rounded to 4 decimal places
        assert isinstance(result["complexity_score"], float)
        assert len(str(result["complexity_score"]).split(".")[-1]) <= 4
