"""Typed, user-visible failures for the reliability runtime."""

from __future__ import annotations

from enum import Enum
from typing import Any


class RuntimeStage(str, Enum):
    CONFIGURATION = "configuration"
    COMPILATION = "compilation"
    INTERPRETATION = "interpretation"
    PLANNING = "planning"
    GENERATION = "generation"
    VALIDATION = "validation"
    EXECUTION = "execution"
    VERIFICATION = "verification"


class ErrorCode(str, Enum):
    CONFIG_INVALID = "config_invalid"
    MODEL_UNAVAILABLE = "model_unavailable"
    MODEL_AUTHENTICATION_FAILED = "model_authentication_failed"
    MODEL_REQUEST_FAILED = "model_request_failed"
    MODEL_OUTPUT_MALFORMED = "model_output_malformed"
    CLARIFICATION_REQUIRED = "clarification_required"
    SEMANTIC_INVALID = "semantic_invalid"
    SQL_INVALID = "sql_invalid"
    SQL_OUT_OF_SCOPE = "sql_out_of_scope"
    SQL_NOT_READ_ONLY = "sql_not_read_only"
    DATABASE_UNAVAILABLE = "database_unavailable"
    DATABASE_QUERY_FAILED = "database_query_failed"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    REQUIRED_EVAL_MISSING = "required_eval_missing"
    REQUIRED_EVAL_FAILED = "required_eval_failed"


class TableTalkError(RuntimeError):
    """A stage-specific failure that is safe to serialize for callers.

    ``details`` must contain diagnostic metadata, never credentials, raw
    connection strings, request headers, or environment values.
    """

    def __init__(
        self,
        code: ErrorCode,
        stage: RuntimeStage,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.message = message
        self.retryable = retryable
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "stage": self.stage.value,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


def model_request_error(
    error: Exception,
    *,
    provider: str,
    model: str,
    stage: RuntimeStage = RuntimeStage.GENERATION,
) -> TableTalkError:
    """Translate an SDK/transport error without exposing request secrets."""
    if isinstance(error, TableTalkError):
        return error

    exception_type = type(error).__name__
    status_code = getattr(error, "status_code", None)
    details: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "exception_type": exception_type,
    }
    if isinstance(status_code, int):
        details["status_code"] = status_code

    lowered = exception_type.lower()
    if status_code in {401, 403} or "authentication" in lowered:
        return TableTalkError(
            ErrorCode.MODEL_AUTHENTICATION_FAILED,
            stage,
            f"Authentication failed for configured model '{model}'.",
            details=details,
        )
    if (
        status_code == 404
        or "connection" in lowered
        or "timeout" in lowered
        or "notfound" in lowered
    ):
        return TableTalkError(
            ErrorCode.MODEL_UNAVAILABLE,
            stage,
            f"Configured model '{model}' is unavailable.",
            retryable=status_code != 404,
            details=details,
        )
    return TableTalkError(
        ErrorCode.MODEL_REQUEST_FAILED,
        stage,
        f"Configured model '{model}' request failed.",
        retryable=bool(status_code and status_code >= 500),
        details=details,
    )
