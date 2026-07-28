"""
memory.py — Per-agent persistent fact memory (item 15).

Agents can store and retrieve key/value facts that persist across sessions.
Facts are stored in .tabletalk_memory/<agent_name>.yaml inside the project
folder.

Usage:
    from tabletalk.memory import set_fact, get_fact, list_facts, clear_facts

    set_fact(project, "analyst", "preferred_timezone", "UTC")
    tz = get_fact(project, "analyst", "preferred_timezone")
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger("tabletalk")

_MEMORY_DIR = ".tabletalk_memory"


def _agent_path(project_folder: str, agent_name: str) -> str:
    return os.path.join(project_folder, _MEMORY_DIR, f"{agent_name}.yaml")


def _load(project_folder: str, agent_name: str) -> Dict[str, Any]:
    path = _agent_path(project_folder, agent_name)
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _save(project_folder: str, agent_name: str, data: Dict[str, Any]) -> None:
    mem_dir = os.path.join(project_folder, _MEMORY_DIR)
    os.makedirs(mem_dir, exist_ok=True)
    with open(_agent_path(project_folder, agent_name), "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=True)


# ── Public API ────────────────────────────────────────────────────────────────


def set_fact(
    project_folder: str,
    agent_name: str,
    key: str,
    value: Any,
) -> None:
    """Store or update a fact for the given agent."""
    mem = _load(project_folder, agent_name)
    mem[key] = {
        "value": value,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save(project_folder, agent_name, mem)
    logger.debug(f"Memory set [{agent_name}] {key} = {value!r}")


def get_fact(
    project_folder: str,
    agent_name: str,
    key: str,
    default: Any = None,
) -> Any:
    """Retrieve a fact value, or *default* if not found."""
    mem = _load(project_folder, agent_name)
    entry = mem.get(key)
    if entry is None:
        return default
    return entry.get("value", default)


def delete_fact(project_folder: str, agent_name: str, key: str) -> bool:
    """Delete a single fact. Returns True if it existed."""
    mem = _load(project_folder, agent_name)
    if key not in mem:
        return False
    del mem[key]
    _save(project_folder, agent_name, mem)
    return True


def list_facts(project_folder: str, agent_name: str) -> List[Dict[str, Any]]:
    """Return all facts for an agent as a list of {key, value, updated_at} dicts."""
    mem = _load(project_folder, agent_name)
    return [
        {"key": k, "value": v.get("value"), "updated_at": v.get("updated_at")}
        for k, v in sorted(mem.items())
    ]


def clear_facts(project_folder: str, agent_name: str) -> int:
    """Delete all facts for an agent. Returns the count removed."""
    mem = _load(project_folder, agent_name)
    count = len(mem)
    _save(project_folder, agent_name, {})
    logger.info(f"Memory cleared [{agent_name}]: {count} facts removed")
    return count


def list_agents_with_memory(project_folder: str) -> List[str]:
    """Return agent names that have a memory file."""
    mem_dir = os.path.join(project_folder, _MEMORY_DIR)
    if not os.path.isdir(mem_dir):
        return []
    return [
        f[:-5]  # strip .yaml
        for f in sorted(os.listdir(mem_dir))
        if f.endswith(".yaml")
    ]
