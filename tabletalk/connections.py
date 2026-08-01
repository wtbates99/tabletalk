"""Read-only execution adapters resolved from a dbt profile and target."""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from tabletalk.factories import get_db_provider, resolve_env_vars
from tabletalk.interfaces import DatabaseProvider

SUPPORTED_ADAPTERS = ("sqlite", "duckdb", "snowflake")


class ConnectionError(ValueError):
    pass


@dataclass(frozen=True)
class Target:
    profile: str
    name: str
    adapter: str
    config: dict[str, Any]

    @property
    def identity(self) -> str:
        safe = [self.adapter]
        for field in ("account", "database", "warehouse", "schema", "database_path"):
            value = self.config.get(field)
            if value:
                safe.append(f"{field}={value}")
        return ";".join(safe)


def load_profile_target(
    project_dir: str | Path,
    target_name: str | None,
    profiles_dir: str | Path | None = None,
) -> Target:
    root = Path(project_dir).resolve()
    project_file = root / "dbt_project.yml"
    if not project_file.is_file():
        raise ConnectionError(f"dbt_project.yml not found in {root}")
    project = yaml.safe_load(project_file.read_text()) or {}
    profile_name = project.get("profile")
    if not isinstance(profile_name, str) or not profile_name:
        raise ConnectionError("dbt_project.yml must declare a profile")
    profile_root = Path(
        profiles_dir or os.environ.get("DBT_PROFILES_DIR") or Path.home() / ".dbt"
    ).expanduser()
    profile_file = profile_root / "profiles.yml"
    if not profile_file.is_file():
        raise ConnectionError(f"dbt profiles.yml not found at {profile_file}")
    profiles = yaml.safe_load(profile_file.read_text()) or {}
    profile = profiles.get(profile_name)
    if not isinstance(profile, dict):
        raise ConnectionError(f"dbt profile '{profile_name}' was not found")
    selected = target_name or profile.get("target")
    outputs = profile.get("outputs") or {}
    raw = outputs.get(selected) if isinstance(outputs, dict) else None
    if not isinstance(selected, str) or not isinstance(raw, dict):
        raise ConnectionError(f"dbt target '{selected}' was not found in profile '{profile_name}'")
    adapter = str(raw.get("type") or "").lower()
    if adapter not in SUPPORTED_ADAPTERS:
        raise ConnectionError(
            f"Unsupported dbt adapter '{adapter}'. Supported: {', '.join(SUPPORTED_ADAPTERS)}"
        )
    config = _provider_config(adapter, raw, root)
    return Target(profile_name, selected, adapter, config)


def available_targets(
    project_dir: str | Path, profiles_dir: str | Path | None = None
) -> tuple[str, ...]:
    root = Path(project_dir).resolve()
    project = yaml.safe_load((root / "dbt_project.yml").read_text()) or {}
    profile_name = project.get("profile")
    profile_root = Path(
        profiles_dir or os.environ.get("DBT_PROFILES_DIR") or Path.home() / ".dbt"
    ).expanduser()
    profiles = yaml.safe_load((profile_root / "profiles.yml").read_text()) or {}
    profile = profiles.get(profile_name) or {}
    outputs = profile.get("outputs") or {}
    return tuple(sorted(outputs)) if isinstance(outputs, dict) else ()


def _provider_config(adapter: str, raw: dict[str, Any], project_dir: Path) -> dict[str, Any]:
    def profile_value(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        match = re.fullmatch(
            r"\{\{\s*env_var\(['\"]([^'\"]+)['\"](?:,\s*['\"]([^'\"]*)['\"])?\)\s*\}\}",
            value,
        )
        if not match:
            return resolve_env_vars(value)
        resolved = os.environ.get(match.group(1), match.group(2))
        if resolved is None:
            raise ConnectionError(f"Environment variable '{match.group(1)}' is not set")
        return resolved

    def resolved_path(value: Any) -> str:
        path = Path(str(profile_value(value))).expanduser()
        return str(path if path.is_absolute() else (project_dir / path).resolve())

    if adapter == "duckdb":
        path = raw.get("path") or raw.get("database_path")
        if not path:
            raise ConnectionError("dbt duckdb target requires path")
        return {"type": "duckdb", "database_path": resolved_path(path), "read_only": True}
    if adapter == "sqlite":
        path = raw.get("path") or raw.get("database_path")
        if not path:
            schemas = raw.get("schemas_and_paths") or {}
            path = schemas.get("main") if isinstance(schemas, dict) else None
        if not path:
            raise ConnectionError("dbt sqlite target requires path or schemas_and_paths.main")
        return {"type": "sqlite", "database_path": resolved_path(path), "read_only": True}
    required = ("account", "user", "database", "warehouse")
    missing = [name for name in required if not raw.get(name)]
    if missing:
        raise ConnectionError("dbt snowflake target is missing: " + ", ".join(missing))
    config = {"type": "snowflake"}
    for field in ("account", "user", "password", "database", "warehouse", "role", "schema"):
        if raw.get(field) is not None:
            config[field] = profile_value(raw[field])
    return config


class ReadOnlyConnection:
    """The only warehouse surface exposed to the runtime."""

    def __init__(self, target: Target, provider: DatabaseProvider | None = None) -> None:
        self.target = target
        self.provider = provider or get_db_provider(target.config)

    @property
    def dialect(self) -> str:
        return {"sqlite": "sqlite", "duckdb": "duckdb", "snowflake": "snowflake"}[
            self.target.adapter
        ]

    @property
    def identity(self) -> str:
        return self.target.identity

    def execute(self, sql: str, timeout_seconds: int) -> tuple[dict[str, Any], ...]:
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self.provider.execute_query, sql)
        try:
            rows = future.result(timeout=timeout_seconds)
        except TimeoutError as exc:
            future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            raise ConnectionError(f"Query exceeded the {timeout_seconds}s timeout") from exc
        executor.shutdown(wait=True)
        return tuple(dict(row) for row in rows)

    def ping(self) -> None:
        self.execute("select 1 as tabletalk_health", 10)
