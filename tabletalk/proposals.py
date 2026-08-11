"""Immutable proposal, approval, and field-provenance records."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from tabletalk.schemas import (
    APPROVAL_SCHEMA,
    PROPOSAL_SCHEMA,
    PROVENANCE_SCHEMA,
    canonical_json_bytes,
    content_digest,
    require_schema_version,
)
from tabletalk.semantics import EvidenceRef, SemanticError


class ProposalError(ValueError):
    """A proposal or approval violates an immutable-content rule."""


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ApprovalAction(str, Enum):
    APPROVE_ALL = "approve_all"
    APPROVE_SELECTED = "approve_selected"


def _resource_path(value: str) -> str:
    raw = value.replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts or raw.startswith(".tabletalk/"):
        raise ProposalError(f"Proposal path '{value}' must be a project-relative resource path")
    return str(path)


def _pointer_part(value: Any) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def leaf_pointers(value: Any, base: str = "") -> set[str]:
    if isinstance(value, dict) and value:
        result: set[str] = set()
        for key, item in value.items():
            result.update(leaf_pointers(item, base + "/" + _pointer_part(key)))
        return result
    if isinstance(value, list) and value:
        result = set()
        for index, item in enumerate(value):
            result.update(leaf_pointers(item, base + f"/{index}"))
        return result
    return {base or "/"}


@dataclass(frozen=True)
class FieldEvidence:
    path: str
    confidence: Confidence
    evidence: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if not self.path.startswith("/"):
            raise ProposalError("A field-evidence path must be a JSON pointer")
        if not self.evidence:
            raise ProposalError(f"Proposal field '{self.path}' requires source evidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "confidence": self.confidence.value,
            "evidence": [item.to_dict() for item in self.evidence],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FieldEvidence:
        try:
            raw_evidence = payload["evidence"]
            if not isinstance(raw_evidence, list):
                raise ProposalError("Field evidence must be a list")
            return cls(
                str(payload["path"]),
                Confidence(str(payload["confidence"])),
                tuple(EvidenceRef.from_dict(item) for item in raw_evidence),
            )
        except (KeyError, TypeError, ValueError, SemanticError) as exc:
            raise ProposalError(f"Invalid field evidence: {exc}") from exc


@dataclass(frozen=True)
class ProposedFile:
    path: str
    content: str
    fields: tuple[FieldEvidence, ...]
    base_sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _resource_path(self.path))
        object.__setattr__(self, "fields", tuple(sorted(self.fields, key=lambda item: item.path)))
        if not self.content:
            raise ProposalError(f"Proposal file '{self.path}' has no content")
        paths = [item.path for item in self.fields]
        if len(paths) != len(set(paths)):
            raise ProposalError(f"Proposal file '{self.path}' has duplicate field evidence")
        expected = self.leaf_pointers
        actual = set(paths)
        if expected != actual:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            details = []
            if missing:
                details.append("missing " + ", ".join(missing))
            if extra:
                details.append("unexpected " + ", ".join(extra))
            raise ProposalError(
                f"Proposal file '{self.path}' has incomplete field provenance: "
                + ". ".join(details)
            )

    @property
    def digest(self) -> str:
        return content_digest(self.content)

    @property
    def leaf_pointers(self) -> set[str]:
        suffix = PurePosixPath(self.path).suffix.lower()
        try:
            if suffix in {".yaml", ".yml"}:
                value = yaml.safe_load(self.content)
            elif suffix == ".json":
                value = json.loads(self.content)
            else:
                return {"/"}
        except (yaml.YAMLError, json.JSONDecodeError) as exc:
            raise ProposalError(f"Proposal file '{self.path}' cannot be parsed: {exc}") from exc
        return leaf_pointers(value)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "path": self.path,
            "sha256": self.digest,
            "content": self.content,
            "fields": [item.to_dict() for item in sorted(self.fields, key=lambda item: item.path)],
        }
        if self.base_sha256:
            payload["base_sha256"] = self.base_sha256
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ProposedFile:
        try:
            fields = payload["fields"]
            if not isinstance(fields, list):
                raise ProposalError("Proposed-file fields must be a list")
            value = cls(
                str(payload["path"]),
                str(payload["content"]),
                tuple(FieldEvidence.from_dict(item) for item in fields),
                str(payload["base_sha256"]) if payload.get("base_sha256") else None,
            )
            if payload.get("sha256") != value.digest:
                raise ProposalError(f"Proposal file '{value.path}' has a content-digest mismatch")
            return value
        except (KeyError, TypeError, ValueError) as exc:
            raise ProposalError(f"Invalid proposed file: {exc}") from exc


@dataclass(frozen=True)
class ProposalRecord:
    files: tuple[ProposedFile, ...]
    manifest_digest: str
    semantic_digest: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", tuple(sorted(self.files, key=lambda item: item.path)))
        if not self.files:
            raise ProposalError("A proposal requires at least one file")
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ProposalError("Proposal file paths must be unique")

    def content_payload(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema_version": PROPOSAL_SCHEMA,
            "manifest_digest": self.manifest_digest,
            "files": [item.to_dict() for item in sorted(self.files, key=lambda item: item.path)],
        }
        if self.semantic_digest:
            value["semantic_digest"] = self.semantic_digest
        return value

    @property
    def digest(self) -> str:
        return content_digest(self.content_payload())

    @property
    def id(self) -> str:
        return "prp_" + self.digest.removeprefix("sha256:")[:12]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "digest": self.digest, **self.content_payload()}

    def dump(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    def write(self, directory: str | Path) -> Path:
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"{self.id}.json"
        rendered = self.dump()
        if target.exists() and target.read_text() != rendered:
            raise ProposalError(f"Proposal path {target} contains different content")
        target.write_text(rendered)
        return target

    @classmethod
    def load(cls, path: str | Path) -> ProposalRecord:
        source = Path(path).resolve()
        try:
            payload = json.loads(source.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ProposalError(f"Could not load proposal {source}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ProposalError(f"Proposal {source} must be a JSON object")
        require_schema_version(
            payload,
            PROPOSAL_SCHEMA,
            resource=str(source),
            editable=False,
        )
        raw_files = payload.get("files")
        if not isinstance(raw_files, list):
            raise ProposalError("Proposal files must be a list")
        record = cls(
            tuple(ProposedFile.from_dict(item) for item in raw_files),
            str(payload.get("manifest_digest") or ""),
            str(payload["semantic_digest"]) if payload.get("semantic_digest") else None,
        )
        if payload.get("id") != record.id or payload.get("digest") != record.digest:
            raise ProposalError(f"Proposal {source} has a record-digest mismatch")
        return record


@dataclass(frozen=True)
class ApprovedFile:
    path: str
    sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _resource_path(self.path))

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256}


@dataclass(frozen=True)
class ApprovalRecord:
    proposal_digest: str
    action: ApprovalAction
    recorded_at: str
    actor: str
    files: tuple[ApprovedFile, ...]

    def __post_init__(self) -> None:
        if not self.files:
            raise ProposalError("An approval requires at least one approved file")
        try:
            datetime.fromisoformat(self.recorded_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ProposalError("Approval recorded_at must use ISO 8601") from exc
        if not self.actor.strip():
            raise ProposalError("An approval requires an actor")

    @classmethod
    def from_proposal(
        cls,
        proposal: ProposalRecord,
        *,
        recorded_at: str,
        actor: str,
        selected_paths: tuple[str, ...] | None = None,
    ) -> ApprovalRecord:
        selected = set(selected_paths or (item.path for item in proposal.files))
        available = {item.path: item for item in proposal.files}
        unknown = selected - set(available)
        if unknown:
            raise ProposalError("Approval selected unknown files: " + ", ".join(sorted(unknown)))
        files = tuple(
            ApprovedFile(path, available[path].digest) for path in sorted(selected)
        )
        action = (
            ApprovalAction.APPROVE_ALL
            if selected == set(available)
            else ApprovalAction.APPROVE_SELECTED
        )
        return cls(proposal.digest, action, recorded_at, actor, files)

    def content_payload(self) -> dict[str, Any]:
        return {
            "schema_version": APPROVAL_SCHEMA,
            "proposal_digest": self.proposal_digest,
            "action": self.action.value,
            "recorded_at": self.recorded_at,
            "actor": self.actor,
            "files": [item.to_dict() for item in self.files],
        }

    @property
    def digest(self) -> str:
        return content_digest(self.content_payload())

    @property
    def id(self) -> str:
        return "apr_" + self.digest.removeprefix("sha256:")[:12]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "digest": self.digest, **self.content_payload()}


@dataclass(frozen=True)
class ProvenanceRecord:
    approval: ApprovalRecord
    files: tuple[ProposedFile, ...]

    def __post_init__(self) -> None:
        proposed = {item.path: item for item in self.files}
        for approved in self.approval.files:
            item = proposed.get(approved.path)
            if item is None or item.digest != approved.sha256:
                raise ProposalError(
                    f"Approved file '{approved.path}' does not match proposal content"
                )

    def to_dict(self) -> dict[str, Any]:
        approved_paths = {item.path for item in self.approval.files}
        return {
            "schema_version": PROVENANCE_SCHEMA,
            "approval": self.approval.to_dict(),
            "files": {
                item.path: {
                    "sha256": item.digest,
                    **(
                        {"base_sha256": item.base_sha256}
                        if item.base_sha256
                        else {}
                    ),
                    "fields": {
                        field.path: {
                            "confidence": field.confidence.value,
                            "evidence": [evidence.to_dict() for evidence in field.evidence],
                        }
                        for field in sorted(item.fields, key=lambda field: field.path)
                    },
                }
                for item in sorted(self.files, key=lambda item: item.path)
                if item.path in approved_paths
            },
        }

    def dump(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    @property
    def digest(self) -> str:
        return content_digest(canonical_json_bytes(self.to_dict()))
