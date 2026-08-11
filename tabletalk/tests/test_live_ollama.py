from __future__ import annotations

import os

import pytest

from tabletalk.factories import get_llm_provider

MODEL = "gemma4:31b-cloud"


@pytest.mark.live_ollama
@pytest.mark.skipif(
    os.environ.get("TABLETALK_LIVE_OLLAMA") != "1",
    reason="Set TABLETALK_LIVE_OLLAMA=1 to run the Ollama smoke test.",
)
def test_gemma4_cloud_returns_valid_structured_output() -> None:
    provider = get_llm_provider(
        {
            "provider": "ollama",
            "model": MODEL,
            "base_url": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            "request_timeout_seconds": 120,
            "max_tokens": 64,
            "temperature": 0,
        }
    )
    result = provider.generate_structured(
        [
            {"role": "system", "content": "Return the requested fixed value."},
            {"role": "user", "content": "Set status to ready."},
        ],
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["status"],
            "properties": {"status": {"type": "string", "const": "ready"}},
        },
    )
    assert result == {"status": "ready"}
