"""Trusted semantic knowledge and explicit status transitions."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from tabletalk.schemas import SEMANTIC_SCHEMA, require_schema_version


class SemanticError(ValueError):
    """A semantic statement violates a trust or serialization rule."""


class SemanticStatus(StrEnum):
    TRUSTED = "trusted"
    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    MISSING = "missing"
    CONFLICTING = "conflicting"


class EvidenceKind(StrEnum):
    DBT_ARTIFACT = "dbt_artifact"
    SQL_IMPORT = "sql_import"
    BI_QUERY_IMPORT = "bi_query_import"
    QUERY_HISTORY_IMPORT = "query_history_import"
    APPROVED_SQL = "approved_sql"
    APPROVED_BI_QUERY = "approved_bi_query"
    APPROVED_QUERY_HISTORY = "approved_query_history"
    USER_ANSWER = "user_answer"
    LLM_INFERENCE = "llm_inference"
    EXECUTION_OBSERVATION = "execution_observation"


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_STATEMENT_ID = re.compile(r"[a-z][a-z0-9_]*\Z")


@dataclass(frozen=True)
class EvidenceRef:
    kind: EvidenceKind
    reference: str
    digest: str
    locator: str = "/"

    def __post_init__(self) -> None:
        if not self.reference.strip():
            raise SemanticError("Semantic evidence requires a source reference")
        if _DIGEST.fullmatch(self.digest) is None:
            raise SemanticError("Semantic evidence requires a complete SHA-256 digest")
        if not self.locator:
            raise SemanticError("Semantic evidence requires a source locator")

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind.value,
            "reference": self.reference,
            "digest": self.digest,
            "locator": self.locator,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> EvidenceRef:
        try:
            return cls(
                EvidenceKind(str(payload["kind"])),
                str(payload["reference"]),
                str(payload["digest"]),
                str(payload.get("locator") or "/"),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise SemanticError(f"Invalid semantic evidence: {exc}") from exc


@dataclass(frozen=True)
class ApprovalRef:
    id: str
    action: str = "confirm"

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise SemanticError("A semantic approval requires an ID")
        if self.action not in {"confirm", "store_untrusted"}:
            raise SemanticError("A semantic approval action must be confirm or store_untrusted")

    def to_value(self) -> str | dict[str, str]:
        if self.action == "confirm":
            return self.id
        return {"id": self.id, "action": self.action}

    @classmethod
    def from_value(cls, value: Any) -> ApprovalRef | None:
        if value is None:
            return None
        if isinstance(value, str):
            return cls(value)
        if isinstance(value, dict):
            return cls(str(value.get("id") or ""), str(value.get("action") or "confirm"))
        raise SemanticError("Semantic approval must be a string or mapping")


@dataclass(frozen=True)
class SemanticStatement:
    id: str
    status: SemanticStatus
    definition: str = ""
    expression: str | None = None
    time_column: str | None = None
    filters: tuple[str, ...] = ()
    approval: ApprovalRef | None = None
    evidence: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        if _STATEMENT_ID.fullmatch(self.id) is None:
            raise SemanticError(
                "A semantic statement ID must use lowercase letters, numbers, and underscores"
            )
        if self.status in {SemanticStatus.TRUSTED, SemanticStatus.CONFIRMED}:
            if not self.definition.strip():
                raise SemanticError("A trusted or confirmed statement requires a definition")
        if self.status is SemanticStatus.CONFIRMED:
            if self.approval is None or self.approval.action != "confirm":
                raise SemanticError("A confirmed statement requires a confirm approval")
        if self.status is SemanticStatus.INFERRED and self.approval is not None:
            if self.approval.action != "store_untrusted":
                raise SemanticError("An inferred statement can only use store_untrusted approval")
        if self.status is SemanticStatus.TRUSTED and self.evidence:
            untrusted = {
                EvidenceKind.LLM_INFERENCE,
                EvidenceKind.EXECUTION_OBSERVATION,
            }
            if all(item.kind in untrusted for item in self.evidence):
                raise SemanticError("Model or execution evidence alone cannot be trusted")

    @property
    def can_support_answer(self) -> bool:
        return self.status in {SemanticStatus.TRUSTED, SemanticStatus.CONFIRMED}

    def confirm(self, approval_id: str, answer_evidence: EvidenceRef) -> SemanticStatement:
        if answer_evidence.kind is not EvidenceKind.USER_ANSWER:
            raise SemanticError("Confirmation requires user-answer evidence")
        return replace(
            self,
            status=SemanticStatus.CONFIRMED,
            approval=ApprovalRef(approval_id, "confirm"),
            evidence=self.evidence + (answer_evidence,),
        )

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "status": self.status.value,
        }
        if self.definition:
            value["definition"] = self.definition
        if self.expression:
            value["expression"] = self.expression
        if self.time_column:
            value["time_column"] = self.time_column
        if self.filters:
            value["filters"] = list(self.filters)
        if self.approval:
            value["approval"] = self.approval.to_value()
        if self.evidence:
            value["evidence"] = [item.to_dict() for item in self.evidence]
        return value

    @classmethod
    def from_dict(cls, statement_id: str, payload: dict[str, Any]) -> SemanticStatement:
        try:
            filters = payload.get("filters") or []
            if not isinstance(filters, list) or not all(isinstance(item, str) for item in filters):
                raise SemanticError("Semantic filters must be a list of strings")
            evidence = payload.get("evidence") or []
            if not isinstance(evidence, list) or not all(
                isinstance(item, dict) for item in evidence
            ):
                raise SemanticError("Semantic evidence must be a list of mappings")
            return cls(
                id=statement_id,
                status=SemanticStatus(str(payload["status"])),
                definition=str(payload.get("definition") or ""),
                expression=str(payload["expression"]) if payload.get("expression") else None,
                time_column=(
                    str(payload["time_column"]) if payload.get("time_column") else None
                ),
                filters=tuple(filters),
                approval=ApprovalRef.from_value(payload.get("approval")),
                evidence=tuple(EvidenceRef.from_dict(item) for item in evidence),
            )
        except (KeyError, ValueError, TypeError, SemanticError) as exc:
            raise SemanticError(f"Invalid semantic statement '{statement_id}': {exc}") from exc


@dataclass(frozen=True)
class SemanticKnowledge:
    statements: tuple[SemanticStatement, ...]
    source_path: Path | None = None

    def __post_init__(self) -> None:
        ids = [item.id for item in self.statements]
        if len(ids) != len(set(ids)):
            raise SemanticError("Semantic statement IDs must be unique")

    def get(self, statement_id: str) -> SemanticStatement:
        matches = [item for item in self.statements if item.id == statement_id]
        if not matches:
            raise SemanticError(f"Semantic statement '{statement_id}' was not found")
        return matches[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SEMANTIC_SCHEMA,
            "statements": {
                item.id: item.to_dict()
                for item in sorted(self.statements, key=lambda item: item.id)
            },
        }

    def dump(self) -> str:
        return yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True)

    @classmethod
    def load(cls, path: str | Path) -> SemanticKnowledge:
        source = Path(path).resolve()
        try:
            payload = yaml.safe_load(source.read_text())
        except (OSError, yaml.YAMLError) as exc:
            raise SemanticError(f"Could not load semantic knowledge {source}: {exc}") from exc
        if not isinstance(payload, dict):
            raise SemanticError(f"Semantic knowledge {source} must be a YAML mapping")
        require_schema_version(payload, SEMANTIC_SCHEMA, resource=str(source))
        raw_statements = payload.get("statements")
        if not isinstance(raw_statements, dict):
            raise SemanticError("Semantic knowledge requires a statements mapping")
        statements = []
        for statement_id, raw in raw_statements.items():
            if not isinstance(raw, dict):
                raise SemanticError(f"Semantic statement '{statement_id}' must be a mapping")
            statements.append(SemanticStatement.from_dict(str(statement_id), raw))
        return cls(tuple(statements), source)
