"""Provider construction and environment-variable resolution."""

from __future__ import annotations

import os
import re
from typing import Any

from tabletalk.interfaces import DatabaseProvider, LLMProvider

SUPPORTED_LLM_PROVIDERS = ("ollama", "openai", "openai-compatible")
SUPPORTED_DB_PROVIDERS = ("duckdb", "snowflake", "sqlite")

_DB_INSTALL_HINTS = {
    "duckdb": "uv add 'tabletalk[duckdb]'",
    "snowflake": "uv add 'tabletalk[snowflake]'",
    "sqlite": "SQLite is included with Python",
}


def resolve_env_vars(value: str) -> str:
    """Resolve every ${NAME} placeholder, failing clearly when a value is missing."""
    for name in re.findall(r"\${([^}]+)}", value):
        resolved = os.environ.get(name)
        if resolved is None:
            raise ValueError(f"Environment variable '{name}' is not set. Export it and retry.")
        value = value.replace(f"${{{name}}}", resolved)
    return value


def get_llm_provider(config: dict[str, Any]) -> LLMProvider:
    provider = str(config.get("provider") or "")
    if provider not in SUPPORTED_LLM_PROVIDERS:
        raise ValueError(
            f"Unsupported LLM provider '{provider}'. Supported: "
            + ", ".join(SUPPORTED_LLM_PROVIDERS)
        )
    from tabletalk.providers.openai_provider import OpenAIProvider

    if provider == "openai-compatible":
        missing = [name for name in ("base_url", "api_key", "model") if not config.get(name)]
        if missing:
            raise ValueError("openai-compatible requires: " + ", ".join(missing))
    if provider == "openai" and not config.get("api_key"):
        raise ValueError("openai requires api_key, normally ${OPENAI_API_KEY}")

    ollama = provider == "ollama"
    api_key = "ollama" if ollama else resolve_env_vars(str(config["api_key"]))
    return OpenAIProvider(
        api_key=api_key,
        model=str(config.get("model") or ("gemma4:31b-cloud" if ollama else "gpt-4o")),
        max_tokens=int(config.get("max_tokens", 1000)),
        temperature=float(config.get("temperature", 0)),
        base_url=config.get("base_url") or ("http://localhost:11434/v1" if ollama else None),
        request_timeout_seconds=float(config.get("request_timeout_seconds", 60)),
        provider_name=provider,
        reasoning_effort=config.get("reasoning_effort"),
    )


def get_db_provider(config: dict[str, Any]) -> DatabaseProvider:
    resolved = {
        key: resolve_env_vars(value) if isinstance(value, str) else value
        for key, value in config.items()
    }
    provider = str(resolved.get("type") or "")
    if provider not in SUPPORTED_DB_PROVIDERS:
        raise ValueError(
            f"Unsupported database provider '{provider}'. Supported: "
            + ", ".join(SUPPORTED_DB_PROVIDERS)
        )
    try:
        return _build_db_provider(provider, resolved)
    except ImportError as exc:
        raise ImportError(
            f"The {provider} driver is unavailable. {_DB_INSTALL_HINTS[provider]}."
        ) from exc


def _build_db_provider(provider: str, config: dict[str, Any]) -> DatabaseProvider:
    if provider == "duckdb":
        from tabletalk.providers.duckdb_provider import DuckDBProvider

        return DuckDBProvider(
            database_path=str(config.get("database_path") or ":memory:"),
            read_only=bool(config.get("read_only", False)),
        )
    if provider == "sqlite":
        from tabletalk.providers.sqlite_provider import SQLiteProvider

        if not config.get("database_path"):
            raise ValueError("sqlite requires database_path")
        return SQLiteProvider(
            database_path=str(config["database_path"]),
            read_only=bool(config.get("read_only", True)),
        )
    from tabletalk.providers.snowflake_provider import SnowflakeProvider

    missing = [
        name
        for name in ("account", "user", "password", "database", "warehouse")
        if not config.get(name)
    ]
    if missing:
        raise ValueError("snowflake requires: " + ", ".join(missing))
    return SnowflakeProvider(
        account=str(config["account"]),
        user=str(config["user"]),
        password=str(config["password"]),
        database=str(config["database"]),
        warehouse=str(config["warehouse"]),
        schema=str(config.get("schema") or "PUBLIC"),
        role=str(config["role"]) if config.get("role") else None,
    )
