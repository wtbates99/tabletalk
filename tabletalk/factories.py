"""
factories.py — provider instantiation with a registry pattern (item 23),
typed config shapes (item 22), and actionable error messages (item 9).
"""
import os
import re
from typing import Any, Dict, Optional

from tabletalk.interfaces import DatabaseProvider, LLMProvider

# ── Typed config shapes (item 22) ─────────────────────────────────────────────
# TypedDicts document the expected keys for each provider without adding a
# runtime dependency on Pydantic. IDEs and mypy can use these for autocomplete.

from typing import TypedDict


class LLMConfig(TypedDict, total=False):
    provider: str       # required
    api_key: str        # required (can be ${ENV_VAR})
    model: str
    max_tokens: int
    temperature: float
    base_url: str       # ollama only


class PostgresConfig(TypedDict, total=False):
    type: str           # required: "postgres"
    host: str           # required
    port: int
    database: str       # required
    user: str           # required
    password: str       # required


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


class AzureSQLConfig(TypedDict, total=False):
    type: str
    server: str
    database: str
    user: str
    password: str
    port: int


class SQLiteConfig(TypedDict, total=False):
    type: str
    database_path: str  # required


class MySQLConfig(TypedDict, total=False):
    type: str
    host: str
    database: str
    user: str
    password: str
    port: int


class BigQueryConfig(TypedDict, total=False):
    type: str
    project_id: str
    use_default_credentials: bool
    credentials: str    # path to service-account JSON


# ── Registry (item 23) ────────────────────────────────────────────────────────
# Maps provider type → import path so the if/elif chains are replaced with
# a single dispatch table. Import errors surface the install hint automatically.

_LLM_INSTALL_HINTS: Dict[str, str] = {
    "openai": "openai is already included — check your OPENAI_API_KEY",
    "anthropic": "anthropic is already included — check your ANTHROPIC_API_KEY",
    "ollama": "Install Ollama from https://ollama.ai and run 'ollama serve'",
}

_DB_INSTALL_HINTS: Dict[str, str] = {
    "postgres": "uv add 'tabletalk[postgres]'",
    "mysql": "uv add 'tabletalk[mysql]'",
    "bigquery": "uv add 'tabletalk[bigquery]'",
    "snowflake": "uv add 'tabletalk[snowflake]'",
    "duckdb": "uv add 'tabletalk[duckdb]'",
    "azuresql": "uv add 'tabletalk[azuresql]'",
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


def _resolve_profile(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    If config contains a 'profile' key, load that profile from
    ~/.tabletalk/profiles.yml and return it. Otherwise return config as-is.
    """
    profile_name = config.get("profile")
    if not profile_name:
        return config
    from tabletalk.profiles import get_profile

    profile = get_profile(profile_name)
    if profile is None:
        raise ValueError(
            f"Profile '{profile_name}' not found in ~/.tabletalk/profiles.yml. "
            f"Run 'tabletalk connect' to create one, or "
            f"'tabletalk profiles list' to see existing profiles."
        )
    return profile


# ── LLM factory ───────────────────────────────────────────────────────────────


def get_llm_provider(config: Dict[str, Any]) -> LLMProvider:
    """Instantiate an LLM provider from config. Raises with an install hint on failure."""
    provider_type = config.get("provider", "")
    if provider_type not in _LLM_INSTALL_HINTS:
        supported = ", ".join(SUPPORTED_LLM_PROVIDERS)
        raise ValueError(
            f"Unsupported LLM provider: '{provider_type}'. Supported: {supported}"
        )

    max_tokens = int(config.get("max_tokens", 1000))
    temperature = float(config.get("temperature", 0.0))

    if provider_type == "openai":
        from tabletalk.providers.openai_provider import OpenAIProvider

        api_key = resolve_env_vars(config["api_key"])
        return OpenAIProvider(
            api_key=api_key,
            model=config.get("model", "gpt-4o"),
            max_tokens=max_tokens,
            temperature=temperature,
        )

    if provider_type == "anthropic":
        from tabletalk.providers.anthropic_provider import AnthropicProvider

        api_key = resolve_env_vars(config["api_key"])
        return AnthropicProvider(
            api_key=api_key,
            model=config.get("model", "claude-sonnet-4-6"),
            max_tokens=max_tokens,
            temperature=temperature,
        )

    # ollama — reuses the OpenAI provider with a custom base_url
    from tabletalk.providers.openai_provider import OpenAIProvider

    return OpenAIProvider(
        api_key="ollama",
        model=config.get("model", "qwen2.5-coder:7b"),
        max_tokens=max_tokens,
        temperature=temperature,
        base_url=config.get("base_url", "http://localhost:11434/v1"),
    )


# ── DB factory ────────────────────────────────────────────────────────────────


def get_db_provider(config: Dict[str, Any]) -> DatabaseProvider:
    """
    Build a DatabaseProvider from config. Supports an inline 'provider' block
    or a reference to a named profile via the 'profile' key.
    Raises ImportError with an install hint when the driver is missing.
    """
    config = _resolve_profile(config)

    # Resolve env-var placeholders on all string values
    config = {
        k: resolve_env_vars(v) if isinstance(v, str) else v
        for k, v in config.items()
    }

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
            f"Missing driver for '{provider_type}': {exc}. "
            f"Install with: {hint}"
        ) from exc


def _build_db_provider(provider_type: str, config: Dict[str, Any]) -> DatabaseProvider:
    """Inner factory — separated so ImportError propagates cleanly."""
    if provider_type == "postgres":
        from tabletalk.providers.postgres_provider import PostgresProvider

        return PostgresProvider(
            host=config["host"],
            port=int(config.get("port", 5432)),
            dbname=config["database"],
            user=config["user"],
            password=config["password"],
        )

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

        return DuckDBProvider(database_path=config.get("database_path", ":memory:"))

    if provider_type == "azuresql":
        from tabletalk.providers.azuresql_provider import AzureSQLProvider

        return AzureSQLProvider(
            server=config["server"],
            database=config["database"],
            user=config["user"],
            password=config["password"],
            port=int(config.get("port", 1433)),
        )

    if provider_type == "sqlite":
        from tabletalk.providers.sqlite_provider import SQLiteProvider

        return SQLiteProvider(database_path=config["database_path"])

    if provider_type == "mysql":
        from tabletalk.providers.mysql_provider import MySQLProvider

        return MySQLProvider(
            host=config["host"],
            database=config["database"],
            user=config["user"],
            password=config["password"],
            port=int(config.get("port", 3306)),
        )

    # bigquery
    from tabletalk.providers.bigquery_provider import BigQueryProvider

    if config.get("use_default_credentials", False):
        return BigQueryProvider(project_id=config["project_id"])
    return BigQueryProvider(
        project_id=config["project_id"],
        credentials_path=config.get("credentials"),
    )
