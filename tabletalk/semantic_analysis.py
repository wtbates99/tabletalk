"""Deterministic semantic-quality analysis over dbt artifacts and approved knowledge."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from tabletalk.manifest import Manifest, Node
from tabletalk.schemas import content_digest
from tabletalk.semantics import (
    EvidenceKind,
    EvidenceRef,
    SemanticStatement,
    SemanticStatus,
)


class FindingCode(StrEnum):
    MISSING_MODEL_DESCRIPTION = "missing_model_description"
    MISSING_COLUMN_DESCRIPTION = "missing_column_description"
    MISSING_RELATIONSHIP_EVIDENCE = "missing_relationship_evidence"
    MISSING_METRIC_DEFINITION = "missing_metric_definition"
    AMBIGUOUS_TIME_BASIS = "ambiguous_time_basis"
    SUSPECTED_SENSITIVE_DATA = "suspected_sensitive_data"
    DUPLICATE_RELATION_NAME = "duplicate_relation_name"
    CONFLICTING_STATEMENT = "conflicting_statement"


@dataclass(frozen=True)
class SemanticQuality:
    resources: int
    documented_resources: int
    columns: int
    documented_columns: int
    relationship_tests: int
    semantic_models: int
    metrics: int
    exposures: int
    sensitive_resources: int

    def to_dict(self) -> dict[str, int]:
        return {
            "resources": self.resources,
            "documented_resources": self.documented_resources,
            "columns": self.columns,
            "documented_columns": self.documented_columns,
            "relationship_tests": self.relationship_tests,
            "semantic_models": self.semantic_models,
            "metrics": self.metrics,
            "exposures": self.exposures,
            "sensitive_resources": self.sensitive_resources,
        }


@dataclass(frozen=True)
class SemanticGap:
    id: str
    code: FindingCode
    status: SemanticStatus
    message: str
    question: str | None
    options: tuple[str, ...]
    blocked_questions: tuple[str, ...]
    evidence: tuple[EvidenceRef, ...]
    owner: str = "dbt_metadata"

    @property
    def priority(self) -> int:
        return len(self.blocked_questions)

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "id": self.id,
            "code": self.code.value,
            "status": self.status.value,
            "message": self.message,
            "blocked_questions": list(self.blocked_questions),
            "owner": self.owner,
            "evidence": [item.to_dict() for item in self.evidence],
        }
        if self.question:
            value["question"] = self.question
            value["options"] = list(self.options)
        return value


@dataclass(frozen=True)
class SemanticAnalysis:
    manifest_digest: str
    resource_ids: tuple[str, ...]
    quality: SemanticQuality
    gaps: tuple[SemanticGap, ...]

    @property
    def digest(self) -> str:
        return content_digest(self.to_dict())

    @property
    def questions(self) -> tuple[SemanticGap, ...]:
        return tuple(
            sorted(
                (item for item in self.gaps if item.question),
                key=lambda item: (-item.priority, item.id),
            )
        )

    def high_value_questions(self, limit: int = 3) -> tuple[SemanticGap, ...]:
        if limit < 0:
            raise ValueError("The semantic-question limit must not be negative")
        return self.questions[:limit]

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_digest": self.manifest_digest,
            "resource_ids": list(self.resource_ids),
            "quality": self.quality.to_dict(),
            "gaps": [item.to_dict() for item in self.gaps],
        }


_SENSITIVE_TERMS = (
    "customer id",
    "customer identity",
    "email",
    "phone",
    "personally identifiable",
    "sensitive",
    "social security",
    "ssn",
)
_MEASURE_TERMS = ("amount", "cost", "margin", "price", "revenue", "sales")
_TIME_TERMS = ("_at", "_date", "date_", "time")


def _evidence(manifest: Manifest, locator: str) -> EvidenceRef:
    reference = manifest.path.name
    for parent in manifest.path.parents:
        if (parent / "dbt_project.yml").is_file():
            reference = str(manifest.path.relative_to(parent))
            break
    return EvidenceRef(
        EvidenceKind.DBT_ARTIFACT,
        reference,
        manifest.digest if manifest.digest.startswith("sha256:") else "sha256:" + manifest.digest,
        locator,
    )


def _has_relationship_test(node: Node) -> bool:
    return any("relationship" in test.name.casefold() for test in node.tests)


def _sensitive(node: Node) -> bool:
    if node.meta.get("sensitive") is True:
        return True
    return any(column.meta.get("sensitive") is True for column in node.columns.values())


def _suspected_sensitive(node: Node) -> tuple[str, ...]:
    result = []
    description_is_sensitive = any(term in node.description.casefold() for term in _SENSITIVE_TERMS)
    if not _sensitive(node) and description_is_sensitive:
        result.append(node.unique_id)
    for column in node.columns.values():
        text = f"{column.name} {column.description}".replace("_", " ").casefold()
        if column.meta.get("sensitive") is not True and any(
            term in text for term in _SENSITIVE_TERMS
        ):
            result.append(f"{node.unique_id}.{column.name}")
    return tuple(result)


def _statement_signature(statement: SemanticStatement) -> tuple[Any, ...]:
    return (
        " ".join(statement.definition.split()).casefold(),
        " ".join((statement.expression or "").split()).casefold(),
        (statement.time_column or "").casefold(),
        tuple(" ".join(item.split()).casefold() for item in statement.filters),
    )


def conflicting_statements(
    statements: Iterable[SemanticStatement],
) -> tuple[SemanticStatement, ...]:
    """Return one explicit conflict for each statement ID with incompatible meanings."""
    grouped: dict[str, list[SemanticStatement]] = defaultdict(list)
    for statement in statements:
        if statement.status in {SemanticStatus.TRUSTED, SemanticStatus.CONFIRMED}:
            grouped[statement.id].append(statement)
    result = []
    for statement_id, values in grouped.items():
        signatures = {_statement_signature(item) for item in values}
        if len(signatures) < 2:
            continue
        result.append(
            SemanticStatement(
                statement_id,
                SemanticStatus.CONFLICTING,
                f"Approved sources give incompatible meanings for {statement_id}.",
                evidence=tuple(item for value in values for item in value.evidence),
            )
        )
    return tuple(sorted(result, key=lambda item: item.id))


def analyze_semantics(
    manifest: Manifest,
    scope: Iterable[Node] | None = None,
    statements: Iterable[SemanticStatement] = (),
) -> SemanticAnalysis:
    """Describe semantic quality and rank concrete questions without model calls."""
    nodes = tuple(sorted(scope or manifest.queryable_nodes, key=lambda node: node.unique_id))
    node_ids = {node.unique_id for node in nodes}
    columns = tuple((node, column) for node in nodes for column in node.columns.values())
    relationship_tests = sum(_has_relationship_test(node) for node in nodes)
    gaps: list[SemanticGap] = []

    for node in nodes:
        locator = f"/nodes/{node.unique_id}"
        if not node.description:
            gaps.append(
                SemanticGap(
                    f"model_description:{node.unique_id}",
                    FindingCode.MISSING_MODEL_DESCRIPTION,
                    SemanticStatus.MISSING,
                    f"{node.unique_id} has no dbt description.",
                    None,
                    (),
                    (f"describe:{node.unique_id}",),
                    (_evidence(manifest, locator),),
                )
            )
        for column in node.columns.values():
            if column.description:
                continue
            gaps.append(
                SemanticGap(
                    f"column_description:{node.unique_id}.{column.name}",
                    FindingCode.MISSING_COLUMN_DESCRIPTION,
                    SemanticStatus.MISSING,
                    f"{node.unique_id}.{column.name} has no dbt description.",
                    None,
                    (),
                    (f"column:{node.name}.{column.name}",),
                    (_evidence(manifest, locator + f"/columns/{column.name}"),),
                )
            )

        suspected = _suspected_sensitive(node)
        for reference in suspected:
            gaps.append(
                SemanticGap(
                    f"sensitive:{reference}",
                    FindingCode.SUSPECTED_SENSITIVE_DATA,
                    SemanticStatus.INFERRED,
                    f"{reference} can contain sensitive information, but dbt has no policy flag.",
                    f"Must {reference} remain outside all proposed agent scopes?",
                    ("Yes, exclude it", "No, it is not sensitive", "Use a narrower policy"),
                    ("safety:sensitive_access", "proposal:scope"),
                    (_evidence(manifest, locator),),
                )
            )

        measure_columns = [
            column.name
            for column in node.columns.values()
            if any(term in column.name.casefold() for term in _MEASURE_TERMS)
        ]
        time_columns = [
            column.name
            for column in node.columns.values()
            if any(term in column.name.casefold() for term in _TIME_TERMS)
        ]
        if measure_columns and len(time_columns) > 1:
            metric_name = sorted(measure_columns)[0]
            gaps.append(
                SemanticGap(
                    f"time_basis:{node.unique_id}.{metric_name}",
                    FindingCode.AMBIGUOUS_TIME_BASIS,
                    SemanticStatus.MISSING,
                    f"{metric_name} has more than one possible time field.",
                    f"Which field defines the date for {metric_name}?",
                    tuple(sorted(time_columns)) + ("Use another expression",),
                    (
                        f"filter:{metric_name}",
                        f"group:{metric_name}:time",
                        f"compare:{metric_name}:period",
                    ),
                    (_evidence(manifest, locator + "/columns"),),
                    "semantic_knowledge",
                )
            )

    aliases: dict[str, list[Node]] = defaultdict(list)
    for node in nodes:
        aliases[node.alias.casefold()].append(node)
    for alias, values in aliases.items():
        if len(values) < 2:
            continue
        ids = tuple(sorted(item.unique_id for item in values))
        gaps.append(
            SemanticGap(
                f"duplicate_relation:{alias}",
                FindingCode.DUPLICATE_RELATION_NAME,
                SemanticStatus.CONFLICTING,
                f"The relation name '{alias}' refers to {len(ids)} dbt resources.",
                f"Which dbt resource is authoritative for relation name '{alias}'?",
                ids,
                (f"select:{alias}", f"join:{alias}"),
                tuple(_evidence(manifest, f"/nodes/{item}") for item in ids),
            )
        )

    for node in nodes:
        parents_in_scope = [parent for parent in node.parents if parent in node_ids]
        if parents_in_scope and not _has_relationship_test(node):
            gaps.append(
                SemanticGap(
                    f"relationship:{node.unique_id}",
                    FindingCode.MISSING_RELATIONSHIP_EVIDENCE,
                    SemanticStatus.MISSING,
                    f"{node.unique_id} has lineage but no relationship test.",
                    None,
                    (),
                    tuple(f"join:{parent}:{node.unique_id}" for parent in parents_in_scope),
                    (_evidence(manifest, f"/parent_map/{node.unique_id}"),),
                )
            )

    for conflict in conflicting_statements(statements):
        gaps.append(
            SemanticGap(
                f"statement_conflict:{conflict.id}",
                FindingCode.CONFLICTING_STATEMENT,
                SemanticStatus.CONFLICTING,
                conflict.definition,
                f"Which approved meaning is correct for {conflict.id}?",
                tuple(sorted({item.reference for item in conflict.evidence})),
                (f"metric:{conflict.id}", f"filter:{conflict.id}", f"time:{conflict.id}"),
                conflict.evidence,
                "semantic_knowledge",
            )
        )

    quality = SemanticQuality(
        resources=len(nodes),
        documented_resources=sum(bool(node.description) for node in nodes),
        columns=len(columns),
        documented_columns=sum(bool(column.description) for _, column in columns),
        relationship_tests=relationship_tests,
        semantic_models=sum(item.model_node in node_ids for item in manifest.semantic_models),
        metrics=sum(bool(set(item.parents) & node_ids) for item in manifest.metrics),
        exposures=sum(bool(set(item.parents) & node_ids) for item in manifest.exposures),
        sensitive_resources=sum(_sensitive(node) for node in nodes),
    )
    return SemanticAnalysis(
        manifest.digest,
        tuple(node.unique_id for node in nodes),
        quality,
        tuple(sorted(gaps, key=lambda item: (item.code.value, item.id))),
    )
