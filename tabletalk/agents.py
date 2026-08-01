"""Small agent resources whose query scope is resolved from dbt selectors."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from tabletalk.manifest import Manifest, Node


class AgentError(ValueError):
    pass


def _strings(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise AgentError(f"Agent {field} must be a list of non-empty strings")
    return tuple(item.strip() for item in value)


@dataclass(frozen=True)
class Agent:
    name: str
    description: str
    select: tuple[str, ...]
    exclude: tuple[str, ...] = ()
    instructions: tuple[str, ...] = ()
    sample_questions: tuple[str, ...] = ()
    include_parents: bool = False
    include_children: bool = False
    allow_sensitive: tuple[str, ...] = ()
    reject_if_contains: tuple[str, ...] = ()
    max_rows: int = 1000
    timeout_seconds: int = 60
    source_path: Path | None = None

    def __post_init__(self) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_-]*", self.name) is None:
            raise AgentError(
                "Agent name must start with a lowercase letter and contain only "
                "lowercase letters, numbers, underscores, or hyphens"
            )
        if not self.description.strip():
            raise AgentError("Agent description must be a non-empty string")
        if not self.select:
            raise AgentError("Agent select must contain at least one dbt selector")

    @classmethod
    def load(cls, path: str | Path) -> Agent:
        source = Path(path).resolve()
        try:
            payload = yaml.safe_load(source.read_text())
        except (OSError, yaml.YAMLError) as exc:
            raise AgentError(f"Could not load agent {source}: {exc}") from exc
        if not isinstance(payload, dict):
            raise AgentError(f"Agent {source.name} must be a YAML mapping")
        if "relations" in payload or "connection" in payload or payload.get("kind") == "Agent":
            raise AgentError(
                f"Agent {source.name} uses the removed pre-dbt format. "
                "Replace relations/connection "
                "with manifest selectors under select/exclude."
            )
        name = payload.get("name")
        description = payload.get("description")
        if not isinstance(name, str) or not name.strip():
            raise AgentError("Agent name must be a non-empty string")
        if not isinstance(description, str) or not description.strip():
            raise AgentError("Agent description must be a non-empty string")
        select = _strings(payload.get("select"), "select")
        if not select:
            raise AgentError("Agent select must contain at least one dbt selector")
        max_rows = payload.get("max_rows", 1000)
        timeout_seconds = payload.get("timeout_seconds", 60)
        if not isinstance(max_rows, int) or isinstance(max_rows, bool) or max_rows < 1:
            raise AgentError("Agent max_rows must be a positive integer")
        if (
            not isinstance(timeout_seconds, int)
            or isinstance(timeout_seconds, bool)
            or timeout_seconds < 1
        ):
            raise AgentError("Agent timeout_seconds must be a positive integer")
        return cls(
            name=name.strip(),
            description=" ".join(description.split()),
            select=select,
            exclude=_strings(payload.get("exclude"), "exclude"),
            instructions=_strings(payload.get("instructions"), "instructions"),
            sample_questions=_strings(payload.get("sample_questions"), "sample_questions"),
            include_parents=bool(payload.get("include_parents", False)),
            include_children=bool(payload.get("include_children", False)),
            allow_sensitive=_strings(payload.get("allow_sensitive"), "allow_sensitive"),
            reject_if_contains=_strings(payload.get("reject_if_contains"), "reject_if_contains"),
            max_rows=max_rows,
            timeout_seconds=timeout_seconds,
            source_path=source,
        )

    def dump(self) -> str:
        payload: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "select": list(self.select),
        }
        if self.exclude:
            payload["exclude"] = list(self.exclude)
        if self.include_parents:
            payload["include_parents"] = True
        if self.include_children:
            payload["include_children"] = True
        if self.instructions:
            payload["instructions"] = list(self.instructions)
        if self.sample_questions:
            payload["sample_questions"] = list(self.sample_questions)
        if self.allow_sensitive:
            payload["allow_sensitive"] = list(self.allow_sensitive)
        if self.reject_if_contains:
            payload["reject_if_contains"] = list(self.reject_if_contains)
        if self.max_rows != 1000:
            payload["max_rows"] = self.max_rows
        if self.timeout_seconds != 60:
            payload["timeout_seconds"] = self.timeout_seconds
        return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.dump().encode()).hexdigest()

    def resolve(self, manifest: Manifest) -> ResolvedAgent:
        nodes = manifest.select(
            self.select,
            self.exclude,
            include_parents=self.include_parents,
            include_children=self.include_children,
        )
        inherited: dict[str, str] = {}
        for node in nodes:
            ancestors = []
            pending = list(node.parents)
            visited: set[str] = set()
            while pending:
                parent_id = pending.pop(0)
                if parent_id in visited or parent_id not in manifest.nodes:
                    continue
                visited.add(parent_id)
                parent = manifest.nodes[parent_id]
                ancestors.append(parent)
                pending.extend(parent.parents)
            for column in node.columns.values():
                if column.description:
                    continue
                descriptions = {
                    parent.columns[column.name].description
                    for parent in ancestors
                    if column.name in parent.columns
                    and parent.columns[column.name].description.strip()
                }
                if len(descriptions) == 1:
                    inherited[f"{node.unique_id}.{column.name}"] = descriptions.pop()
        return ResolvedAgent(
            source=self,
            nodes=nodes,
            manifest_digest=manifest.digest,
            inherited_column_descriptions=inherited,
        )


@dataclass(frozen=True)
class ResolvedAgent:
    source: Agent
    nodes: tuple[Node, ...]
    manifest_digest: str
    inherited_column_descriptions: dict[str, str] = field(default_factory=dict)

    @property
    def unique_ids(self) -> tuple[str, ...]:
        return tuple(node.unique_id for node in self.nodes)

    @property
    def relation_names(self) -> tuple[str, ...]:
        return tuple(node.relation_name or f"{node.schema}.{node.alias}" for node in self.nodes)

    @property
    def missing_descriptions(self) -> tuple[str, ...]:
        missing = [node.unique_id for node in self.nodes if not node.description]
        missing.extend(
            f"{node.unique_id}.{column.name}"
            for node in self.nodes
            for column in node.columns.values()
            if not column.description
            and f"{node.unique_id}.{column.name}" not in self.inherited_column_descriptions
        )
        return tuple(missing)

    @property
    def duplicate_aliases(self) -> dict[str, tuple[str, ...]]:
        values: dict[str, list[str]] = {}
        for node in self.nodes:
            values.setdefault(node.alias.lower(), []).append(node.unique_id)
        return {key: tuple(ids) for key, ids in values.items() if len(ids) > 1}

    def prompt_context(self) -> str:
        lines: list[str] = []
        for node in self.nodes:
            relation = node.relation_name or f"{node.schema}.{node.alias}".strip(".")
            lines.append(f"DBT_RESOURCE {node.unique_id}: {relation}")
            metadata = [
                f"materialized={node.materialized}" if node.materialized else "",
                f"access={node.access}" if node.access else "",
                f"group={node.group}" if node.group else "",
                f"owner={node.owner}" if node.owner else "",
                f"package={node.package}",
            ]
            lines.append("  dbt metadata: " + ", ".join(item for item in metadata if item))
            if node.description:
                lines.append(f"  Description: {node.description}")
            if node.parents:
                lines.append("  Upstream lineage: " + ", ".join(node.parents))
            if node.children:
                lines.append("  Downstream lineage: " + ", ".join(node.children))
            if node.columns:
                lines.append(
                    "  Columns: "
                    + ", ".join(
                        " ".join(
                            part
                            for part in (
                                column.name,
                                f"[{column.physical_type or column.data_type}]"
                                if column.physical_type or column.data_type
                                else "",
                                (
                                    f"— {column.description}"
                                    if column.description
                                    else (
                                        "— upstream metadata: "
                                        + self.inherited_column_descriptions.get(
                                            f"{node.unique_id}.{column.name}", ""
                                        )
                                        if self.inherited_column_descriptions.get(
                                            f"{node.unique_id}.{column.name}"
                                        )
                                        else ""
                                    )
                                ),
                            )
                            if part
                        )
                        for column in node.columns.values()
                    )
                )
            if node.tests:
                lines.append(
                    "  Tests: "
                    + ", ".join(
                        f"{test.name}({test.arguments})" if test.arguments else test.name
                        for test in node.tests
                    )
                )
            if node.constraints:
                lines.append(f"  Declared constraints: {node.constraints}")
            if node.meta.get("joins"):
                lines.append(f"  Explicit join metadata: {node.meta['joins']}")
        return "\n".join(lines)


def load_agents(directory: str | Path) -> tuple[Agent, ...]:
    root = Path(directory)
    if not root.is_dir():
        return ()
    return tuple(Agent.load(path) for path in sorted((*root.glob("*.yaml"), *root.glob("*.yml"))))
