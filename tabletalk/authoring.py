"""Manifest-backed views and choice parsing for guided authoring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tabletalk.manifest import Manifest, ManifestError, Node

SELECTOR_KINDS = ("group", "tag", "model", "path", "package", "source")


@dataclass(frozen=True)
class SelectorOption:
    selector: str
    label: str
    resource_count: int


def selector_options(manifest: Manifest, kind: str) -> tuple[SelectorOption, ...]:
    if kind not in SELECTOR_KINDS:
        raise ManifestError(f"Unsupported selector type '{kind}'")
    values: dict[str, str] = {}
    for node in manifest.queryable_nodes:
        if node.resource_type == "source":
            if kind == "source":
                value = ".".join(node.unique_id.split(".")[-2:])
                values[value] = node.description or node.alias
            continue
        if kind == "group" and node.group:
            values[node.group] = "dbt group"
        elif kind == "tag":
            values.update({tag: "dbt tag" for tag in node.tags})
        elif kind == "model" and node.resource_type == "model":
            values[f"{node.package}.{node.name}"] = node.description or node.relation_name
        elif kind == "path" and node.original_file_path:
            values[node.original_file_path.rsplit("/", 1)[0]] = "dbt model path"
        elif kind == "package":
            values[node.package] = "dbt package"
    options = []
    for value, description in sorted(values.items()):
        selector = f"{kind}:{value}"
        count = len(manifest.select((selector,)))
        options.append(SelectorOption(selector, description, count))
    return tuple(options)


def parse_choices(raw: str, options: tuple[SelectorOption, ...]) -> tuple[str, ...]:
    selected: list[str] = []
    by_selector = {option.selector.lower(): option.selector for option in options}
    by_value = {option.selector.split(":", 1)[1].lower(): option.selector for option in options}
    for token in (item.strip() for item in raw.split(",")):
        if not token:
            continue
        selector: str | None
        if token.isdigit() and 1 <= int(token) <= len(options):
            selector = options[int(token) - 1].selector
        else:
            selector = by_selector.get(token.lower()) or by_value.get(token.lower())
        if selector is None:
            raise ManifestError(
                f"Unknown choice '{token}'. Use a displayed number or selector value."
            )
        if selector not in selected:
            selected.append(selector)
    if not selected:
        raise ManifestError("Choose at least one dbt resource scope")
    return tuple(selected)


def node_details(node: Node) -> dict[str, Any]:
    return {
        "unique_id": node.unique_id,
        "relation": node.relation_name,
        "description": node.description,
        "group": node.group,
        "tags": node.tags,
        "owner": node.owner,
        "access": node.access,
        "materialized": node.materialized,
        "path": node.original_file_path,
        "columns": [
            {
                "name": column.name,
                "declared_type": column.data_type,
                "physical_type": column.physical_type,
                "description": column.description,
                "constraints": column.constraints,
                "statistics": column.statistics,
            }
            for column in node.columns.values()
        ],
        "constraints": node.constraints,
        "tests": [
            {
                "name": test.name,
                "column": test.column_name,
                "arguments": test.arguments,
                "latest_status": test.status,
            }
            for test in node.tests
        ],
        "parents": node.parents,
        "children": node.children,
        "statistics": node.statistics,
    }
