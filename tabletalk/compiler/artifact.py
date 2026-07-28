"""Versioned, canonical compiled-agent artifact schema."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tabletalk.domain import canonical_digest, canonical_json


@dataclass(frozen=True)
class CompiledColumn:
    name: str
    data_type: str
    description: str = ""
    nullable: bool | None = None
    provenance: str = "database_metadata"


@dataclass(frozen=True)
class CompiledForeignKey:
    column: str
    references: str
    provenance: str = "database_constraint"


@dataclass(frozen=True)
class CompiledRelation:
    name: str
    description: str
    columns: tuple[CompiledColumn, ...]
    primary_key: tuple[str, ...] = ()
    foreign_keys: tuple[CompiledForeignKey, ...] = ()
    dbt_metadata: tuple[tuple[str, Any], ...] = ()


@dataclass(frozen=True)
class CompiledRelationship:
    name: str
    source: str
    target: str
    on: str
    cardinality: str
    provenance: str = "declared"
    confidence: str = "declared"


@dataclass(frozen=True)
class CompiledMetric:
    name: str
    expression: str
    label: str = ""
    description: str = ""
    relation: str | None = None
    aggregation: str | None = None
    filters: tuple[str, ...] = ()
    allowed_dimensions: tuple[str, ...] = ()
    synonyms: tuple[str, ...] = ()
    time_dimension: str | None = None
    unit: str | None = None
    grain: str | None = None
    provenance: str = "agent_definition"


@dataclass(frozen=True)
class CompiledAgent:
    format_version: str
    compiler_version: str
    resource_kind: str
    name: str
    version: str
    connection: str | None
    connection_type: str | None
    dialect: str | None
    context: str
    description: str
    owner: str | None
    persona: str | None
    sample_questions: tuple[str, ...]
    relations: tuple[CompiledRelation, ...]
    relationships: tuple[CompiledRelationship, ...]
    metrics: tuple[CompiledMetric, ...]
    time_semantics: tuple[tuple[str, Any], ...]
    rules: tuple[str, ...]
    policies: tuple[tuple[str, Any], ...]
    required_evals: tuple[str, ...]
    model_capabilities: tuple[str, ...]
    source_fingerprints: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class CompiledArtifact:
    digest: str
    agent: CompiledAgent

    def to_json(self) -> str:
        return canonical_json(self)


def envelope(agent: CompiledAgent) -> CompiledArtifact:
    return CompiledArtifact(digest=canonical_digest(agent), agent=agent)
