"""Canonical, deterministic serialization for domain objects."""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any


def to_primitive(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: to_primitive(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, dict):
        return {
            str(key): to_primitive(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [to_primitive(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Cannot canonically serialize {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        to_primitive(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
