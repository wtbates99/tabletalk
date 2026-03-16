import os
import re
from typing import Any, Dict

from tabletalk.interfaces import DatabaseProvider, LLMProvider


def resolve_env_vars(value: str) -> str:
    """Resolve ${ENV_VAR} placeholders in a string value."""
    if isinstance(value, str) and "${" in value:
        pattern = r"\${([^}]+)}"
        for match in re.findall(pattern, value):
            env_value = os.environ.get(match)
            if env_value is None:
                raise ValueError(f"Environment variable '{match}' is not set")
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
            f"Run 'tabletalk connect' to create one."
        )
    return profile


def get_llm_provider(config: Dict[str, Any]) -> LLMProvider:
    from tabletalk.providers.anthropic_provider import AnthropicProvider
    from tabletalk.providers.openai_provider import OpenAIProvider

    provider_type = config["provider"]
    max_tokens = int(config.get("max_tokens", 1000))
    temperature = float(config.get("temperature", 0.0))

    if provider_type == "openai":
        api_key = resolve_env_vars(config["api_key"])
        return OpenAIProvider(
            api_key=api_key,
            model=config.get("model", "gpt-4o"),
            max_tokens=max_tokens,
            temperature=temperature,
        )
    elif provider_type == "anthropic":
        api_key = resolve_env_vars(config["api_key"])
        return AnthropicProvider(
            api_key=api_key,
            model=config.get("model", "claude-sonnet-4-6"),
            max_tokens=max_tokens,
            temperature=temperature,
        )
    elif provider_type == "ollama":
        return OpenAIProvider(
            api_key="ollama",
            model=config.get("model", "qwen2.5-coder:7b"),
            max_tokens=max_tokens,
            temperature=temperature,
            base_url=config.get("base_url", "http://localhost:11434/v1"),
        )
    raise ValueError(
        f"Unsupported LLM provider: '{provider_type}'. "
        f"Supported: openai, anthropic, ollama"
    )


def get_db_provider(config: Dict[str, Any]) -> DatabaseProvider:
    """
    Build a DatabaseProvider from config. Supports an inline 'provider' block
    or a reference to a named profile via the 'profile' key.
    """
    config = _resolve_profile(config)

    # Resolve env vars on all string values
    config = {
        k: resolve_env_vars(v) if isinstance(v, str) else v
        for k, v in config.items()
    }

    provider_type = config.get("type", "")

    if provider_type == "postgres":
        from tabletalk.providers.postgres_provider import PostgresProvider

        return PostgresProvider(
            host=config["host"],
            port=int(config.get("port", 5432)),
            dbname=config["database"],
            user=config["user"],
            password=config["password"],
        )
    elif provider_type == "snowflake":
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
    elif provider_type == "duckdb":
        from tabletalk.providers.duckdb_provider import DuckDBProvider

        return DuckDBProvider(database_path=config.get("database_path", ":memory:"))
    elif provider_type == "azuresql":
        from tabletalk.providers.azuresql_provider import AzureSQLProvider

        return AzureSQLProvider(
            server=config["server"],
            database=config["database"],
            user=config["user"],
            password=config["password"],
            port=int(config.get("port", 1433)),
        )
    elif provider_type == "sqlite":
        from tabletalk.providers.sqlite_provider import SQLiteProvider

        return SQLiteProvider(database_path=config["database_path"])
    elif provider_type == "mysql":
        from tabletalk.providers.mysql_provider import MySQLProvider

        return MySQLProvider(
            host=config["host"],
            database=config["database"],
            user=config["user"],
            password=config["password"],
            port=int(config.get("port", 3306)),
        )
    elif provider_type == "bigquery":
        from tabletalk.providers.bigquery_provider import BigQueryProvider

        if config.get("use_default_credentials", False):
            return BigQueryProvider(project_id=config["project_id"])
        return BigQueryProvider(
            project_id=config["project_id"],
            credentials_path=config.get("credentials"),
        )

    raise ValueError(
        f"Unsupported database provider: '{provider_type}'. "
        f"Supported: postgres, snowflake, duckdb, azuresql, sqlite, mysql, bigquery"
    )
