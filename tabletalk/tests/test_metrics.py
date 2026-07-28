"""Tests for tabletalk/metrics.py"""
from __future__ import annotations

import time

import pytest

from tabletalk.metrics import MetricsRegistry, timer


class TestCounters:
    def test_initial_zero(self):
        reg = MetricsRegistry()
        assert reg.counter("foo") == 0.0

    def test_inc_by_one(self):
        reg = MetricsRegistry()
        reg.inc("foo")
        assert reg.counter("foo") == 1.0

    def test_inc_by_value(self):
        reg = MetricsRegistry()
        reg.inc("foo", 5)
        assert reg.counter("foo") == 5.0

    def test_accumulates(self):
        reg = MetricsRegistry()
        reg.inc("foo", 3)
        reg.inc("foo", 2)
        assert reg.counter("foo") == 5.0


class TestGauges:
    def test_initial_zero(self):
        reg = MetricsRegistry()
        assert reg.gauge("g") == 0.0

    def test_set_gauge(self):
        reg = MetricsRegistry()
        reg.set_gauge("g", 42.5)
        assert reg.gauge("g") == 42.5

    def test_inc_gauge(self):
        reg = MetricsRegistry()
        reg.set_gauge("g", 10)
        reg.inc_gauge("g", 5)
        assert reg.gauge("g") == 15.0

    def test_dec_gauge(self):
        reg = MetricsRegistry()
        reg.set_gauge("g", 10)
        reg.dec_gauge("g", 3)
        assert reg.gauge("g") == 7.0


class TestHistograms:
    def test_observe_increases_count(self):
        reg = MetricsRegistry()
        reg.observe("latency", 0.1)
        reg.observe("latency", 0.2)
        h = reg.histogram_summary("latency")
        assert h is not None
        assert h["count"] == 2

    def test_observe_accumulates_sum(self):
        reg = MetricsRegistry()
        reg.observe("latency", 0.1)
        reg.observe("latency", 0.2)
        h = reg.histogram_summary("latency")
        assert abs(h["sum"] - 0.3) < 1e-9

    def test_unknown_histogram_returns_none(self):
        reg = MetricsRegistry()
        assert reg.histogram_summary("unknown") is None


class TestSnapshot:
    def test_snapshot_contains_all_types(self):
        reg = MetricsRegistry()
        reg.inc("c1", 5)
        reg.set_gauge("g1", 10)
        reg.observe("h1", 0.5)
        snap = reg.snapshot()
        assert "counters" in snap
        assert "gauges" in snap
        assert "histograms" in snap
        assert snap["counters"]["c1"] == 5.0
        assert snap["gauges"]["g1"] == 10.0
        assert snap["histograms"]["h1"]["count"] == 1

    def test_histogram_avg_in_snapshot(self):
        reg = MetricsRegistry()
        reg.observe("h", 0.4)
        reg.observe("h", 0.6)
        snap = reg.snapshot()
        assert abs(snap["histograms"]["h"]["avg"] - 0.5) < 1e-9


class TestPrometheusFormat:
    def test_counter_format(self):
        reg = MetricsRegistry()
        reg.inc("my_counter", 3, help_text="A counter")
        output = reg.format_prometheus()
        assert "# HELP my_counter A counter" in output
        assert "# TYPE my_counter counter" in output
        assert "my_counter_total 3.0" in output

    def test_gauge_format(self):
        reg = MetricsRegistry()
        reg.set_gauge("my_gauge", 42, help_text="A gauge")
        output = reg.format_prometheus()
        assert "# TYPE my_gauge gauge" in output
        assert "my_gauge 42" in output

    def test_histogram_format(self):
        reg = MetricsRegistry()
        reg.observe("my_hist", 0.1)
        output = reg.format_prometheus()
        assert "# TYPE my_hist histogram" in output
        assert "my_hist_sum" in output
        assert "my_hist_count" in output
        assert 'le="+Inf"' in output

    def test_empty_registry_empty_output(self):
        reg = MetricsRegistry()
        assert reg.format_prometheus() == ""


class TestReset:
    def test_reset_clears_all(self):
        reg = MetricsRegistry()
        reg.inc("c", 5)
        reg.set_gauge("g", 10)
        reg.observe("h", 1.0)
        reg.reset()
        assert reg.counter("c") == 0.0
        assert reg.gauge("g") == 0.0
        assert reg.histogram_summary("h") is None


class TestTimer:
    def test_timer_records_observation(self):
        reg = MetricsRegistry()
        with timer("elapsed", registry=reg):
            time.sleep(0.01)
        h = reg.histogram_summary("elapsed")
        assert h is not None
        assert h["count"] == 1
        assert h["sum"] >= 0.01
