"""
Profile management — connection configs stored at ~/.tabletalk/profiles.yml.
Works just like ~/.dbt/profiles.yml so the workflow feels familiar.

item 19: Optional keyring integration. If the `keyring` package is installed,
         passwords are stored in the OS credential store (macOS Keychain,
         Windows Credential Manager, SecretService on Linux) rather than in
         plaintext YAML. Falls back gracefully to plaintext with a warning when
         keyring is unavailable.

         Install keyring support:  pip install keyring
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger("tabletalk")

PROFILES_DIR = Path.home() / ".tabletalk"
PROFILES_FILE = PROFILES_DIR / "profiles.yml"

_KEYRING_SERVICE = "tabletalk"
_SENSITIVE_KEYS = {"password", "credentials"}

# ── Keyring availability (item 19) ────────────────────────────────────────────

try:
    import keyring as _keyring

    _HAS_KEYRING = True
except ImportError:
    _HAS_KEYRING = False


def _keyring_key(profile_name: str, field: str) -> str:
    """Stable key used to store a credential in the OS keychain."""
    return f"{profile_name}.{field}"


def _store_secret(profile_name: str, field: str, value: str) -> bool:
    """Store `value` in the OS keychain. Returns True on success."""
    if not _HAS_KEYRING:
        return False
    try:
        _keyring.set_password(_KEYRING_SERVICE, _keyring_key(profile_name, field), value)
        return True
    except Exception as exc:
        logger.warning(f"keyring.set_password failed: {exc} — falling back to plaintext")
        return False


def _load_secret(profile_name: str, field: str) -> Optional[str]:
    """Retrieve a credential from the OS keychain. Returns None if not found."""
    if not _HAS_KEYRING:
        return None
    try:
        return _keyring.get_password(_KEYRING_SERVICE, _keyring_key(profile_name, field))
    except Exception:
        return None


def _delete_secret(profile_name: str, field: str) -> None:
    if not _HAS_KEYRING:
        return
    try:
        _keyring.delete_password(_KEYRING_SERVICE, _keyring_key(profile_name, field))
    except Exception:
        pass


# ── Core profile CRUD ─────────────────────────────────────────────────────────


def load_profiles() -> Dict[str, Any]:
    """Return all saved profiles (empty dict if file doesn't exist)."""
    if not PROFILES_FILE.exists():
        return {}
    with open(PROFILES_FILE) as f:
        return yaml.safe_load(f) or {}


def get_profile(name: str) -> Optional[Dict[str, Any]]:
    """
    Return a single profile by name, or None if not found.
    Sensitive fields (password, credentials) are merged back from the
    OS keychain when keyring is available.
    """
    profile = load_profiles().get(name)
    if profile is None:
        return None

    if _HAS_KEYRING:
        for key in _SENSITIVE_KEYS:
            secret = _load_secret(name, key)
            if secret is not None:
                profile[key] = secret

    return profile


def save_profile(name: str, config: Dict[str, Any]) -> None:
    """
    Write or overwrite a profile.

    When keyring is available, sensitive fields (password, credentials) are
    extracted, stored in the OS keychain, and replaced with a sentinel marker
    in the YAML file so the file itself contains no plaintext secrets.

    When keyring is not available, a warning is logged and secrets are stored
    in plaintext (existing behaviour, unchanged for users who don't install keyring).
    """
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    profiles = load_profiles()

    stored_config = dict(config)

    if _HAS_KEYRING:
        for key in _SENSITIVE_KEYS:
            if key in stored_config and stored_config[key]:
                ok = _store_secret(name, key, str(stored_config[key]))
                if ok:
                    stored_config[key] = "__keyring__"
                    logger.debug(f"Stored '{key}' for profile '{name}' in OS keychain.")
    else:
        sensitive_present = [k for k in _SENSITIVE_KEYS if k in stored_config and stored_config[k]]
        if sensitive_present:
            logger.warning(
                f"Profile '{name}' contains sensitive fields {sensitive_present} stored "
                "in plaintext in ~/.tabletalk/profiles.yml. "
                "Install 'keyring' (pip install keyring) to store them in the OS keychain."
            )

    profiles[name] = stored_config
    with open(PROFILES_FILE, "w") as f:
        yaml.dump(profiles, f, default_flow_style=False, sort_keys=False)


def delete_profile(name: str) -> bool:
    """Delete a profile. Returns True if it existed."""
    profiles = load_profiles()
    if name not in profiles:
        return False
    del profiles[name]

    # Also remove any stored keyring secrets
    for key in _SENSITIVE_KEYS:
        _delete_secret(name, key)

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
