"""Opt-in smoke tests for the documented free local Ollama development path."""

from __future__ import annotations

import os

import pytest

from tabletalk.factories import get_llm_provider

pytestmark = pytest.mark.live_ollama


def _development_provider():
    return get_llm_provider(
        {
            "provider": "ollama",
            "model": os.environ.get(
                "TABLETALK_OLLAMA_MODEL",
                "gemma4:31b-cloud",
            ),
            "base_url": os.environ.get("TABLETALK_OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            "max_tokens": 200,
            "temperature": 0,
            "reasoning_effort": "none",
            "request_timeout_seconds": 60,
        }
    )


@pytest.mark.skipif(
    os.environ.get("TABLETALK_LIVE_OLLAMA") != "1",
    reason="set TABLETALK_LIVE_OLLAMA=1 to use the configured local Ollama daemon",
)
def test_gemma_cloud_development_model_generates_sql_without_fallback() -> None:
    provider = _development_provider()

    response = provider.generate_response(
        "Return only SQLite SQL, no markdown: count rows in customers(id INTEGER, name TEXT)."
    )

    assert response.strip().upper().startswith("SELECT")
    assert "CUSTOMERS" in response.upper()


@pytest.mark.skipif(
    os.environ.get("TABLETALK_LIVE_OLLAMA") != "1",
    reason="set TABLETALK_LIVE_OLLAMA=1 to use the configured local Ollama daemon",
)
def test_gemma_cloud_supports_required_schema_constrained_output() -> None:
    provider = _development_provider()
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["sql", "read_only"],
        "properties": {
            "sql": {"type": "string"},
            "read_only": {"type": "boolean"},
        },
    }

    response = provider.generate_structured(
        [
            {
                "role": "system",
                "content": "Return one schema-constrained SQLite query.",
            },
            {
                "role": "user",
                "content": "Count rows in customers(id INTEGER, name TEXT).",
            },
        ],
        schema,
    )

    assert response["read_only"] is True
    assert str(response["sql"]).upper().startswith("SELECT")
    assert "CUSTOMERS" in str(response["sql"]).upper()
