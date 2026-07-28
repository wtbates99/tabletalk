from __future__ import annotations

import pytest

from tabletalk.domain import ErrorCode, TableTalkError
from tabletalk.structured_output import validate_structured_output

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sql", "claims", "ambiguity"],
    "properties": {
        "sql": {"type": "string"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["claim", "supported"],
                "properties": {
                    "claim": {"type": "string"},
                    "supported": {"type": "boolean"},
                },
            },
        },
        "ambiguity": {"type": ["string", "null"]},
    },
}


def test_validates_nested_schema_subset() -> None:
    value = {
        "sql": "SELECT 1",
        "claims": [{"claim": "One.", "supported": True}],
        "ambiguity": None,
    }

    assert validate_structured_output(value, SCHEMA) is value


@pytest.mark.parametrize(
    "value",
    [
        {"sql": "SELECT 1", "claims": []},
        {
            "sql": "SELECT 1",
            "claims": [{"claim": "One.", "supported": "yes"}],
            "ambiguity": None,
        },
        {
            "sql": "SELECT 1",
            "claims": [],
            "ambiguity": None,
            "extra": "not allowed",
        },
    ],
)
def test_schema_mismatch_is_a_typed_model_failure(value: object) -> None:
    with pytest.raises(TableTalkError) as raised:
        validate_structured_output(value, SCHEMA)

    assert raised.value.code is ErrorCode.MODEL_OUTPUT_MALFORMED
    assert raised.value.details["validation_error"].startswith("$")
