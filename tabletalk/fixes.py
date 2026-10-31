"""Suggest dbt YAML fixes as a reviewable patch. TableTalk never edits dbt files itself.

Only fixes whose content is certain are proposed:

- ``meta: {sensitive: true}`` on a column that looks sensitive but has no flag;
- a column description inherited verbatim from the same column upstream, when
  every documented ancestor agrees on it.

Edits are inserted as text, so comments and formatting survive. Each edit is
checked by re-parsing the YAML. A fix that cannot be placed exactly is reported
for a human instead of guessed.
"""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

from tabletalk.agents import Agent
from tabletalk.manifest import Manifest
from tabletalk.semantic_analysis import FindingCode, analyze_semantics

FixKind = Literal["flag_sensitive", "inherit_description"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Fix(_Model):
    """One change to one dbt column."""

    kind: FixKind
    model: str
    column: str
    path: str
    detail: str


class ManualFix(_Model):
    """A fix TableTalk found but could not place exactly."""

    kind: FixKind
    model: str
    column: str
    reason: str


class FixPlan(_Model):
    fixes: list[Fix]
    manual: list[ManualFix]
    patch: str


@dataclass(frozen=True)
class _Wanted:
    kind: FixKind
    unique_id: str
    model: str
    column: str
    path: str | None
    description: str = ""


def _yaml_path(manifest: Manifest, unique_id: str) -> str | None:
    """Return the project-relative schema file that documents a model."""
    raw = manifest.payload.get("nodes", {}).get(unique_id, {})
    patch_path = raw.get("patch_path") if isinstance(raw, dict) else None
    if not isinstance(patch_path, str) or "://" not in patch_path:
        return None
    return patch_path.split("://", 1)[1]


def _wanted(agents: Sequence[Agent], manifest: Manifest) -> list[_Wanted]:
    found: dict[tuple[str, str, str], _Wanted] = {}
    for agent in agents:
        resolved = agent.resolve(manifest)
        names = {node.unique_id: node.name for node in resolved.nodes}
        for gap in analyze_semantics(manifest, resolved.nodes).gaps:
            if gap.code is not FindingCode.SUSPECTED_SENSITIVE_DATA:
                continue
            reference = gap.id.removeprefix("sensitive:")
            for unique_id, name in names.items():
                if reference.startswith(unique_id + "."):
                    column = reference.removeprefix(unique_id + ".")
                    found[("flag_sensitive", unique_id, column)] = _Wanted(
                        "flag_sensitive", unique_id, name, column, _yaml_path(manifest, unique_id)
                    )
        for key, description in resolved.inherited_column_descriptions.items():
            unique_id, column = key.rsplit(".", 1)
            found[("inherit_description", unique_id, column)] = _Wanted(
                "inherit_description",
                unique_id,
                names[unique_id],
                column,
                _yaml_path(manifest, unique_id),
                description,
            )
    return [found[key] for key in sorted(found)]


_ITEM = re.compile(r"^(?P<indent> *)- +name: *['\"]?(?P<name>[^'\"#\s]+)['\"]? *(#.*)?$")


def _block(lines: list[str], start: int) -> tuple[int, int]:
    """Return the dash indent of the list item at ``start`` and the index after it."""
    match = _ITEM.match(lines[start])
    assert match is not None
    indent = len(match.group("indent"))
    end = start + 1
    while end < len(lines):
        stripped = lines[end].strip()
        if stripped and not stripped.startswith("#"):
            if len(lines[end]) - len(lines[end].lstrip()) <= indent:
                break
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return indent, end


def _find_column(lines: list[str], model: str, column: str) -> int | None:
    """Return the line of ``- name: column`` under ``- name: model`` in a models list."""
    for start, line in enumerate(lines):
        match = _ITEM.match(line)
        if not match or match.group("name") != model:
            continue
        _, model_end = _block(lines, start)
        for index in range(start + 1, model_end):
            item = _ITEM.match(lines[index])
            if item and item.group("name") == column:
                return index
    return None


def _scalar(text: str) -> str:
    """Render ``text`` as a YAML scalar: plain when that round-trips, else double-quoted."""
    if yaml.safe_load(f"k: {text}") == {"k": text} and "\n" not in text:
        return text
    return json.dumps(text)


def _column(document: Any, model: str, column: str) -> dict[str, Any] | None:
    for item in (document or {}).get("models") or []:
        if isinstance(item, dict) and item.get("name") == model:
            for entry in item.get("columns") or []:
                if isinstance(entry, dict) and entry.get("name") == column:
                    return entry
    return None


def _apply(lines: list[str], wanted: _Wanted) -> list[str] | str:
    """Return edited lines, or the reason the fix must be made by hand."""
    start = _find_column(lines, wanted.model, wanted.column)
    if start is None:
        return f"{wanted.column} is not listed under {wanted.model} in {wanted.path}"
    indent, end = _block(lines, start)
    key_indent = " " * (indent + 2)
    keys = {
        line.strip().split(":", 1)[0]
        for line in lines[start + 1 : end]
        if line.startswith(key_indent) and not line.startswith(key_indent + " ")
    }
    if wanted.kind == "inherit_description":
        if "description" in keys:
            return f"{wanted.column} already has a description key"
        insert = [f"{key_indent}description: {_scalar(wanted.description)}"]
        return lines[: start + 1] + insert + lines[start + 1 :]
    if "config" in keys or "meta" in keys:
        return f"{wanted.column} already has a config or meta block; add sensitive: true there"
    insert = [f"{key_indent}config:", f"{key_indent}  meta:", f"{key_indent}    sensitive: true"]
    return lines[:end] + insert + lines[end:]


def _verified(before: str, after: str, wanted: _Wanted) -> bool:
    """Check that the edit added exactly the intended key to the column, and nothing else."""
    old, new = yaml.safe_load(before), yaml.safe_load(after)
    old_column = _column(old, wanted.model, wanted.column)
    new_column = _column(new, wanted.model, wanted.column)
    if old_column is None or new_column is None:
        return False
    expected = dict(old_column)
    if wanted.kind == "inherit_description":
        expected["description"] = wanted.description
    else:
        expected["config"] = {"meta": {"sensitive": True}}
    if new_column != expected:
        return False
    old_column.update(new_column)  # The rest of the document must be byte-for-byte equal.
    return bool(old == new)


def plan_fixes(agents: Sequence[Agent], manifest: Manifest, dbt_project_dir: Path) -> FixPlan:
    """Compute every certain fix for the agents' scopes and the patch that applies them."""
    originals: dict[str, str] = {}
    edited: dict[str, list[str]] = {}
    fixes: list[Fix] = []
    manual: list[ManualFix] = []
    for wanted in _wanted(agents, manifest):
        if wanted.path is None:
            manual.append(
                ManualFix(
                    kind=wanted.kind,
                    model=wanted.model,
                    column=wanted.column,
                    reason=f"{wanted.model} has no schema YAML file in the manifest",
                )
            )
            continue
        if wanted.path not in edited:
            originals[wanted.path] = (dbt_project_dir / wanted.path).read_text()
            edited[wanted.path] = originals[wanted.path].splitlines()
        before = "\n".join(edited[wanted.path]) + "\n"
        result = _apply(edited[wanted.path], wanted)
        if isinstance(result, str) or not _verified(before, "\n".join(result) + "\n", wanted):
            reason = result if isinstance(result, str) else "the edit did not re-parse as intended"
            manual.append(
                ManualFix(kind=wanted.kind, model=wanted.model, column=wanted.column, reason=reason)
            )
            continue
        edited[wanted.path] = result
        fixes.append(
            Fix(
                kind=wanted.kind,
                model=wanted.model,
                column=wanted.column,
                path=wanted.path,
                detail=(
                    f"inherit '{wanted.description}' from upstream"
                    if wanted.kind == "inherit_description"
                    else "set config.meta.sensitive: true"
                ),
            )
        )
    patch = "".join(
        "".join(
            difflib.unified_diff(
                originals[path].splitlines(keepends=True),
                [line + "\n" for line in edited[path]],
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        )
        for path in sorted(edited)
        if originals[path].splitlines() != edited[path]
    )
    return FixPlan(fixes=fixes, manual=manual, patch=patch)
