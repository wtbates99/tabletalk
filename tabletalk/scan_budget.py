"""Warehouse cost guard: refuse a query whose estimated scan exceeds the agent's budget."""

from __future__ import annotations

import json

from tabletalk.connections import ReadOnlyConnection
from tabletalk.traces import Verification

CHECK_NAME = "scan_budget"


class ScanBudgetError(RuntimeError):
    """The warehouse estimated a scan above the agent's budget, or could not estimate one."""


def format_bytes(size: int) -> str:
    """Render a byte count in decimal units, as Snowflake bills and reports them."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            return f"{size} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    raise AssertionError("unreachable")


def check_scan_budget(connection: ReadOnlyConnection, sql: str, budget: int) -> Verification:
    """Estimate the scan for ``sql`` and pass only if it is within ``budget`` bytes.

    The guard fails closed: an adapter that supports estimates but returns an error
    blocks the query. Adapters with no estimate (DuckDB, SQLite) pass, and the check
    says so, because local scans carry no warehouse cost.
    """
    try:
        estimate = connection.estimate_scan_bytes(sql)
    except Exception as exc:
        raise ScanBudgetError(
            f"Query blocked: the warehouse could not estimate its scan ({exc})"
        ) from exc
    if estimate is None:
        return Verification(
            CHECK_NAME, True, f"{connection.dialect} reports no scan estimate; budget not applied"
        )
    if estimate > budget:
        raise ScanBudgetError(
            f"Query blocked: the warehouse estimates {format_bytes(estimate)} scanned, above "
            f"this agent's max_scan_bytes of {format_bytes(budget)}"
        )
    return Verification(
        CHECK_NAME,
        True,
        f"Estimated scan {format_bytes(estimate)} of {format_bytes(budget)} allowed",
    )


def query_tag(agent: str, inference_fingerprint: str) -> str:
    """Return the warehouse query tag that joins query history to a receipt."""
    return json.dumps(
        {"app": "tabletalk", "agent": agent, "inference_fingerprint": inference_fingerprint},
        separators=(",", ":"),
        sort_keys=True,
    )
