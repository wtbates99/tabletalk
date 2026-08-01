"""dbt artifact loading, normalization, selection, and graph navigation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any


class ManifestError(ValueError):
    """Raised when a dbt artifact cannot define an unambiguous query scope."""


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _tuple(value: Any) -> tuple[str, ...]:
    return tuple(str(item) for item in (value or ()))


def _relation_part(value: Any) -> str:
    return str(value or "").strip().strip('"`[]')


def _relation_key(value: str) -> str:
    return ".".join(_relation_part(part).lower() for part in value.split(".") if part.strip())


@dataclass(frozen=True)
class Column:
    name: str
    description: str = ""
    data_type: str | None = None
    physical_type: str | None = None
    constraints: tuple[dict[str, Any], ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    statistics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Test:
    unique_id: str
    name: str
    column_name: str | None = None
    status: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Node:
    unique_id: str
    resource_type: str
    package: str
    name: str
    alias: str
    database: str
    schema: str
    relation_name: str
    original_file_path: str
    description: str
    columns: dict[str, Column]
    tags: tuple[str, ...]
    group: str | None
    owner: str | None
    access: str | None
    materialized: str | None
    meta: dict[str, Any]
    constraints: tuple[dict[str, Any], ...]
    parents: tuple[str, ...]
    children: tuple[str, ...]
    tests: tuple[Test, ...]
    checksum: str | None
    enabled: bool
    statistics: dict[str, Any] = field(default_factory=dict)

    @property
    def queryable(self) -> bool:
        return (
            self.enabled
            and self.resource_type in {"model", "seed", "snapshot", "source"}
            and self.materialized != "ephemeral"
        )

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(self.columns)

    def matches_relation(self, parts: Iterable[str]) -> bool:
        wanted = _relation_key(".".join(parts))
        candidates = {self.name, self.alias, self.relation_name}
        if self.schema:
            candidates.add(f"{self.schema}.{self.alias}")
        if self.database and self.schema:
            candidates.add(f"{self.database}.{self.schema}.{self.alias}")
        return wanted in {_relation_key(value) for value in candidates if value}


@dataclass(frozen=True)
class ManifestSummary:
    manifest_version: str
    dbt_version: str
    model_count: int
    groups: tuple[str, ...]
    tags: tuple[str, ...]
    packages: tuple[str, ...]
    paths: tuple[str, ...]


class Manifest:
    """Normalized, immutable view of the queryable universe in manifest.json."""

    def __init__(
        self,
        path: Path,
        payload: dict[str, Any],
        catalog: dict[str, Any] | None = None,
        run_results: dict[str, Any] | None = None,
    ) -> None:
        self.path = path
        self.payload = payload
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        self.digest = hashlib.sha256(canonical).hexdigest()
        self.catalog_digest = (
            hashlib.sha256(
                json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if catalog
            else None
        )
        metadata = payload.get("metadata") or {}
        self.manifest_version = str(metadata.get("dbt_schema_version") or "")
        self.dbt_version = str(metadata.get("dbt_version") or "")
        self.nodes = self._normalize()
        self._enrich_catalog(catalog or {})
        self._enrich_run_results(run_results or {})
        self._queryable = {uid: node for uid, node in self.nodes.items() if node.queryable}

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        catalog_path: str | Path | None = None,
        run_results_path: str | Path | None = None,
    ) -> Manifest:
        artifact = Path(path).expanduser().resolve()
        if not artifact.is_file():
            raise ManifestError(f"dbt manifest not found: {artifact}. Run 'dbt parse' first.")
        try:
            payload = json.loads(artifact.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ManifestError(f"Invalid dbt manifest at {artifact}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ManifestError(f"Invalid dbt manifest at {artifact}: expected an object")

        def optional_payload(configured: str | Path | None, default_name: str) -> dict[str, Any]:
            optional = (
                Path(configured).expanduser() if configured else artifact.with_name(default_name)
            )
            if not optional.is_absolute():
                optional = artifact.parent / optional
            if not optional.is_file():
                return {}
            try:
                value = json.loads(optional.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                raise ManifestError(f"Invalid optional dbt artifact at {optional}: {exc}") from exc
            return value if isinstance(value, dict) else {}

        return cls(
            artifact,
            payload,
            optional_payload(catalog_path, "catalog.json"),
            optional_payload(run_results_path, "run_results.json"),
        )

    def _enrich_catalog(self, catalog: dict[str, Any]) -> None:
        resources: dict[str, Any] = {}
        for collection in ("nodes", "sources"):
            values = catalog.get(collection) or {}
            if isinstance(values, dict):
                resources.update(values)
        for uid, raw in resources.items():
            node = self.nodes.get(str(uid))
            if node is None or not isinstance(raw, dict):
                continue
            columns = dict(node.columns)
            for key, raw_column in (raw.get("columns") or {}).items():
                if not isinstance(raw_column, dict):
                    continue
                name = str(raw_column.get("name") or key)
                existing_key = next(
                    (item for item in columns if item.lower() == name.lower()), name
                )
                existing = columns.get(existing_key, Column(name=name))
                columns[existing_key] = replace(
                    existing,
                    data_type=existing.data_type or raw_column.get("type"),
                    physical_type=str(raw_column["type"]) if raw_column.get("type") else None,
                    statistics=dict(raw_column.get("stats") or {}),
                )
            self.nodes[str(uid)] = replace(
                node,
                columns=columns,
                statistics=dict(raw.get("stats") or {}),
            )

    def _enrich_run_results(self, run_results: dict[str, Any]) -> None:
        statuses = {
            str(item.get("unique_id")): str(item.get("status"))
            for item in run_results.get("results") or ()
            if isinstance(item, dict) and item.get("unique_id") and item.get("status")
        }
        for uid, node in tuple(self.nodes.items()):
            tests = tuple(replace(test, status=statuses.get(test.unique_id)) for test in node.tests)
            self.nodes[uid] = replace(node, tests=tests)

    def _normalize(self) -> dict[str, Node]:
        group_owners: dict[str, str] = {}
        for raw_group in (self.payload.get("groups") or {}).values():
            if not isinstance(raw_group, dict):
                continue
            raw_owner = raw_group.get("owner") or {}
            owner_name = (
                raw_owner.get("name") or raw_owner.get("email")
                if isinstance(raw_owner, dict)
                else raw_owner
            )
            if raw_group.get("name") and owner_name:
                group_owners[str(raw_group["name"])] = str(owner_name)
        raw_resources: dict[str, dict[str, Any]] = {}
        for collection in ("nodes", "sources", "exposures", "metrics", "semantic_models"):
            values = self.payload.get(collection) or {}
            if isinstance(values, dict):
                raw_resources.update({str(k): v for k, v in values.items() if isinstance(v, dict)})

        child_map: dict[str, list[str]] = {}
        raw_child_map = self.payload.get("child_map") or {}
        if isinstance(raw_child_map, dict):
            child_map = {
                str(k): [str(v) for v in values]
                for k, values in raw_child_map.items()
                if isinstance(values, list)
            }
        for uid, raw in raw_resources.items():
            dependencies = raw.get("depends_on") or {}
            parents = dependencies.get("nodes") or [] if isinstance(dependencies, dict) else []
            for parent in parents:
                child_map.setdefault(str(parent), []).append(uid)

        tests_by_target: dict[str, list[Test]] = {}
        raw_nodes = self.payload.get("nodes") or {}
        if isinstance(raw_nodes, dict):
            for uid, raw in raw_nodes.items():
                if not isinstance(raw, dict) or raw.get("resource_type") != "test":
                    continue
                metadata = raw.get("test_metadata") or {}
                kwargs = metadata.get("kwargs") or {} if isinstance(metadata, dict) else {}
                test = Test(
                    unique_id=str(uid),
                    name=str(metadata.get("name") or raw.get("name") or uid),
                    column_name=str(kwargs.get("column_name"))
                    if kwargs.get("column_name") is not None
                    else raw.get("column_name"),
                    arguments=dict(kwargs),
                )
                dependencies = raw.get("depends_on") or {}
                for target in (
                    dependencies.get("nodes", ()) if isinstance(dependencies, dict) else ()
                ):
                    tests_by_target.setdefault(str(target), []).append(test)

        normalized: dict[str, Node] = {}
        for uid, raw in raw_resources.items():
            if raw.get("resource_type") == "test":
                continue
            config = raw.get("config") or {}
            columns: dict[str, Column] = {}
            for key, value in (raw.get("columns") or {}).items():
                if not isinstance(value, dict):
                    continue
                name = str(value.get("name") or key)
                columns[name] = Column(
                    name=name,
                    description=_text(value.get("description")),
                    data_type=str(value["data_type"])
                    if value.get("data_type") is not None
                    else None,
                    constraints=tuple(
                        v for v in value.get("constraints") or () if isinstance(v, dict)
                    ),
                    meta=dict(value.get("meta") or {}),
                    tags=_tuple(value.get("tags")),
                )
            owner = raw.get("owner")
            if isinstance(owner, dict):
                owner = owner.get("name") or owner.get("email")
            group_name = raw.get("group") or config.get("group")
            if not owner and group_name:
                owner = group_owners.get(str(group_name))
            checksum = raw.get("checksum")
            if isinstance(checksum, dict):
                checksum = checksum.get("checksum")
            dependencies = raw.get("depends_on") or {}
            parents = dependencies.get("nodes") or () if isinstance(dependencies, dict) else ()
            normalized[uid] = Node(
                unique_id=uid,
                resource_type=str(raw.get("resource_type") or ""),
                package=str(raw.get("package_name") or uid.split(".")[1] if "." in uid else ""),
                name=str(raw.get("name") or ""),
                alias=str(raw.get("alias") or raw.get("identifier") or raw.get("name") or ""),
                database=_relation_part(raw.get("database")),
                schema=_relation_part(raw.get("schema")),
                relation_name=str(raw.get("relation_name") or ""),
                original_file_path=str(raw.get("original_file_path") or raw.get("path") or ""),
                description=_text(raw.get("description")),
                columns=columns,
                tags=tuple(sorted(set(_tuple(raw.get("tags")) + _tuple(config.get("tags"))))),
                group=str(group_name) if group_name else None,
                owner=str(owner) if owner else None,
                access=str(raw.get("access")) if raw.get("access") else None,
                materialized=str(config.get("materialized"))
                if config.get("materialized")
                else None,
                meta={**dict(config.get("meta") or {}), **dict(raw.get("meta") or {})},
                constraints=tuple(
                    v
                    for v in raw.get("constraints") or config.get("constraints") or ()
                    if isinstance(v, dict)
                ),
                parents=tuple(str(v) for v in parents),
                children=tuple(sorted(set(child_map.get(uid, ())))),
                tests=tuple(tests_by_target.get(uid, ())),
                checksum=str(checksum) if checksum else None,
                enabled=bool(config.get("enabled", True)),
            )
        return normalized

    @property
    def queryable_nodes(self) -> tuple[Node, ...]:
        return tuple(sorted(self._queryable.values(), key=lambda node: node.unique_id))

    @property
    def summary(self) -> ManifestSummary:
        nodes = self.queryable_nodes
        return ManifestSummary(
            manifest_version=self.manifest_version,
            dbt_version=self.dbt_version,
            model_count=sum(node.resource_type == "model" for node in nodes),
            groups=tuple(sorted({node.group for node in nodes if node.group})),
            tags=tuple(sorted({tag for node in nodes for tag in node.tags})),
            packages=tuple(sorted({node.package for node in nodes if node.package})),
            paths=tuple(
                sorted(
                    {
                        str(Path(node.original_file_path).parent)
                        for node in nodes
                        if node.original_file_path
                    }
                )
            ),
        )

    def select(
        self,
        selectors: Iterable[str],
        exclude: Iterable[str] = (),
        *,
        include_parents: bool = False,
        include_children: bool = False,
    ) -> tuple[Node, ...]:
        chosen: set[str] = set()
        values = tuple(selectors)
        if not values:
            raise ManifestError("Agent select must contain at least one dbt selector")
        for selector in values:
            matches = self._match_selector(selector)
            if not matches:
                raise ManifestError(f"dbt selector '{selector}' matched no queryable resources")
            chosen.update(matches)
        if include_parents:
            chosen.update(self._walk(chosen, "parents"))
        if include_children:
            chosen.update(self._walk(chosen, "children"))
        for selector in exclude:
            chosen.difference_update(self._match_selector(selector))
        if not chosen:
            raise ManifestError("Agent selectors resolve to an empty scope")
        return tuple(self._queryable[uid] for uid in sorted(chosen) if uid in self._queryable)

    def _match_selector(self, selector: str) -> set[str]:
        if not isinstance(selector, str) or ":" not in selector:
            raise ManifestError(f"Invalid dbt selector '{selector}'; expected type:value")
        kind, value = (part.strip() for part in selector.split(":", 1))
        if kind not in {"group", "tag", "model", "source", "path", "package"} or not value:
            raise ManifestError(f"Unsupported dbt selector '{selector}'")
        result: set[str] = set()
        for node in self.queryable_nodes:
            if node.resource_type == "source" and kind != "source":
                continue
            matched = (
                (kind == "group" and node.group == value)
                or (kind == "tag" and value in node.tags)
                or (
                    kind == "model"
                    and node.resource_type == "model"
                    and value in {node.name, node.unique_id, f"{node.package}.{node.name}"}
                )
                or (
                    kind == "source"
                    and node.resource_type == "source"
                    and (
                        value == node.unique_id or value == ".".join(node.unique_id.split(".")[-2:])
                    )
                )
                or (
                    kind == "path"
                    and (
                        node.original_file_path == value
                        or node.original_file_path.startswith(value.rstrip("/") + "/")
                    )
                )
                or (kind == "package" and node.package == value)
            )
            if matched:
                result.add(node.unique_id)
        return result

    def _walk(self, starts: Iterable[str], direction: str) -> set[str]:
        found: set[str] = set()
        pending = list(starts)
        while pending:
            uid = pending.pop()
            node = self.nodes.get(uid)
            if node is None:
                continue
            for adjacent in getattr(node, direction):
                if adjacent not in found:
                    found.add(adjacent)
                    pending.append(adjacent)
        return {
            uid
            for uid in found
            if uid in self._queryable and self._queryable[uid].resource_type != "source"
        }

    def resolve_relation(self, parts: Iterable[str], scope: Iterable[Node] | None = None) -> Node:
        parts_tuple = tuple(parts)
        matches = [
            node for node in (scope or self.queryable_nodes) if node.matches_relation(parts_tuple)
        ]
        if not matches:
            raise ManifestError(
                f"Relation '{'.'.join(parts_tuple)}' is not a manifest-backed "
                "resource in agent scope"
            )
        if len(matches) > 1:
            ids = ", ".join(node.unique_id for node in matches)
            raise ManifestError(f"Relation '{'.'.join(parts_tuple)}' is ambiguous: {ids}")
        return matches[0]
