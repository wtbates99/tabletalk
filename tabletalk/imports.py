"""Explicit, non-executing import boundary for SQL and query exports."""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from tabletalk.manifest import Manifest
from tabletalk.schemas import content_digest
from tabletalk.semantics import EvidenceKind, EvidenceRef, SemanticStatus
from tabletalk.validation import SQLValidationError, validate_sql


class EvidenceImportError(ValueError):
    """Explicit imported material cannot pass the safe preview boundary."""


class ImportKind(str, Enum):
    SQL = "sql"
    BI_QUERY = "bi_query"
    QUERY_HISTORY = "query_history"


_QUERY_FIELDS = ("sql", "query", "query_text", "statement")
_REF = re.compile(r"\{\{\s*ref\(['\"]([^'\"]+)['\"]\)\s*\}\}")


def _evidence_kind(kind: ImportKind, *, approved: bool) -> EvidenceKind:
    if approved:
        return {
            ImportKind.SQL: EvidenceKind.APPROVED_SQL,
            ImportKind.BI_QUERY: EvidenceKind.APPROVED_BI_QUERY,
            ImportKind.QUERY_HISTORY: EvidenceKind.APPROVED_QUERY_HISTORY,
        }[kind]
    return {
        ImportKind.SQL: EvidenceKind.SQL_IMPORT,
        ImportKind.BI_QUERY: EvidenceKind.BI_QUERY_IMPORT,
        ImportKind.QUERY_HISTORY: EvidenceKind.QUERY_HISTORY_IMPORT,
    }[kind]


@dataclass(frozen=True)
class QueryImportPreview:
    kind: ImportKind
    source_path: Path
    source_digest: str
    locator: str
    normalized_sql: str
    redacted_sql: str
    resource_ids: tuple[str, ...]
    columns: tuple[str, ...]
    literal_count: int
    status: SemanticStatus = SemanticStatus.INFERRED
    executed: bool = False

    def evidence(self, *, approved: bool = False) -> EvidenceRef:
        return EvidenceRef(
            _evidence_kind(self.kind, approved=approved),
            str(self.source_path),
            self.source_digest,
            self.locator,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "source_path": str(self.source_path),
            "source_digest": self.source_digest,
            "locator": self.locator,
            "normalized_sql": self.normalized_sql,
            "redacted_sql": self.redacted_sql,
            "resource_ids": list(self.resource_ids),
            "columns": list(self.columns),
            "literal_count": self.literal_count,
            "status": self.status.value,
            "executed": self.executed,
        }


def _render_refs(sql: str, manifest: Manifest) -> str:
    def replace(match: re.Match[str]) -> str:
        nodes = tuple(node for node in manifest.queryable_nodes if node.name == match.group(1))
        if len(nodes) != 1:
            raise EvidenceImportError(f"dbt ref('{match.group(1)}') is missing or ambiguous")
        node = nodes[0]
        return node.relation_name or f"{node.schema}.{node.alias}".strip(".")

    rendered = _REF.sub(replace, sql)
    if "{{" in rendered or "{%" in rendered:
        raise EvidenceImportError(
            "Imported SQL can contain dbt ref() but no other template expression"
        )
    return rendered


def _extract_json_queries(value: Any) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    if isinstance(value, list):
        values = value
    elif isinstance(value, dict):
        values = value.get("queries", [value])
    else:
        values = []
    if not isinstance(values, list):
        return result
    for index, item in enumerate(values):
        if isinstance(item, str):
            result.append((f"/{index}", item))
            continue
        if not isinstance(item, dict):
            continue
        field = next((name for name in _QUERY_FIELDS if isinstance(item.get(name), str)), None)
        if field:
            result.append((f"/{index}/{field}", str(item[field])))
    return result


def _extract_queries(path: Path, kind: ImportKind) -> list[tuple[str, str]]:
    text = path.read_text()
    if kind is ImportKind.SQL:
        return [("/", text)]
    if path.suffix.casefold() == ".json":
        try:
            return _extract_json_queries(json.loads(text))
        except json.JSONDecodeError as exc:
            raise EvidenceImportError(f"Imported {kind.value} JSON is invalid: {exc}") from exc
    if path.suffix.casefold() == ".csv":
        rows = list(csv.DictReader(io.StringIO(text)))
        result = []
        for index, row in enumerate(rows):
            field = next((name for name in _QUERY_FIELDS if row.get(name)), None)
            if field:
                result.append((f"/{index}/{field}", str(row[field])))
        return result
    return [("/", text)]


def _redact_literals(sql: str, dialect: str) -> tuple[str, int]:
    tree = parse_one(sql, read=dialect)
    literals = tuple(tree.find_all(exp.Literal))
    redacted = tree.transform(
        lambda node: exp.Placeholder() if isinstance(node, exp.Literal) else node
    )
    return redacted.sql(dialect=dialect, pretty=True) + "\n", len(literals)


def preview_import(
    path: str | Path,
    kind: ImportKind,
    manifest: Manifest,
    *,
    dialect: str,
) -> tuple[QueryImportPreview, ...]:
    """Parse and attribute explicit query material without warehouse execution."""
    source_reference = Path(path).expanduser()
    source = source_reference.resolve()
    if not source.is_file():
        raise EvidenceImportError(f"Imported {kind.value} file was not found: {source}")
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise EvidenceImportError(
            f"Could not read imported {kind.value} file {source}: {exc}"
        ) from exc
    source_digest = content_digest(raw)
    try:
        queries = _extract_queries(source, kind)
    except (OSError, UnicodeDecodeError) as exc:
        raise EvidenceImportError(
            f"Could not read imported {kind.value} file {source}: {exc}"
        ) from exc
    if not queries:
        raise EvidenceImportError(f"Imported {kind.value} file contains no query field")

    sensitive_permissions = [node.unique_id for node in manifest.queryable_nodes]
    sensitive_permissions.extend(
        column.name for node in manifest.queryable_nodes for column in node.columns.values()
    )
    result = []
    for locator, query in queries:
        if not query.strip():
            raise EvidenceImportError(f"Imported query at {locator} is empty")
        rendered = _render_refs(query, manifest)
        try:
            validated = validate_sql(
                rendered,
                manifest,
                manifest.queryable_nodes,
                dialect=dialect,
                max_rows=1_000_000_000,
                allow_sensitive=sensitive_permissions,
            )
            tree = parse_one(rendered, read=dialect)
            normalized = (
                query.strip() + "\n"
                if _REF.search(query)
                else tree.sql(dialect=dialect, pretty=True) + "\n"
            )
            redacted, literal_count = _redact_literals(rendered, dialect)
        except (SQLValidationError, ParseError, ValueError) as exc:
            raise EvidenceImportError(f"Imported query at {locator} is not safe: {exc}") from exc
        result.append(
            QueryImportPreview(
                kind,
                source_reference,
                source_digest,
                locator,
                normalized,
                redacted,
                tuple(node.unique_id for node in validated.nodes),
                validated.columns,
                literal_count,
            )
        )
    return tuple(result)
