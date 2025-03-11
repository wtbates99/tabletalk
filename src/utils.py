import yaml
import json
from factories import get_db_provider
import os


def initialize_project():
    """Initialize a new project by creating a folder structure with default configuration files."""
    project_name = "tabletext"
    project_folder = project_name

    # Check if the project folder already exists
    if os.path.exists(project_folder):
        print(
            f"Folder {project_folder} already exists. Please choose a different project name or remove the existing folder."
        )
        return

    # Create the project folder
    os.makedirs(project_folder)

    # Write config.yaml with the desired order and comments
    config_yaml_path = os.path.join(project_folder, "config.yaml")
    config_content = """
# Configuration for the data provider
provider:
  type: bigquery  # Type of the provider, e.g., bigquery, snowflake, etc.
  project_id: your-gcp-project-id  # GCP project ID for BigQuery
  use_default_credentials: true  # Whether to use default GCP credentials

# Configuration for the LLM
llm:
  provider: openai  # LLM provider, e.g., openai, anthropic, etc.
  api_key: your-openai-api-key  # API key for the LLM provider
  model: text-davinci-003  # Model to use
  max_tokens: 150  # Maximum number of tokens to generate
  temperature: 0  # Sampling temperature
"""
    with open(config_yaml_path, "w") as file:
        file.write(config_content)

    # Create contexts folder and write a sample context YAML file
    contexts_folder = os.path.join(project_folder, "contexts")
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

    # Create manifest folder
    manifest_folder = os.path.join(project_folder, "manifest")
    os.makedirs(manifest_folder)

    print(
        f"Project {project_name} initialized. Edit the files in {project_folder}/config.yaml and {project_folder}/contexts/ to customize your settings."
    )


def apply_schema(project_folder):
    """Apply the schema to all contexts in the project folder, generating JSON files in the manifest folder."""
    # Load default settings from config.yaml
    config_path = os.path.join(project_folder, "config.yaml")
    with open(config_path, "r") as file:
        defaults = yaml.safe_load(file)

    default_provider = defaults.get("provider", {})
    default_llm = defaults.get("llm", {})

    contexts_folder = os.path.join(project_folder, "contexts")
    manifest_folder = os.path.join(project_folder, "manifest")

    total_tables = 0
    processed_contexts = 0

    # Process each YAML file in the contexts folder
    for context_file in os.listdir(contexts_folder):
        if context_file.endswith(".yaml"):
            context_path = os.path.join(contexts_folder, context_file)
            with open(context_path, "r") as file:
                context_config = yaml.safe_load(file)

            # Merge default settings with context-specific settings
            provider = {**default_provider, **context_config.get("provider", {})}
            llm = {**default_llm, **context_config.get("llm", {})}
            datasets = context_config["datasets"]
            context_name = context_config.get("name", os.path.splitext(context_file)[0])

            # Generate compact tables (assuming generate_compact_tables is defined elsewhere)
            db_provider = get_db_provider(provider)
            client = db_provider.get_client()
            type_map = db_provider.get_database_type_map()
            compact_tables = generate_compact_tables(client, datasets, type_map)

            # Prepare context data
            context_data = {
                "name": context_name,
                "provider": provider,
                "llm": llm,
                "compact_tables": compact_tables,
            }

            # Write to manifest folder
            context_output_path = os.path.join(manifest_folder, f"{context_name}.json")
            with open(context_output_path, "w") as outfile:
                json.dump(context_data, outfile, indent=2)

            total_tables += len(compact_tables)
            processed_contexts += 1
            print(
                f"Successfully generated {context_output_path} with {len(compact_tables)} tables"
            )

    print(f"Total: {total_tables} tables across {processed_contexts} contexts")


# Assuming generate_compact_tables remains unchanged from your original code
def generate_compact_tables(client, datasets, type_map):
    compact_tables = []
    for dataset_item in datasets:
        dataset_id = dataset_item["name"]
        table_ids = dataset_item.get("tables", [])
        if table_ids:
            tables = [
                client.get_table(f"{dataset_id}.{table_id}") for table_id in table_ids
            ]
        else:
            dataset_ref = client.dataset(dataset_id)
            tables = [
                client.get_table(table) for table in client.list_tables(dataset_ref)
            ]
        for table_ref in tables:
            compact_fields = [
                {"n": field.name, "t": type_map.get(field.field_type, field.field_type)}
                for field in table_ref.schema
            ]
            compact_tables.append(
                {
                    "t": f"{table_ref.dataset_id}.{table_ref.table_id}",
                    "d": table_ref.description if table_ref.description else "",
                    "f": compact_fields,
                }
            )
    return compact_tables
