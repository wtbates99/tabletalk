"""Deterministic compilation from declarative agent/context definitions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from tabletalk.compiler.artifact import (
    CompiledAgent,
    CompiledArtifact,
    CompiledColumn,
    CompiledForeignKey,
    CompiledMetric,
    CompiledRelation,
    CompiledRelationship,
    envelope,
)
from tabletalk.domain import (
    ErrorCode,
    RuntimeStage,
    TableTalkError,
    canonical_digest,
)

_FORMAT_VERSION = "2"
_COMPILER_VERSION = "0.4.0"
_SECRET_KEYS = {
    "api_key",
    "authorization",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
}


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _normalized_source(value: Any) -> Any:
    """Normalize source semantics before fingerprinting order-insensitive inputs."""
    if isinstance(value, Mapping):
        return {
            str(key): _normalized_source(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        normalized = [_normalized_source(item) for item in value]
        return sorted(normalized, key=lambda item: canonical_digest(item))
    if isinstance(value, str):
        return _text(value)
    return value


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TableTalkError(
            ErrorCode.CONFIG_INVALID,
            RuntimeStage.COMPILATION,
            f"{label} must be a mapping.",
        )
    return value


def _reject_secrets(value: Any, path: str = "") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower()
            item_path = f"{path}.{key}" if path else str(key)
            if normalized in _SECRET_KEYS:
                raise TableTalkError(
                    ErrorCode.CONFIG_INVALID,
                    RuntimeStage.COMPILATION,
                    f"Secret-bearing field '{item_path}' is not allowed in an agent artifact.",
                )
            _reject_secrets(item, item_path)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, item in enumerate(value):
            _reject_secrets(item, f"{path}[{index}]")


def _columns(raw: Any) -> tuple[CompiledColumn, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    columns: list[CompiledColumn] = []
    for item in raw:
        column = _mapping(item, "schema column")
        name = _text(column.get("name"))
        if not name:
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.COMPILATION,
                "Schema columns require a name.",
            )
        nullable = column.get("nullable")
        columns.append(
            CompiledColumn(
                name=name,
                data_type=_text(column.get("data_type") or column.get("type") or "unknown"),
                description=_text(column.get("description")),
                nullable=nullable if isinstance(nullable, bool) else None,
                provenance=_text(column.get("provenance"))
                or "database_metadata",
            )
        )
    return tuple(sorted(columns, key=lambda item: item.name))


def _schema_relation(schema_snapshot: Mapping[str, Any], relation_name: str) -> Mapping[str, Any]:
    value = schema_snapshot.get(relation_name)
    if value is None:
        short_name = relation_name.rsplit(".", 1)[-1]
        matches = [
            item
            for key, item in schema_snapshot.items()
            if str(key).rsplit(".", 1)[-1] == short_name
        ]
        if len(matches) == 1:
            value = matches[0]
    if value is None:
        raise TableTalkError(
            ErrorCode.CONFIG_INVALID,
            RuntimeStage.COMPILATION,
            f"Declared relation '{relation_name}' is missing from the schema snapshot.",
        )
    return _mapping(value, f"schema snapshot relation '{relation_name}'")


def _relations(
    context: Mapping[str, Any], schema_snapshot: Mapping[str, Any]
) -> tuple[CompiledRelation, ...]:
    datasets = context.get("datasets") or context.get("schemas") or []
    if not isinstance(datasets, Sequence) or isinstance(datasets, (str, bytes)):
        raise TableTalkError(
            ErrorCode.CONFIG_INVALID,
            RuntimeStage.COMPILATION,
            "context.datasets must be a list.",
        )
    relations: list[CompiledRelation] = []
    seen: set[str] = set()
    for raw_dataset in datasets:
        dataset = _mapping(raw_dataset, "context dataset")
        dataset_name = _text(dataset.get("name"))
        for raw_table in dataset.get("tables") or []:
            table = (
                {"name": raw_table}
                if isinstance(raw_table, str)
                else _mapping(raw_table, "context table")
            )
            table_name = _text(table.get("name"))
            if not dataset_name or not table_name:
                raise TableTalkError(
                    ErrorCode.CONFIG_INVALID,
                    RuntimeStage.COMPILATION,
                    "Every declared relation requires dataset and table names.",
                )
            name = f"{dataset_name}.{table_name}"
            if name in seen:
                raise TableTalkError(
                    ErrorCode.CONFIG_INVALID,
                    RuntimeStage.COMPILATION,
                    f"Relation '{name}' is declared more than once.",
                )
            seen.add(name)
            schema = _schema_relation(schema_snapshot, name)
            primary_key = schema.get("primary_key") or ()
            raw_foreign_keys = schema.get("foreign_keys") or ()
            relations.append(
                CompiledRelation(
                    name=name,
                    description=_text(table.get("description") or schema.get("description")),
                    columns=_columns(schema.get("columns") or ()),
                    primary_key=tuple(sorted(_text(item) for item in primary_key)),
                    foreign_keys=tuple(
                        sorted(
                            (
                                CompiledForeignKey(
                                    column=_text(item.get("column")),
                                    references=_text(item.get("references")),
                                    provenance=_text(item.get("provenance"))
                                    or "database_constraint",
                                )
                                for item in raw_foreign_keys
                                if isinstance(item, Mapping)
                                and _text(item.get("column"))
                                and _text(item.get("references"))
                            ),
                            key=lambda item: (item.column, item.references),
                        )
                    ),
                    dbt_metadata=_pairs(
                        schema.get("dbt"),
                        f"schema snapshot relation '{name}'.dbt",
                    ),
                )
            )
    return tuple(sorted(relations, key=lambda item: item.name))


def _relationships(
    context: Mapping[str, Any],
    schema_snapshot: Mapping[str, Any],
) -> tuple[CompiledRelationship, ...]:
    values = context.get("relationships") or []
    relationships = []
    for raw in values:
        item = _mapping(raw, "relationship")
        relationship = CompiledRelationship(
            name=_text(item.get("name")),
            source=_text(item.get("source") or item.get("from")),
            target=_text(item.get("target") or item.get("to")),
            on=_text(item.get("on")),
            cardinality=_text(item.get("cardinality")),
            provenance=_text(item.get("provenance") or item.get("source_type"))
            or "declared",
            confidence=_text(item.get("confidence")) or "declared",
        )
        if not all(
            (
                relationship.name,
                relationship.source,
                relationship.target,
                relationship.on,
                relationship.cardinality,
            )
        ):
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.COMPILATION,
                "Relationships require name, source, target, on, and cardinality.",
            )
        relationships.append(relationship)
    existing_edges = {
        frozenset((relationship.source.lower(), relationship.target.lower()))
        for relationship in relationships
    }
    for relation_name, raw_schema in schema_snapshot.items():
        if not isinstance(raw_schema, Mapping):
            continue
        for raw in raw_schema.get("foreign_keys") or ():
            if not isinstance(raw, Mapping):
                continue
            column = _text(raw.get("column"))
            references = _text(raw.get("references"))
            source = f"{relation_name}.{column}"
            edge = frozenset((source.lower(), references.lower()))
            if not column or not references or edge in existing_edges:
                continue
            relationships.append(
                CompiledRelationship(
                    name=f"fk_{relation_name}_{column}",
                    source=source,
                    target=references,
                    on=f"{source} = {references}",
                    cardinality="many_to_one",
                    provenance=_text(raw.get("provenance"))
                    or "database_constraint",
                    confidence="database_constraint",
                )
            )
            existing_edges.add(edge)
    return tuple(sorted(relationships, key=lambda item: item.name))


def _metrics(context: Mapping[str, Any]) -> tuple[CompiledMetric, ...]:
    values = context.get("metrics") or []
    metrics = []
    for raw in values:
        item = _mapping(raw, "metric")
        name = _text(item.get("name"))
        expression = _text(item.get("expression"))
        if not name or not expression:
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.COMPILATION,
                "Metrics require name and expression.",
            )
        filters = item.get("filters") or ()
        metrics.append(
            CompiledMetric(
                name=name,
                expression=expression,
                label=_text(item.get("label")),
                description=_text(item.get("description")),
                relation=_text(item.get("relation")) or None,
                aggregation=_text(item.get("aggregation")) or None,
                filters=tuple(sorted(_text(value) for value in filters)),
                allowed_dimensions=tuple(
                    sorted(_text(value) for value in item.get("allowed_dimensions") or ())
                ),
                synonyms=tuple(
                    sorted(_text(value) for value in item.get("synonyms") or ())
                ),
                time_dimension=_text(item.get("time_dimension")) or None,
                unit=_text(item.get("unit") or item.get("currency")) or None,
                grain=_text(item.get("grain")) or None,
                provenance=_text(item.get("provenance")) or "agent_definition",
            )
        )
    return tuple(sorted(metrics, key=lambda item: item.name))


def _pairs(value: Any, label: str) -> tuple[tuple[str, Any], ...]:
    if value is None:
        return ()
    mapping = _mapping(value, label)
    return tuple(sorted((str(key), item) for key, item in mapping.items()))


def compile_agent(
    agent_definition: Mapping[str, Any],
    context_definition: Mapping[str, Any],
    schema_snapshot: Mapping[str, Any],
    *,
    connection_type: str | None = None,
    dialect: str | None = None,
    resource_kind: str = "Agent",
) -> CompiledArtifact:
    """Compile definitions into a canonical, secret-free artifact."""
    _reject_secrets(agent_definition, "agent")
    _reject_secrets(context_definition, "context")
    _reject_secrets(schema_snapshot, "schema")

    name = _text(agent_definition.get("name"))
    context_name = _text(agent_definition.get("context") or context_definition.get("name"))
    if not name or not context_name:
        raise TableTalkError(
            ErrorCode.CONFIG_INVALID,
            RuntimeStage.COMPILATION,
            "Agent compilation requires agent.name and agent.context.",
        )
    questions = agent_definition.get("sample_questions") or ()
    required_evals = (
        agent_definition.get("required_evals") or context_definition.get("required_evals") or ()
    )
    compiled = CompiledAgent(
        format_version=_FORMAT_VERSION,
        compiler_version=_COMPILER_VERSION,
        resource_kind=resource_kind,
        name=name,
        version=_text(agent_definition.get("version") or "1"),
        connection=_text(agent_definition.get("connection")) or None,
        connection_type=_text(connection_type) or None,
        dialect=_text(dialect) or None,
        context=context_name,
        description=_text(
            agent_definition.get("description") or context_definition.get("description")
        ),
        owner=_text(agent_definition.get("owner")) or None,
        persona=_text(agent_definition.get("persona")) or None,
        sample_questions=tuple(sorted(_text(value) for value in questions)),
        relations=_relations(context_definition, schema_snapshot),
        relationships=_relationships(context_definition, schema_snapshot),
        metrics=_metrics(context_definition),
        time_semantics=_pairs(context_definition.get("time_semantics"), "time_semantics"),
        rules=tuple(
            _text(value)
            for value in context_definition.get("rules") or ()
            if _text(value)
        ),
        policies=_pairs(
            agent_definition.get("policies") or context_definition.get("policies"),
            "policies",
        ),
        required_evals=tuple(sorted(_text(value) for value in required_evals)),
        model_capabilities=("json_schema", "read_only_sql"),
        source_fingerprints=(
            (
                "agent_definition",
                canonical_digest(
                    _normalized_source(
                        {
                            "agent": agent_definition,
                            "semantics": context_definition,
                        }
                    )
                ),
            ),
            ("schema_snapshot", canonical_digest(_normalized_source(schema_snapshot))),
        ),
    )
    return envelope(compiled)
