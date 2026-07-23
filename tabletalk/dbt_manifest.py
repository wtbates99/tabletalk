"""Read semantic context from a compiled dbt ``manifest.json``.

TableTalk keeps its own compact manifest because it is efficient to place in an
LLM system prompt. This module enriches that prompt from dbt's source of truth
without asking users to duplicate model and column documentation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _compact_text(value: Any) -> str:
    """Collapse multiline dbt descriptions into prompt-safe single lines."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _relation_key(value: str) -> str:
    """Normalize quoted database relation names for matching."""
    return value.replace('"', "").replace("`", "").replace("[", "").replace("]", "").strip().lower()


@dataclass
class DbtRelationContext:
    """The dbt metadata that matters when an LLM writes SQL."""

    unique_id: str
    name: str
    description: str = ""
    columns: dict[str, str] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)

    def prompt_lines(self, relation_name: str) -> list[str]:
        """Render compact, explicit metadata lines for a TableTalk manifest."""
        lines = [f"DBT_NODE: {relation_name} = {self.unique_id}"]
        if self.description:
            lines.append(f"DBT_DESCRIPTION: {relation_name} - {self.description}")
        for column, description in self.columns.items():
            if description:
                lines.append(f"DBT_COLUMN: {relation_name}.{column} - {description}")
        if self.depends_on:
            lines.append(f"DBT_LINEAGE: {relation_name} <- {', '.join(self.depends_on)}")
        if self.tests:
            lines.append(f"DBT_TESTS: {relation_name} - {', '.join(self.tests)}")
        return lines


class DbtManifest:
    """Indexed, read-only view over a dbt manifest artifact."""

    def __init__(self, path: Path, payload: dict[str, Any]):
        self.path = path
        self.payload = payload
        self._by_relation: dict[str, DbtRelationContext] = {}
        self._by_unique_id: dict[str, DbtRelationContext] = {}
        self._index()

    @classmethod
    def load(cls, project_folder: str | Path, config: Any) -> DbtManifest:
        """Load the dbt artifact configured in ``tabletalk.yaml``.

        Supported forms:

        ``dbt: {manifest: dbt_project/target/manifest.json}``
        ``dbt: dbt_project/target/manifest.json``
        """
        configured_path: Any
        if isinstance(config, str):
            configured_path = config
        elif isinstance(config, dict):
            configured_path = config.get("manifest") or config.get("manifest_path")
        else:
            configured_path = None
        if not configured_path:
            raise ValueError(
                "dbt configuration requires 'manifest', for example "
                "dbt: {manifest: target/manifest.json}"
            )

        path = Path(str(configured_path)).expanduser()
        if not path.is_absolute():
            path = Path(project_folder) / path
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(
                f"Configured dbt manifest not found: {path}. "
                "Run 'dbt compile' or 'dbt build' before 'tabletalk apply'."
            )
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid dbt manifest JSON at {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid dbt manifest at {path}: expected a JSON object.")
        return cls(path, payload)

    @staticmethod
    def _node_candidates(node: dict[str, Any]) -> set[str]:
        schema = str(node.get("schema") or "").strip()
        name = str(node.get("alias") or node.get("identifier") or node.get("name") or "").strip()
        database = str(node.get("database") or "").strip()
        candidates = {
            str(node.get("relation_name") or ""),
            name,
            f"{schema}.{name}" if schema and name else "",
            f"{database}.{schema}.{name}" if database and schema and name else "",
        }
        return {_relation_key(value) for value in candidates if value}

    def _index(self) -> None:
        resources: dict[str, Any] = {}
        for collection in ("nodes", "sources", "seeds", "snapshots"):
            values = self.payload.get(collection, {})
            if isinstance(values, dict):
                resources.update(values)

        for unique_id, raw_node in resources.items():
            if not isinstance(raw_node, dict):
                continue
            resource_type = raw_node.get("resource_type")
            if resource_type not in {"model", "source", "seed", "snapshot"}:
                continue
            columns: dict[str, str] = {}
            raw_columns = raw_node.get("columns", {})
            if isinstance(raw_columns, dict):
                for key, raw_column in raw_columns.items():
                    if not isinstance(raw_column, dict):
                        continue
                    column_name = str(raw_column.get("name") or key)
                    columns[column_name] = _compact_text(raw_column.get("description"))
            depends_on = raw_node.get("depends_on", {})
            upstream = depends_on.get("nodes", []) if isinstance(depends_on, dict) else []
            context = DbtRelationContext(
                unique_id=str(unique_id),
                name=str(
                    raw_node.get("alias")
                    or raw_node.get("identifier")
                    or raw_node.get("name")
                    or ""
                ),
                description=_compact_text(raw_node.get("description")),
                columns=columns,
                depends_on=[str(value) for value in upstream],
            )
            self._by_unique_id[str(unique_id)] = context
            for candidate in self._node_candidates(raw_node):
                self._by_relation[candidate] = context

        nodes = self.payload.get("nodes", {})
        if not isinstance(nodes, dict):
            return
        for unique_id, raw_node in nodes.items():
            if not isinstance(raw_node, dict) or raw_node.get("resource_type") != "test":
                continue
            depends_on = raw_node.get("depends_on", {})
            targets = depends_on.get("nodes", []) if isinstance(depends_on, dict) else []
            metadata = raw_node.get("test_metadata", {})
            test_name = (metadata.get("name") if isinstance(metadata, dict) else None) or str(
                unique_id
            ).split(".")[-1]
            test_column_name = (
                metadata.get("kwargs", {}).get("column_name")
                if isinstance(metadata, dict) and isinstance(metadata.get("kwargs"), dict)
                else None
            )
            label = f"{test_name}({test_column_name})" if test_column_name else str(test_name)
            for target in targets:
                target_context = self._by_unique_id.get(str(target))
                if target_context is not None and label not in target_context.tests:
                    target_context.tests.append(label)

    def relation(self, schema: str, table: str) -> DbtRelationContext | None:
        """Find dbt context for a live relation returned by database introspection."""
        candidates = [
            _relation_key(f"{schema}.{table}"),
            _relation_key(table),
        ]
        for candidate in candidates:
            context = self._by_relation.get(candidate)
            if context is not None:
                return context
        return None
