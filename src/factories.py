import os
import re
from typing import Any, Dict

from interfaces import DatabaseProvider, LLMProvider
from providers.bigquery_provider import BigQueryProvider
from providers.openai_provider import OpenAIProvider


def resolve_env_vars(value: str) -> str:
    """Resolve environment variables in a string."""
    if isinstance(value, str) and "${" in value:
        pattern = r"\${([^}]+)}"
        matches = re.findall(pattern, value)
        resolved_value = value
        for match in matches:
            env_value = os.environ.get(match)
            if env_value is None:
                raise ValueError(f"Environment variable {match} not found")
            resolved_value = resolved_value.replace(f"${{{match}}}", env_value)
        return resolved_value
    return value


def get_llm_provider(config: Dict[str, Any]) -> LLMProvider:
    provider_type = config["provider"]
    if provider_type == "openai":
        api_key = resolve_env_vars(config["api_key"])
        return OpenAIProvider(api_key=api_key, model=config.get("model", "gpt-4o"))
    raise ValueError(f"Unsupported LLM provider: {provider_type}")


def get_db_provider(config: Dict[str, Any]) -> DatabaseProvider:
    provider_type = config["type"]
    if provider_type == "bigquery":
        if config.get("use_default_credentials", False):
            return BigQueryProvider(project_id=config["project_id"])
        else:
            return BigQueryProvider(
                project_id=config["project_id"], credentials_path=config["credentials"]
            )
    raise ValueError(f"Unsupported database provider: {provider_type}")
