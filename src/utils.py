import os

import yaml
from factories import get_db_provider
from interfaces import Parser


def initialize_project() -> None:
    """Initialize a new project by creating configuration files in the current working directory."""
    project_folder = os.getcwd()
    config_yaml_path = os.path.join(project_folder, "tabletext.yaml")
    if os.path.exists(config_yaml_path):
        print(f"File {config_yaml_path} already exists.")
        return

    config_content = """
# Configuration for the data provider
provider:
  type: bigquery  # Type of the provider, e.g., bigquery, snowflake, etc.
  project_id: your-gcp-project-id  # GCP project ID for BigQuery
  use_default_credentials: true  # Whether to use default GCP credentials

# Configuration for the LLM
llm:
  provider: openai  # LLM provider, e.g., openai, anthropic, etc.
  api_key: ${OPENAI_API_KEY}  # Use environment variable for API key
  model: gpt-4o  # Model to use
  max_tokens: 500  # Maximum number of tokens to generate
  temperature: 0  # Sampling temperature

contexts: contexts
output: manifest
"""
    with open(config_yaml_path, "w") as file:
        file.write(config_content)

    contexts_folder = os.path.join(project_folder, "contexts")
    if not os.path.exists(contexts_folder):
        os.makedirs(contexts_folder)

    sample_context_path = os.path.join(contexts_folder, "default_context.yaml")
    sample_context_content = """
# Sample context configuration
name: default_context  # Name of the context
datasets:
  - name: your-dataset-name  # Name of the dataset
    tables:
      - your-table-name  # Table within the dataset
"""
    with open(sample_context_path, "w") as file:
        file.write(sample_context_content)

    manifest_folder = os.path.join(project_folder, "manifest")
    if not os.path.exists(manifest_folder):
        os.makedirs(manifest_folder)

    print(
        "Project initialized in the current directory. Edit tabletext.yaml and contexts/default_context.yaml to customize your settings."
    )


def apply_schema(project_folder: str) -> None:
    """Apply the schema to all contexts in the project folder, generating JSON files in the manifest folder."""

    config_path = os.path.join(project_folder, "tabletext.yaml")
    with open(config_path, "r") as file:
        defaults = yaml.safe_load(file)

    # Instantiate the provider
    provider_config = defaults.get("provider", {})
    db_provider = get_db_provider(provider_config)
    parser = Parser(project_folder, db_provider)
    parser.apply_schema()
