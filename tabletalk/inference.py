"""Immutable inference artifacts and explicit cache policies."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from tabletalk.interfaces import LLMProvider
from tabletalk.schemas import (
    INFERENCE_SCHEMA,
    canonical_json_bytes,
    content_digest,
    require_schema_version,
)
from tabletalk.traces import Trace, Usage, trace_from_dict


class InferenceError(ValueError):
    """An inference artifact or cache record is invalid."""


class ReplayMissError(InferenceError):
    """Strict replay found no matching inference artifact."""


class CachePolicy(StrEnum):
    NORMAL = "normal"
    READ_ONLY = "read-only"
    WRITE_ONLY = "write-only"
    REPLAY = "replay"
    DISABLED = "disabled"

    @property
    def reads(self) -> bool:
        return self in {self.NORMAL, self.READ_ONLY, self.REPLAY}

    @property
    def writes(self) -> bool:
        return self in {self.NORMAL, self.WRITE_ONLY}


def _typed_value(value: Any) -> Any:
    if is_dataclass(value):
        return {item.name: _typed_value(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, dict):
        return {str(key): _typed_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_typed_value(item) for item in value]
    if isinstance(value, Decimal):
        return {"__tabletalk_type__": "decimal", "value": str(value)}
    if isinstance(value, datetime):
        return {"__tabletalk_type__": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"__tabletalk_type__": "date", "value": value.isoformat()}
    return value


def _from_typed_value(value: Any) -> Any:
    if isinstance(value, dict):
        value_type = value.get("__tabletalk_type__")
        if value_type == "decimal":
            return Decimal(str(value["value"]))
        if value_type == "date":
            return date.fromisoformat(str(value["value"]))
        if value_type == "datetime":
            return datetime.fromisoformat(str(value["value"]))
        return {str(key): _from_typed_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_from_typed_value(item) for item in value]
    return value


def redact_secrets(value: Any) -> Any:
    """Remove values whose field names identify common credential types."""
    secret_terms = ("api_key", "apikey", "authorization", "password", "secret", "token")
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if any(term in str(key).casefold() for term in secret_terms)
                else redact_secrets(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return [redact_secrets(item) for item in value]
    return value


@dataclass(frozen=True)
class InferenceFingerprint:
    question_digest: str
    agent_digest: str
    # What the model sees: the agent's prompt context and in-scope dbt nodes. Changes
    # elsewhere in the manifest do not invalidate recorded inference.
    scope_digest: str
    semantic_digest: str | None
    model_identity: str
    model_config: dict[str, Any]
    prompt_version: str
    runtime_version: str
    execution_config: dict[str, Any]
    trial: int = 1

    def __post_init__(self) -> None:
        if self.trial < 1:
            raise InferenceError("The trial number must be positive")
        object.__setattr__(self, "model_config", redact_secrets(self.model_config))
        object.__setattr__(self, "execution_config", redact_secrets(self.execution_config))

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_digest": self.question_digest,
            "agent_digest": self.agent_digest,
            "scope_digest": self.scope_digest,
            "semantic_digest": self.semantic_digest,
            "model_identity": self.model_identity,
            "model_config": self.model_config,
            "prompt_version": self.prompt_version,
            "runtime_version": self.runtime_version,
            "execution_config": self.execution_config,
            "trial": self.trial,
        }

    @property
    def digest(self) -> str:
        return content_digest(self.to_dict())


@dataclass(frozen=True)
class ModelCallArtifact:
    stage: str
    input_digest: str
    schema_digest: str
    output: dict[str, Any]
    latency_ms: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    retry_count: int = 0
    provider_errors: tuple[str, ...] = ()
    messages: tuple[dict[str, str], ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "stage": self.stage,
            "input_digest": self.input_digest,
            "schema_digest": self.schema_digest,
            "output": self.output,
            "latency_ms": self.latency_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "retry_count": self.retry_count,
            "provider_errors": list(self.provider_errors),
        }
        if self.messages is not None:
            value["messages"] = list(self.messages)
        return value

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ModelCallArtifact:
        raw_messages = payload.get("messages")
        messages = None
        if raw_messages is not None:
            if not isinstance(raw_messages, list):
                raise InferenceError("Inference call messages must be a list")
            messages = tuple(
                {str(key): str(value) for key, value in item.items()}
                for item in raw_messages
                if isinstance(item, dict)
            )
        return cls(
            stage=str(payload.get("stage") or "unknown"),
            input_digest=str(payload.get("input_digest") or ""),
            schema_digest=str(payload.get("schema_digest") or ""),
            output=dict(payload.get("output") or {}),
            latency_ms=float(payload.get("latency_ms") or 0),
            prompt_tokens=payload.get("prompt_tokens"),
            completion_tokens=payload.get("completion_tokens"),
            retry_count=int(payload.get("retry_count") or 0),
            provider_errors=tuple(str(item) for item in payload.get("provider_errors") or ()),
            messages=messages,
        )


@dataclass(frozen=True)
class InferenceOutcome:
    kind: str
    message: str
    usage: Usage = Usage()

    def __post_init__(self) -> None:
        if self.kind not in {"ambiguity", "rejection", "error"}:
            raise InferenceError("Inference outcome kind is invalid")
        if not self.message.strip():
            raise InferenceError("Inference outcome message must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "usage": _typed_value(self.usage),
        }


@dataclass(frozen=True)
class InferenceArtifact:
    fingerprint: InferenceFingerprint
    calls: tuple[ModelCallArtifact, ...]
    trace: Trace | None
    outcome: InferenceOutcome | None
    created_at: str

    def __post_init__(self) -> None:
        if (self.trace is None) == (self.outcome is None):
            raise InferenceError("Inference artifact must contain one trace or one typed outcome")

    @classmethod
    def create(
        cls,
        fingerprint: InferenceFingerprint,
        calls: tuple[ModelCallArtifact, ...],
        trace: Trace,
    ) -> InferenceArtifact:
        return cls(
            fingerprint,
            calls,
            trace,
            None,
            datetime.now(UTC).isoformat(),
        )

    @classmethod
    def create_outcome(
        cls,
        fingerprint: InferenceFingerprint,
        calls: tuple[ModelCallArtifact, ...],
        kind: str,
        message: str,
        usage: Usage,
    ) -> InferenceArtifact:
        return cls(
            fingerprint,
            calls,
            None,
            InferenceOutcome(kind, message, usage),
            datetime.now(UTC).isoformat(),
        )

    def content_payload(self) -> dict[str, Any]:
        trace = _typed_value(self.trace) if self.trace is not None else None
        if trace is not None:
            trace["question"] = f"[HASHED:{self.fingerprint.question_digest}]"
        value = {
            "schema_version": INFERENCE_SCHEMA,
            "fingerprint": self.fingerprint.to_dict(),
            "fingerprint_digest": self.fingerprint.digest,
            "calls": [call.to_dict() for call in self.calls],
            "trace": trace,
            "created_at": self.created_at,
        }
        if self.outcome:
            value["outcome"] = self.outcome.to_dict()
        return value

    @property
    def digest(self) -> str:
        return content_digest(self.content_payload())

    @property
    def id(self) -> str:
        return "inf_" + self.digest.removeprefix("sha256:")[:16]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "digest": self.digest, **self.content_payload()}

    def dump(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> InferenceArtifact:
        require_schema_version(
            payload,
            INFERENCE_SCHEMA,
            resource="inference artifact",
            editable=False,
        )
        raw_fingerprint = payload.get("fingerprint")
        if not isinstance(raw_fingerprint, dict):
            raise InferenceError("Inference artifact fingerprint must be an object")
        fingerprint = InferenceFingerprint(
            question_digest=str(raw_fingerprint.get("question_digest") or ""),
            agent_digest=str(raw_fingerprint.get("agent_digest") or ""),
            scope_digest=str(raw_fingerprint.get("scope_digest") or ""),
            semantic_digest=(
                str(raw_fingerprint["semantic_digest"])
                if raw_fingerprint.get("semantic_digest")
                else None
            ),
            model_identity=str(raw_fingerprint.get("model_identity") or ""),
            model_config=dict(raw_fingerprint.get("model_config") or {}),
            prompt_version=str(raw_fingerprint.get("prompt_version") or ""),
            runtime_version=str(raw_fingerprint.get("runtime_version") or ""),
            execution_config=dict(raw_fingerprint.get("execution_config") or {}),
            trial=int(raw_fingerprint.get("trial") or 1),
        )
        calls = payload.get("calls")
        if not isinstance(calls, list):
            raise InferenceError("Inference artifact calls must be a list")
        raw_trace = payload.get("trace")
        raw_outcome = payload.get("outcome")
        if raw_trace is not None and not isinstance(raw_trace, dict):
            raise InferenceError("Inference artifact trace must be an object or null")
        if raw_outcome is not None and not isinstance(raw_outcome, dict):
            raise InferenceError("Inference artifact outcome must be an object or null")
        outcome = (
            InferenceOutcome(
                str(raw_outcome.get("kind") or ""),
                str(raw_outcome.get("message") or ""),
                Usage(
                    **{
                        **_from_typed_value(dict(raw_outcome.get("usage") or {})),
                        "provider_errors": tuple(
                            raw_outcome.get("usage", {}).get("provider_errors") or ()
                        ),
                    }
                ),
            )
            if isinstance(raw_outcome, dict)
            else None
        )
        artifact = cls(
            fingerprint,
            tuple(ModelCallArtifact.from_dict(item) for item in calls),
            (
                trace_from_dict(_from_typed_value(raw_trace))
                if isinstance(raw_trace, dict)
                else None
            ),
            outcome,
            str(payload.get("created_at") or ""),
        )
        if payload.get("fingerprint_digest") != fingerprint.digest:
            raise InferenceError("Inference artifact fingerprint digest does not match")
        if payload.get("id") != artifact.id or payload.get("digest") != artifact.digest:
            raise InferenceError("Inference artifact content digest does not match")
        return artifact


class InferenceStore:
    def __init__(self, directory: str | Path) -> None:
        self.root = Path(directory)

    def _index_path(self, fingerprint: InferenceFingerprint) -> Path:
        name = fingerprint.digest.removeprefix("sha256:")
        return self.root / "cache" / f"{name}.json"

    def get(self, fingerprint: InferenceFingerprint) -> InferenceArtifact | None:
        index_path = self._index_path(fingerprint)
        if not index_path.is_file():
            return None
        try:
            index = json.loads(index_path.read_text())
            artifact_id = str(index["artifact_id"])
            artifact_path = self.root / "artifacts" / f"{artifact_id}.json"
            payload = json.loads(artifact_path.read_text())
        except (OSError, KeyError, json.JSONDecodeError) as exc:
            raise InferenceError(f"Inference cache record is corrupt: {index_path}") from exc
        if content_digest(canonical_json_bytes(index["fingerprint"])) != fingerprint.digest:
            raise InferenceError(f"Inference cache fingerprint is corrupt: {index_path}")
        artifact = InferenceArtifact.from_dict(payload)
        if index.get("artifact_digest") != artifact.digest:
            raise InferenceError(f"Inference cache artifact digest is corrupt: {index_path}")
        if artifact.fingerprint != fingerprint:
            raise InferenceError(f"Inference cache points to a different fingerprint: {index_path}")
        return artifact

    def put(self, artifact: InferenceArtifact) -> Path:
        artifact_directory = self.root / "artifacts"
        cache_directory = self.root / "cache"
        artifact_directory.mkdir(parents=True, exist_ok=True)
        cache_directory.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_directory / f"{artifact.id}.json"
        rendered = artifact.dump()
        if artifact_path.exists() and artifact_path.read_text() != rendered:
            raise InferenceError(f"Inference artifact path has different content: {artifact_path}")
        artifact_path.write_text(rendered)
        index = {
            "fingerprint": artifact.fingerprint.to_dict(),
            "artifact_id": artifact.id,
            "artifact_digest": artifact.digest,
        }
        self._index_path(artifact.fingerprint).write_text(
            json.dumps(index, indent=2, sort_keys=True) + "\n"
        )
        return artifact_path


class RecordingLLM(LLMProvider):
    """Record structured model calls without changing the provider contract."""

    def __init__(self, provider: LLMProvider, *, persist_prompts: bool = False) -> None:
        super().__init__()
        self.provider = provider
        self.persist_prompts = persist_prompts
        self.calls: list[ModelCallArtifact] = []

    def generate_response(self, prompt: str) -> str:
        return self.provider.generate_response(prompt)

    def generate_structured(
        self,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
    ) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            output = self.provider.generate_structured(messages, json_schema)
        except Exception as exc:
            usage = getattr(self.provider, "last_usage", {}) or {}
            self.calls.append(
                ModelCallArtifact(
                    stage="query" if not self.calls else "answer",
                    input_digest=content_digest(messages),
                    schema_digest=content_digest(json_schema),
                    output={},
                    latency_ms=(time.perf_counter() - started) * 1000,
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    retry_count=int(getattr(self.provider, "last_retry_count", 0)),
                    provider_errors=(type(exc).__name__,),
                    messages=tuple(messages) if self.persist_prompts else None,
                )
            )
            raise
        usage = getattr(self.provider, "last_usage", {}) or {}
        self.calls.append(
            ModelCallArtifact(
                stage="query" if not self.calls else "answer",
                input_digest=content_digest(messages),
                schema_digest=content_digest(json_schema),
                output=output,
                latency_ms=(time.perf_counter() - started) * 1000,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                retry_count=int(getattr(self.provider, "last_retry_count", 0)),
                provider_errors=tuple(getattr(self.provider, "last_provider_errors", ())),
                messages=tuple(messages) if self.persist_prompts else None,
            )
        )
        self.last_usage = {
            "prompt_tokens": sum(call.prompt_tokens or 0 for call in self.calls),
            "completion_tokens": sum(call.completion_tokens or 0 for call in self.calls),
        }
        return output
