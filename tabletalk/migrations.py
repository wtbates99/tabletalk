"""Deterministic migrations for editable TableTalk project resources."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tabletalk.schemas import AGENT_SCHEMA, EVAL_SCHEMA, PROJECT_SCHEMA, SEMANTIC_SCHEMA


class MigrationError(ValueError):
    """A resource cannot migrate without changing its meaning."""


def _schema_for(path: Path, payload: dict[str, Any]) -> str:
    if path.name == "tabletalk.yaml":
        if "dbt" not in payload or "llm" not in payload:
            raise MigrationError("tabletalk.yaml requires dbt and llm mappings")
        return PROJECT_SCHEMA
    if "statements" in payload:
        return SEMANTIC_SCHEMA
    if "cases" in payload and "agent" in payload:
        return EVAL_SCHEMA
    if "select" in payload and "name" in payload:
        if "relations" in payload or "connection" in payload:
            raise MigrationError(
                f"{path} uses the removed pre-dbt agent format. Create a new proposal."
            )
        return AGENT_SCHEMA
    raise MigrationError(f"TableTalk cannot determine the resource type for {path}")


def migrate_file(path: str | Path) -> bool:
    """Add the current schema identifier without changing resource meaning."""
    target = Path(path).expanduser().resolve()
    try:
        payload = yaml.safe_load(target.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise MigrationError(f"Could not read {target}: {exc}") from exc
    if not isinstance(payload, dict):
        raise MigrationError(f"{target} must be a YAML mapping")
    expected = _schema_for(target, payload)
    actual = payload.get("schema_version")
    if actual == expected:
        return False
    if actual not in {None, expected.replace("/v1", "/v0")}:
        raise MigrationError(
            f"{target} uses unsupported schema version '{actual}'. Expected '{expected}'."
        )
    migrated = {"schema_version": expected, **payload}
    migrated["schema_version"] = expected
    target.write_text(yaml.safe_dump(migrated, sort_keys=False, allow_unicode=True))
    return True


def migrate_project(path: str | Path) -> tuple[Path, ...]:
    """Migrate editable resources in one TableTalk project."""
    root = Path(path).expanduser().resolve()
    if root.is_file():
        return (root,) if migrate_file(root) else ()
    if not root.is_dir():
        raise MigrationError(f"Migration path does not exist: {root}")
    candidates = [root / "tabletalk.yaml"]
    for directory in ("agents", "evals", "semantics"):
        candidates.extend(sorted((root / directory).glob("*.yaml")))
        candidates.extend(sorted((root / directory).glob("*.yml")))
    existing = tuple(path for path in candidates if path.is_file())
    if not existing:
        raise MigrationError(f"No editable TableTalk resources were found in {root}")
    changed = []
    for candidate in existing:
        if migrate_file(candidate):
            changed.append(candidate)
    return tuple(changed)
