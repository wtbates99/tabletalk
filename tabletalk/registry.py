"""
registry.py — Agent registry for tabletalk fleet management.

Tracks named agents, their assigned manifests, permissions, and last-seen
timestamps in a YAML file (.tabletalk_agents.yaml) in the project folder.

item 4: Agent registry — register/list/remove agents, assign manifests.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger("tabletalk")

_REGISTRY_FILE = ".tabletalk_agents.yaml"


def _registry_path(project_folder: str) -> str:
    return os.path.join(project_folder, _REGISTRY_FILE)


def _load(project_folder: str) -> Dict[str, Any]:
    path = _registry_path(project_folder)
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _save(project_folder: str, data: Dict[str, Any]) -> None:
    with open(_registry_path(project_folder), "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=True)


# ── Public API ────────────────────────────────────────────────────────────────


def register_agent(
    project_folder: str,
    name: str,
    manifest: Optional[str] = None,
    permissions: Optional[List[str]] = None,
    description: str = "",
) -> Dict[str, Any]:
    """
    Register a named agent in the project registry.
    If the agent already exists, its fields are updated.
    Returns the agent entry dict.
    """
    registry = _load(project_folder)
    now = datetime.now(timezone.utc).isoformat()

    entry: Dict[str, Any] = registry.get(name, {})
    entry["name"] = name
    entry["manifest"] = manifest
    entry["permissions"] = permissions or ["read"]
    entry["description"] = description
    entry.setdefault("registered_at", now)
    entry["updated_at"] = now

    registry[name] = entry
    _save(project_folder, registry)
    logger.info(f"Agent registered: {name}")
    return entry


def list_agents(project_folder: str) -> List[Dict[str, Any]]:
    """Return all registered agents as a list, sorted by name."""
    registry = _load(project_folder)
    return sorted(registry.values(), key=lambda a: a.get("name", ""))


def get_agent(project_folder: str, name: str) -> Optional[Dict[str, Any]]:
    """Return a single agent entry, or None if not found."""
    return _load(project_folder).get(name)


def remove_agent(project_folder: str, name: str) -> bool:
    """Remove an agent from the registry. Returns True if it existed."""
    registry = _load(project_folder)
    if name not in registry:
        return False
    del registry[name]
    _save(project_folder, registry)
    logger.info(f"Agent removed: {name}")
    return True


def ping_agent(project_folder: str, name: str) -> Optional[Dict[str, Any]]:
    """Update the last_seen timestamp for an agent. Returns the entry or None."""
    registry = _load(project_folder)
    if name not in registry:
        return None
    registry[name]["last_seen"] = datetime.now(timezone.utc).isoformat()
    _save(project_folder, registry)
    return registry[name]


def agent_has_permission(project_folder: str, name: str, permission: str) -> bool:
    """
    Check whether a registered agent has a given permission.
    Unknown agents are denied. Agents with the 'admin' permission pass all checks.
    """
    entry = get_agent(project_folder, name)
    if entry is None:
        return False
    perms: List[str] = entry.get("permissions", [])
    return "admin" in perms or permission in perms
