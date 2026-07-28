"""
openai_provider.py — OpenAI and Ollama LLM provider.

item 25: Token usage is captured from the streaming response via
         stream_options={"include_usage": True} (OpenAI SDK >= 1.26)
         and stored in self.last_usage for QuerySession to persist.
"""

import json
from collections.abc import Generator
from typing import Any

from openai import OpenAI

from tabletalk.domain import ErrorCode, RuntimeStage, TableTalkError
from tabletalk.interfaces import LLMProvider


class OpenAIProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        max_tokens: int = 1000,
        temperature: float = 0.0,
        base_url: str | None = None,
        request_timeout_seconds: float = 60,
        provider_name: str = "openai-compatible",
        reasoning_effort: str | None = None,
    ):
        super().__init__()
        self.provider_name = provider_name
        self.model = model
        self.base_url = base_url
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.request_timeout_seconds = request_timeout_seconds
        self.reasoning_effort = reasoning_effort
        client_kwargs: dict = {
            "api_key": api_key,
            "timeout": request_timeout_seconds,
        }
        if base_url:
            client_kwargs["base_url"] = base_url
        self.client = OpenAI(**client_kwargs)

    def generate_response(self, prompt: str) -> str:
        request: dict = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        if self.reasoning_effort:
            request["reasoning_effort"] = self.reasoning_effort
        response = self.client.chat.completions.create(
            **request,
        )
        # Capture token usage (item 25)
        if response.usage:
            self.last_usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
            }
        content = response.choices[0].message.content
        result = content.strip() if content is not None else ""
        if not result:
            raise TableTalkError(
                ErrorCode.MODEL_OUTPUT_MALFORMED,
                RuntimeStage.GENERATION,
                f"Configured model '{self.model}' returned an empty response.",
                details={"provider": self.provider_name, "model": self.model},
            )
        return result

    def generate_response_stream(self, prompt: str) -> Generator[str, None, None]:
        yield from self.generate_chat_stream([{"role": "user", "content": prompt}])

    def generate_structured(
        self,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
    ) -> dict[str, Any]:
        schema_instruction = (
            "Return only a JSON object matching this exact JSON Schema. "
            "Do not use Markdown or code fences.\n"
            + json.dumps(json_schema, separators=(",", ":"), sort_keys=True)
        )
        schema_messages = [
            {"role": "system", "content": schema_instruction},
            *messages,
        ]
        request: dict = {
            "model": self.model,
            "messages": schema_messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "tabletalk_response",
                    "strict": True,
                    "schema": json_schema,
                },
            },
        }
        if self.reasoning_effort:
            request["reasoning_effort"] = self.reasoning_effort
        response = self.client.chat.completions.create(**request)
        if response.usage:
            self.last_usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
            }
        content = response.choices[0].message.content
        if not content:
            raise TableTalkError(
                ErrorCode.MODEL_OUTPUT_MALFORMED,
                RuntimeStage.GENERATION,
                f"Configured model '{self.model}' returned an empty response.",
                details={"provider": self.provider_name, "model": self.model},
            )
        try:
            value = json.loads(content)
        except json.JSONDecodeError as error:
            raise TableTalkError(
                ErrorCode.MODEL_OUTPUT_MALFORMED,
                RuntimeStage.GENERATION,
                f"Configured model '{self.model}' returned malformed JSON.",
                details={"provider": self.provider_name, "model": self.model},
            ) from error
        from tabletalk.structured_output import validate_structured_output

        return validate_structured_output(value, json_schema)

    def generate_chat_stream(self, messages: list[dict[str, str]]) -> Generator[str, None, None]:
        # Request usage data in the final streaming chunk (item 25)
        # stream_options is supported by OpenAI SDK >= 1.26; ignored by Ollama.
        request: dict = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": True,
        }
        if self.reasoning_effort:
            request["reasoning_effort"] = self.reasoning_effort
        try:
            stream = self.client.chat.completions.create(
                **request,
                stream_options={"include_usage": True},
            )
        except TypeError:
            # Older SDK or Ollama that doesn't accept stream_options
            stream = self.client.chat.completions.create(**request)

        emitted = False
        for chunk in stream:
            # The final chunk from OpenAI contains usage when stream_options is set
            if chunk.usage:
                self.last_usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens,
                    "completion_tokens": chunk.usage.completion_tokens,
                }
            if chunk.choices and chunk.choices[0].delta.content:
                emitted = True
                yield chunk.choices[0].delta.content
        if not emitted:
            raise TableTalkError(
                ErrorCode.MODEL_OUTPUT_MALFORMED,
                RuntimeStage.GENERATION,
                f"Configured model '{self.model}' returned an empty response.",
                details={"provider": self.provider_name, "model": self.model},
            )
