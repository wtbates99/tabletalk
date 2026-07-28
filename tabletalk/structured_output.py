"""Small validator for the JSON Schema subset used by model contracts."""

from __future__ import annotations

from typing import Any

from tabletalk.domain import ErrorCode, RuntimeStage, TableTalkError


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


def _validate(value: Any, schema: dict[str, Any], path: str) -> None:
    expected = schema.get("type")
    if isinstance(expected, str):
        types = [expected]
    elif (
        isinstance(expected, list)
        and expected
        and all(isinstance(item, str) for item in expected)
    ):
        types = [str(item) for item in expected]
    else:
        raise ValueError(f"{path}: schema type is missing or unsupported")
    if not any(_matches_type(value, item) for item in types):
        raise ValueError(f"{path}: expected {' or '.join(types)}")
    if value is None:
        return
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: value is outside the allowed enum")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise ValueError(f"{path}: object properties schema is invalid")
        required = schema.get("required", [])
        if not isinstance(required, list) or not all(
            isinstance(field, str) for field in required
        ):
            raise ValueError(f"{path}: required schema is invalid")
        missing = [str(field) for field in required if field not in value]
        if missing:
            raise ValueError(f"{path}: missing required fields {', '.join(missing)}")
        if schema.get("additionalProperties") is False:
            additional = sorted(set(value) - set(properties))
            if additional:
                raise ValueError(
                    f"{path}: unexpected fields {', '.join(additional)}"
                )
        for field, item in value.items():
            field_schema = properties.get(field)
            if isinstance(field_schema, dict):
                _validate(item, field_schema, f"{path}.{field}")
    if isinstance(value, list):
        item_schema = schema.get("items")
        if not isinstance(item_schema, dict):
            raise ValueError(f"{path}: array items schema is missing")
        for index, item in enumerate(value):
            _validate(item, item_schema, f"{path}[{index}]")


def validate_structured_output(
    value: Any,
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Return a validated object or raise a safe typed model-output failure."""
    try:
        _validate(value, schema, "$")
    except ValueError as error:
        raise TableTalkError(
            ErrorCode.MODEL_OUTPUT_MALFORMED,
            RuntimeStage.GENERATION,
            "Configured model output did not match the required JSON schema.",
            details={"validation_error": str(error)},
        ) from error
    if not isinstance(value, dict):
        raise TableTalkError(
            ErrorCode.MODEL_OUTPUT_MALFORMED,
            RuntimeStage.GENERATION,
            "Configured model structured output must be an object.",
        )
    return value
