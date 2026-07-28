"""
factories.py — provider instantiation with a registry pattern (item 23),
typed config shapes (item 22), and actionable error messages (item 9).
"""

import os
import re

# ── Typed config shapes (item 22) ─────────────────────────────────────────────
# TypedDicts document the expected keys for each provider without adding a
# runtime dependency on Pydantic. IDEs and mypy can use these for autocomplete.
from typing import Any, TypedDict

from tabletalk.interfaces import DatabaseProvider, LLMProvider


class LLMConfig(TypedDict, total=False):
    provider: str  # required
    api_key: str  # required (can be ${ENV_VAR})
    model: str
    max_tokens: int
    temperature: float
    base_url: str
    request_timeout_seconds: float
    reasoning_effort: str


class SnowflakeConfig(TypedDict, total=False):
    type: str
    account: str
    user: str
    password: str
    database: str
    warehouse: str
    schema: str
    role: str


class DuckDBConfig(TypedDict, total=False):
    type: str
    database_path: str


class SQLiteConfig(TypedDict, total=False):
    type: str
    database_path: str  # required


# ── Registry (item 23) ────────────────────────────────────────────────────────
# Maps provider type → import path so the if/elif chains are replaced with
# a single dispatch table. Import errors surface the install hint automatically.

_LLM_INSTALL_HINTS: dict[str, str] = {
    "openai-compatible": ("Configure an OpenAI-compatible base_url, api_key, and model"),
    "openai": "openai is already included — check your OPENAI_API_KEY",
    "ollama": (
        "Install Ollama from https://ollama.com, run 'ollama serve', "
        "and pull the configured local model"
    ),
}

_DB_INSTALL_HINTS: dict[str, str] = {
    "snowflake": "uv add 'tabletalk[snowflake]'",
    "duckdb": "uv add 'tabletalk[duckdb]'",
    "sqlite": "(built-in, no extra install needed)",
}

SUPPORTED_LLM_PROVIDERS = sorted(_LLM_INSTALL_HINTS)
SUPPORTED_DB_PROVIDERS = sorted(_DB_INSTALL_HINTS)


# ── Env-var resolution ────────────────────────────────────────────────────────


def resolve_env_vars(value: str) -> str:
    """Resolve ${ENV_VAR} placeholders in a string value. (item 9: actionable error)"""
    if isinstance(value, str) and "${" in value:
        pattern = r"\${([^}]+)}"
        for match in re.findall(pattern, value):
            env_value = os.environ.get(match)
            if env_value is None:
                raise ValueError(
                    f"Environment variable '{match}' is not set. "
                    f"Fix: export {match}=<value>  (or add it to your shell profile)"
                )
            value = value.replace(f"${{{match}}}", env_value)
    return value


# ── LLM factory ───────────────────────────────────────────────────────────────


def get_llm_provider(config: dict[str, Any]) -> LLMProvider:
    """Instantiate an LLM provider from config. Raises with an install hint on failure."""
    provider_type = config.get("provider", "")
    if provider_type not in _LLM_INSTALL_HINTS:
        supported = ", ".join(SUPPORTED_LLM_PROVIDERS)
        raise ValueError(f"Unsupported LLM provider: '{provider_type}'. Supported: {supported}")

    max_tokens = int(config.get("max_tokens", 1000))
    temperature = float(config.get("temperature", 0.0))
    request_timeout_seconds = float(config.get("request_timeout_seconds", 60))

    if provider_type in {"openai", "openai-compatible"}:
        from tabletalk.providers.openai_provider import OpenAIProvider

        if provider_type == "openai-compatible":
            missing = [field for field in ("base_url", "api_key", "model") if not config.get(field)]
            if missing:
                raise ValueError("openai-compatible requires: " + ", ".join(missing))
        api_key = resolve_env_vars(config["api_key"])
        return OpenAIProvider(
            api_key=api_key,
            model=config.get("model", "gpt-4o"),
            max_tokens=max_tokens,
            temperature=temperature,
            base_url=config.get("base_url"),
            request_timeout_seconds=request_timeout_seconds,
            provider_name=provider_type,
            reasoning_effort=config.get("reasoning_effort"),
        )

    # ollama — reuses the OpenAI provider with a custom base_url
    from tabletalk.providers.openai_provider import OpenAIProvider

    return OpenAIProvider(
        api_key="ollama",
        model=config.get("model", "gemma4:31b-cloud"),
        max_tokens=max_tokens,
        temperature=temperature,
        base_url=config.get("base_url", "http://localhost:11434/v1"),
        request_timeout_seconds=request_timeout_seconds,
        provider_name="ollama",
        reasoning_effort=config.get("reasoning_effort", "none"),
    )


# ── DB factory ────────────────────────────────────────────────────────────────


def get_db_provider(config: dict[str, Any]) -> DatabaseProvider:
    """
    Build one of the three supported database providers from explicit config.
    Raises ImportError with an install hint when the driver is missing.
    """
    # Resolve env-var placeholders on all string values
    config = {k: resolve_env_vars(v) if isinstance(v, str) else v for k, v in config.items()}

    provider_type = config.get("type", "")
    if provider_type not in _DB_INSTALL_HINTS:
        supported = ", ".join(SUPPORTED_DB_PROVIDERS)
        raise ValueError(
            f"Unsupported database provider: '{provider_type}'. Supported: {supported}"
        )

    try:
        return _build_db_provider(provider_type, config)
    except ImportError as exc:
        hint = _DB_INSTALL_HINTS.get(provider_type, "")
        raise ImportError(
            f"Missing driver for '{provider_type}': {exc}. Install with: {hint}"
        ) from exc


def _build_db_provider(provider_type: str, config: dict[str, Any]) -> DatabaseProvider:
    """Inner factory — separated so ImportError propagates cleanly."""
    if provider_type == "snowflake":
        from tabletalk.providers.snowflake_provider import SnowflakeProvider

        return SnowflakeProvider(
            account=config["account"],
            user=config["user"],
            password=config["password"],
            database=config["database"],
            warehouse=config["warehouse"],
            schema=config.get("schema", "PUBLIC"),
            role=config.get("role"),
        )

    if provider_type == "duckdb":
        from tabletalk.providers.duckdb_provider import DuckDBProvider

        return DuckDBProvider(
            database_path=config.get("database_path", ":memory:"),
            read_only=bool(config.get("read_only", False)),
        )

    if provider_type == "sqlite":
        from tabletalk.providers.sqlite_provider import SQLiteProvider

        return SQLiteProvider(
            database_path=config["database_path"],
            read_only=bool(config.get("read_only", True)),
        )
    raise AssertionError(f"Unhandled database provider: {provider_type}")
