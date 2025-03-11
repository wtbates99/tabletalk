from typing import Any, Dict

from interfaces import DatabaseProvider, LLMProvider
from providers.bigquery_provider import BigQueryProvider
from providers.openai_provider import OpenAIProvider


def get_llm_provider(config: Dict[str, Any]) -> LLMProvider:
    provider_type = config["provider"]
    if provider_type == "openai":
        return OpenAIProvider(
            api_key=config["api_key"], model=config.get("model", "gpt-3.5-turbo")
        )
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
