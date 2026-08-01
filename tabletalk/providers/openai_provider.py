"""
openai_provider.py — OpenAI and Ollama LLM provider.

Token usage is captured from the response and attached to the shared answer trace.
"""

import json
from collections.abc import Generator
from typing import Any

from openai import OpenAI

from tabletalk.interfaces import LLMProvider, validate_structured_value


def _json_object(content: str, model: str) -> dict[str, Any]:
    candidate = content.strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        first_line, _, body = candidate.partition("\n")
        if first_line.lower() in {"```", "```json"}:
            candidate = body.rsplit("```", 1)[0].strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as error:
        # Some OpenAI-compatible cloud endpoints occasionally wrap an otherwise
        # valid structured response in a short preamble or Markdown. Recover the
        # first complete JSON object without attempting to repair invalid JSON.
        decoder = json.JSONDecoder()
        value = None
        for start, character in enumerate(candidate):
            if character != "{":
                continue
            try:
                decoded, _ = decoder.raw_decode(candidate[start:])
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, dict):
                value = decoded
                break
        if value is None:
            raise ValueError(f"Configured model '{model}' returned malformed JSON") from error
    if not isinstance(value, dict):
        raise ValueError("Model structured output must be an object")
    return value


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
        if response.usage:
            self.last_usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
            }
        content = response.choices[0].message.content
        result = content.strip() if content is not None else ""
        if not result:
            raise ValueError(f"Configured model '{self.model}' returned an empty response")
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
        last_error: ValueError | None = None
        for attempt in range(2):
            response = self.client.chat.completions.create(**request)
            if response.usage:
                self.last_usage = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                }
            content = response.choices[0].message.content
            if not content:
                last_error = ValueError(
                    f"Configured model '{self.model}' returned an empty response"
                )
            else:
                try:
                    value = _json_object(content, self.model)
                    validate_structured_value(value, json_schema)
                    return value
                except ValueError as exc:
                    last_error = exc
            if attempt == 0:
                request["messages"] = [
                    *schema_messages,
                    {
                        "role": "system",
                        "content": (
                            "Your previous response did not match the required schema. "
                            "Try once more and return only the complete JSON object."
                        ),
                    },
                ]
        raise ValueError(
            f"Configured model '{self.model}' failed structured output after one retry: "
            f"{last_error}"
        ) from last_error

    def generate_chat_stream(self, messages: list[dict[str, str]]) -> Generator[str, None, None]:
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
            stream = self.client.chat.completions.create(**request)

        emitted = False
        for chunk in stream:
            if chunk.usage:
                self.last_usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens,
                    "completion_tokens": chunk.usage.completion_tokens,
                }
            if chunk.choices and chunk.choices[0].delta.content:
                emitted = True
                yield chunk.choices[0].delta.content
        if not emitted:
            raise ValueError(f"Configured model '{self.model}' returned an empty response")
