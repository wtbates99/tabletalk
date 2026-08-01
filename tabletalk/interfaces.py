"""Narrow connector and model contracts used by the shared runtime."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Generator
from typing import Any


def validate_structured_value(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    expected = schema.get("type")
    allowed = (expected,) if isinstance(expected, str) else tuple(expected or ())
    matches = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    if allowed and not any(kind in matches and matches[kind](value) for kind in allowed):
        raise ValueError(f"Structured output {path} must have type {' or '.join(allowed)}")
    if isinstance(value, dict) and "object" in allowed:
        properties = schema.get("properties") or {}
        missing = [name for name in schema.get("required") or () if name not in value]
        if missing:
            raise ValueError(f"Structured output {path} is missing: {', '.join(missing)}")
        if schema.get("additionalProperties") is False:
            extras = set(value) - set(properties)
            if extras:
                raise ValueError(
                    f"Structured output {path} has unexpected fields: {', '.join(sorted(extras))}"
                )
        for name, item in value.items():
            if name in properties:
                validate_structured_value(item, properties[name], f"{path}.{name}")
    if isinstance(value, list) and "array" in allowed and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            validate_structured_value(item, schema["items"], f"{path}[{index}]")


class DatabaseProvider(ABC):
    @abstractmethod
    def execute_query(self, sql_query: str) -> list[dict[str, Any]]:
        """Execute a query and return row mappings."""

    @abstractmethod
    def get_client(self) -> Any:
        """Return the native connection for health checks."""


class LLMProvider(ABC):
    def __init__(self) -> None:
        self.last_usage: dict[str, int] = {}

    @abstractmethod
    def generate_response(self, prompt: str) -> str:
        """Generate a complete response."""

    def generate_response_stream(self, prompt: str) -> Generator[str, None, None]:
        yield self.generate_response(prompt)

    def generate_chat_stream(self, messages: list[dict[str, str]]) -> Generator[str, None, None]:
        prompt = "\n".join(message["content"] for message in messages)
        yield from self.generate_response_stream(prompt)

    def generate_structured(
        self,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
    ) -> dict[str, Any]:
        value = json.loads("".join(self.generate_chat_stream(messages)))
        if not isinstance(value, dict):
            raise ValueError("Model structured output must be an object")
        validate_structured_value(value, json_schema)
        return value
