"""Deterministic semantic differences between compiled agent artifacts."""

from __future__ import annotations

from typing import Any


def _named(values: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(values, list):
        return {}
    return {
        str(value["name"]): value
        for value in values
        if isinstance(value, dict) and value.get("name")
    }


def semantic_changes(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> tuple[str, ...]:
    if before is None and after is not None:
        return ("create agent",)
    if before is not None and after is None:
        return ("delete agent",)
    if before is None or after is None:
        return ()

    changes: list[str] = []
    for field in (
        "version",
        "description",
        "owner",
        "persona",
        "time_semantics",
        "policies",
        "required_evals",
    ):
        if before.get(field) != after.get(field):
            changes.append(f"change {field}")

    for field in ("relations", "relationships", "metrics"):
        old = _named(before.get(field))
        new = _named(after.get(field))
        for name in sorted(new.keys() - old.keys()):
            changes.append(f"add {field[:-1]} {name}")
        for name in sorted(old.keys() - new.keys()):
            changes.append(f"remove {field[:-1]} {name}")
        for name in sorted(old.keys() & new.keys()):
            if old[name] != new[name]:
                changes.append(f"change {field[:-1]} {name}")
    return tuple(changes)
