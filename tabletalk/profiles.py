"""
Profile management — connection configs stored at ~/.tabletalk/profiles.yml
Works just like ~/.dbt/profiles.yml so the workflow feels familiar.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger("tabletalk")

PROFILES_DIR = Path.home() / ".tabletalk"
PROFILES_FILE = PROFILES_DIR / "profiles.yml"


def load_profiles() -> Dict[str, Any]:
    """Return all saved profiles (empty dict if file doesn't exist)."""
    if not PROFILES_FILE.exists():
        return {}
    with open(PROFILES_FILE) as f:
        return yaml.safe_load(f) or {}


def get_profile(name: str) -> Optional[Dict[str, Any]]:
    """Return a single profile by name, or None if not found."""
    return load_profiles().get(name)


def save_profile(name: str, config: Dict[str, Any]) -> None:
    """Write or overwrite a profile."""
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    profiles = load_profiles()
    profiles[name] = config
    with open(PROFILES_FILE, "w") as f:
        yaml.dump(profiles, f, default_flow_style=False, sort_keys=False)


def delete_profile(name: str) -> bool:
    """Delete a profile. Returns True if it existed."""
    profiles = load_profiles()
    if name not in profiles:
        return False
    del profiles[name]
    with open(PROFILES_FILE, "w") as f:
        yaml.dump(profiles, f, default_flow_style=False, sort_keys=False)
    return True


def list_profiles() -> List[str]:
    """Return sorted list of all profile names."""
    return sorted(load_profiles().keys())


def import_from_dbt(dbt_profile: str, target: str = "dev") -> Optional[Dict[str, Any]]:
    """
    Import a connection from an existing dbt profiles.yml.
    Returns a tabletalk-formatted config dict, or None if not found / unsupported.

    Usage example:
        config = import_from_dbt("my_dbt_project", target="prod")
        if config:
            save_profile("my_dbt_project_prod", config)
    """
    dbt_file = Path.home() / ".dbt" / "profiles.yml"
    if not dbt_file.exists():
        logger.warning("No ~/.dbt/profiles.yml found")
        return None

    with open(dbt_file) as f:
        dbt_profiles = yaml.safe_load(f) or {}

    profile = dbt_profiles.get(dbt_profile, {})
    outputs = profile.get("outputs", {})
    target_config = outputs.get(target) or outputs.get("dev")
    if not target_config:
        logger.warning(f"Target '{target}' not found in dbt profile '{dbt_profile}'")
        return None

    db_type = target_config.get("type", "").lower()

    if db_type == "postgres":
        return {
            "type": "postgres",
            "host": target_config.get("host", "localhost"),
            "port": int(target_config.get("port", 5432)),
            "database": target_config.get("dbname") or target_config.get("database"),
            "user": target_config.get("user"),
            "password": target_config.get("password"),
        }
    elif db_type == "snowflake":
        cfg: Dict[str, Any] = {
            "type": "snowflake",
            "account": target_config.get("account"),
            "user": target_config.get("user"),
            "password": target_config.get("password"),
            "database": target_config.get("database"),
            "warehouse": target_config.get("warehouse"),
            "schema": target_config.get("schema", "PUBLIC"),
        }
        if target_config.get("role"):
            cfg["role"] = target_config["role"]
        return cfg
    elif db_type == "bigquery":
        return {
            "type": "bigquery",
            "project_id": target_config.get("project"),
            "use_default_credentials": target_config.get("method") == "oauth",
        }
    elif db_type == "sqlserver":
        return {
            "type": "azuresql",
            "server": target_config.get("server"),
            "database": target_config.get("database"),
            "user": target_config.get("username") or target_config.get("user"),
            "password": target_config.get("password"),
        }
    elif db_type == "duckdb":
        return {
            "type": "duckdb",
            "database_path": target_config.get("path", ":memory:"),
        }

    logger.warning(f"Unsupported dbt adapter type: '{db_type}'")
    return None
