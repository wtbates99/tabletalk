"""Version identifiers and canonical content digests for TableTalk resources."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

PROJECT_SCHEMA = "tabletalk.project/v1"
AGENT_SCHEMA = "tabletalk.agent/v1"
SEMANTIC_SCHEMA = "tabletalk.semantic/v1"
EVAL_SCHEMA = "tabletalk.eval/v1"
EVAL_RESULT_SCHEMA = "tabletalk.eval-result/v1"
PROPOSAL_SCHEMA = "tabletalk.proposal/v1"
PROPOSAL_REJECTION_SCHEMA = "tabletalk.proposal-rejection/v1"
APPROVAL_SCHEMA = "tabletalk.approval/v1"
PROVENANCE_SCHEMA = "tabletalk.provenance/v1"
INFERENCE_SCHEMA = "tabletalk.inference/v1"
RUNTIME_SCHEMA = "tabletalk.runtime/v1"
REPORT_SCHEMA = "tabletalk.report/v1"
BASELINE_SCHEMA = "tabletalk.baseline/v1"


class SchemaVersionError(ValueError):
    """A serialized TableTalk resource has an unsupported schema version."""


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON bytes for a JSON-compatible value."""
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def content_digest(value: bytes | str | Any) -> str:
    """Return one prefixed SHA-256 digest."""
    if isinstance(value, str):
        raw = value.encode("utf-8")
    elif isinstance(value, bytes):
        raw = value
    else:
        raw = canonical_json_bytes(value)
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def require_schema_version(
    payload: Mapping[str, Any],
    expected: str,
    *,
    resource: str,
    editable: bool = True,
) -> None:
    """Reject a missing or unsupported schema version with a migration message."""
    actual = payload.get("schema_version")
    if actual == expected:
        return
    if actual is None:
        detail = "has no schema_version"
    else:
        detail = f"uses schema version '{actual}'"
    if editable:
        action = f"Run 'tabletalk migrate {resource}' before you continue."
    else:
        action = (
            "This immutable artifact cannot migrate. "
            f"Regenerate it with a TableTalk version that writes '{expected}'."
        )
    raise SchemaVersionError(f"{resource} {detail}. Expected '{expected}'. {action}")
