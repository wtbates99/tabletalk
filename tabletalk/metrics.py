"""
metrics.py — Prometheus-compatible metrics collector (item 17).

Collects counters, gauges, and histograms for tabletalk operations.
Exposes them in Prometheus text format via format_prometheus().
No external dependency required for collection; prometheus-client is only
needed if you want to push to a Prometheus push-gateway.

Usage:
    from tabletalk.metrics import get_registry

    reg = get_registry()
    reg.inc("queries_total")
    reg.observe("generation_seconds", 1.234)

    print(reg.format_prometheus())
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional


class MetricsRegistry:
    """
    Lightweight in-process metrics registry.

    Counters : monotonically increasing integers.
    Gauges   : arbitrary float values (set / inc / dec).
    Histograms: tracks sum + count + configurable bucket counts.
    """

    _DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Dict[str, float] = defaultdict(float)
        self._gauges: Dict[str, float] = {}
        self._histograms: Dict[str, Dict[str, Any]] = {}
        self._meta: Dict[str, str] = {}  # name → help text

    # ── Counters ──────────────────────────────────────────────────────────────

    def inc(self, name: str, value: float = 1.0, help_text: str = "") -> None:
        with self._lock:
            self._counters[name] += value
            if help_text:
                self._meta[name] = help_text

    def counter(self, name: str) -> float:
        return self._counters.get(name, 0.0)

    # ── Gauges ────────────────────────────────────────────────────────────────

    def set_gauge(self, name: str, value: float, help_text: str = "") -> None:
        with self._lock:
            self._gauges[name] = value
            if help_text:
                self._meta[name] = help_text

    def inc_gauge(self, name: str, value: float = 1.0) -> None:
        with self._lock:
            self._gauges[name] = self._gauges.get(name, 0.0) + value

    def dec_gauge(self, name: str, value: float = 1.0) -> None:
        with self._lock:
            self._gauges[name] = self._gauges.get(name, 0.0) - value

    def gauge(self, name: str) -> float:
        return self._gauges.get(name, 0.0)

    # ── Histograms ────────────────────────────────────────────────────────────

    def observe(
        self,
        name: str,
        value: float,
        buckets: Optional[tuple] = None,
        help_text: str = "",
    ) -> None:
        """Record a single observation in a histogram."""
        bkts = buckets or self._DEFAULT_BUCKETS
        with self._lock:
            if name not in self._histograms:
                self._histograms[name] = {
                    "sum": 0.0,
                    "count": 0,
                    "buckets": {b: 0 for b in sorted(bkts)},
                }
            h = self._histograms[name]
            h["sum"] += value
            h["count"] += 1
            for b in h["buckets"]:
                if value <= b:
                    h["buckets"][b] += 1
            if help_text:
                self._meta[name] = help_text

    def histogram_summary(self, name: str) -> Optional[Dict[str, Any]]:
        return self._histograms.get(name)

    # ── Prometheus text format ────────────────────────────────────────────────

    def format_prometheus(self) -> str:
        lines: List[str] = []

        for name, value in sorted(self._counters.items()):
            help_text = self._meta.get(name, "")
            if help_text:
                lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name}_total {value}")

        for name, value in sorted(self._gauges.items()):
            help_text = self._meta.get(name, "")
            if help_text:
                lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {value}")

        for name, h in sorted(self._histograms.items()):
            help_text = self._meta.get(name, "")
            if help_text:
                lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} histogram")
            for b, cnt in sorted(h["buckets"].items()):
                lines.append(f'{name}_bucket{{le="{b}"}} {cnt}')
            lines.append(f'{name}_bucket{{le="+Inf"}} {h["count"]}')
            lines.append(f"{name}_sum {h['sum']}")
            lines.append(f"{name}_count {h['count']}")

        return "\n".join(lines) + ("\n" if lines else "")

    # ── Snapshot for JSON export ──────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": {
                    name: {
                        "sum": h["sum"],
                        "count": h["count"],
                        "avg": h["sum"] / h["count"] if h["count"] else 0.0,
                    }
                    for name, h in self._histograms.items()
                },
            }

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()


# ── Process-wide singleton ────────────────────────────────────────────────────

_registry: Optional[MetricsRegistry] = None


def get_registry() -> MetricsRegistry:
    """Return the process-wide MetricsRegistry, creating it if needed."""
    global _registry
    if _registry is None:
        _registry = MetricsRegistry()
        # Pre-declare well-known metrics with help text
        _registry.inc(
            "tabletalk_queries_total", 0,
            help_text="Total number of SQL generation requests",
        )
        _registry.inc(
            "tabletalk_executions_total", 0,
            help_text="Total number of SQL executions",
        )
        _registry.inc(
            "tabletalk_errors_total", 0,
            help_text="Total number of errors (generation + execution)",
        )
    return _registry


class _Timer:
    """Context manager that records elapsed time into a histogram on exit."""

    def __init__(self, registry: MetricsRegistry, name: str, help_text: str = "") -> None:
        self._reg = registry
        self._name = name
        self._help = help_text
        self._start: float = 0.0

    def __enter__(self) -> "_Timer":
        self._start = time.monotonic()
        return self

    def __exit__(self, *_: Any) -> None:
        elapsed = time.monotonic() - self._start
        self._reg.observe(self._name, elapsed, help_text=self._help)


def timer(name: str, help_text: str = "", registry: Optional[MetricsRegistry] = None) -> _Timer:
    """Return a context-manager that records elapsed seconds into *name*."""
    reg = registry or get_registry()
    return _Timer(reg, name, help_text)
