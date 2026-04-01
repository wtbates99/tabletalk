import logging
import os

import yaml

from tabletalk.factories import get_db_provider
from tabletalk.interfaces import Parser

logger = logging.getLogger("tabletalk")


def initialize_project() -> None:
    """Initialize a new project in the current working directory."""
    project_folder = os.getcwd()
    config_path = os.path.join(project_folder, "tabletalk.yaml")

    if os.path.exists(config_path):
        print(f"Already initialized ({config_path} exists).")
        return

    config_content = """\
# tabletalk.yaml
#
# Option A — reference a profile from ~/.tabletalk/profiles.yml
#   Run 'tabletalk connect' to create one, then set:
#
# profile: my_snowflake
#
# Option B — inline connection (not recommended for passwords)
#
provider:
  type: postgres          # postgres | snowflake | duckdb | azuresql | bigquery | mysql | sqlite
  host: localhost
  port: 5432
  database: mydb
  user: myuser
  password: ${DB_PASSWORD}

# LLM configuration
llm:
  provider: openai        # openai | anthropic | ollama
  api_key: ${OPENAI_API_KEY}
  model: gpt-4o
  max_tokens: 1000
  temperature: 0

contexts: contexts
output: manifest

# ── Optional tuning ────────────────────────────────────────────────────────────
# safe_mode: true          # block non-SELECT queries
# max_rows: 500            # cap result set size (item 17)
# query_timeout: 30        # kill queries after N seconds (item 16)
# max_conv_messages: 20    # conversation history window (item 11)
# audit_log: false         # set true to write .tabletalk_audit.jsonl (item 18)
"""

    with open(config_path, "w") as f:
        f.write(config_content)

    contexts_folder = os.path.join(project_folder, "contexts")
    os.makedirs(contexts_folder, exist_ok=True)

    sample_context = """\
name: default_context
description: "Default context — edit this to match your schema."
datasets:
  - name: public
    description: "Main schema."
    tables:
      - name: customers
        description: "Customer records."
      - name: orders
        description: "Order records."
"""
    with open(os.path.join(contexts_folder, "default_context.yaml"), "w") as f:
        f.write(sample_context)

    os.makedirs(os.path.join(project_folder, "manifest"), exist_ok=True)

    print(
        "Project initialized.\n"
        "Next steps:\n"
        "  1. Run 'tabletalk connect' to configure your database connection\n"
        "  2. Edit contexts/default_context.yaml to describe your tables\n"
        "  3. Run 'tabletalk apply' to generate manifests\n"
        "  4. Run 'tabletalk query' or 'tabletalk serve' to start querying"
    )


def apply_schema(project_folder: str) -> None:
    """Connect to the database, introspect the schema, write manifests."""
    config_path = os.path.join(project_folder, "tabletalk.yaml")
    with open(config_path, "r") as f:
        defaults = yaml.safe_load(f)

    # Support both inline 'provider' block and 'profile' reference at top level
    if "profile" in defaults:
        provider_config = {"profile": defaults["profile"]}
    else:
        provider_config = defaults.get("provider", {})

    db_provider = get_db_provider(provider_config)
    parser = Parser(project_folder, db_provider)
    parser.apply_schema()


def check_manifest_staleness(project_folder: str) -> bool:
    """Return True if any context file is newer than its corresponding manifest."""
    contexts_path = os.path.join(project_folder, "contexts")
    manifest_path = os.path.join(project_folder, "manifest")

    if not os.path.exists(manifest_path):
        return True
    if not os.path.exists(contexts_path):
        return False

    for context_file in os.listdir(contexts_path):
        if not context_file.endswith(".yaml"):
            continue
        context_mtime = os.path.getmtime(os.path.join(contexts_path, context_file))
        manifest_file = os.path.join(
            manifest_path, context_file.replace(".yaml", ".txt")
        )
        if not os.path.exists(manifest_file):
            return True
        if context_mtime > os.path.getmtime(manifest_file):
            return True
    return False
