import yaml
import json
from factories import get_db_provider, get_llm_provider
import os


def initialize_project():
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
  api_key: your-openai-api-key  # API key for the LLM provider
  model: text-davinci-003  # Model to use
  max_tokens: 150  # Maximum number of tokens to generate
  temperature: 0  # Sampling temperature
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


def apply_schema(project_folder):
    """Apply the schema to all contexts in the project folder, generating JSON files in the manifest folder."""
    config_path = os.path.join(project_folder, "tabletext.yaml")
    with open(config_path, "r") as file:
        defaults = yaml.safe_load(file)

    # Initialize the database provider once using the project-wide configuration
    provider_config = defaults.get("provider", {})
    db_provider = get_db_provider(provider_config)
    client = db_provider.get_client()
    type_map = db_provider.get_database_type_map()

    contexts_folder = os.path.join(project_folder, "contexts")
    manifest_folder = os.path.join(project_folder, "manifest")

    total_tables = 0
    processed_contexts = 0

    for context_file in os.listdir(contexts_folder):
        if context_file.endswith(".yaml"):
            context_path = os.path.join(contexts_folder, context_file)
            with open(context_path, "r") as file:
                context_config = yaml.safe_load(file)

            datasets = context_config["datasets"]
            context_name = context_config.get("name", os.path.splitext(context_file)[0])

            # Use the single provider instance to generate compact tables
            compact_tables = generate_compact_tables(client, datasets, type_map)

            # Create a new context_data dictionary with ONLY the name and compact_tables
            # This ensures no provider or LLM information is included
            context_data = {
                "name": context_name,
                "compact_tables": compact_tables,
            }

            context_output_path = os.path.join(manifest_folder, f"{context_name}.json")
            with open(context_output_path, "w") as outfile:
                json.dump(context_data, outfile, indent=2)

            total_tables += len(compact_tables)
            processed_contexts += 1
            print(
                f"Successfully generated {context_output_path} with {len(compact_tables)} tables"
            )

    print(f"Total: {total_tables} tables across {processed_contexts} contexts")


def generate_compact_tables(client, datasets, type_map):
    """Generate compact table schemas using the provided client."""
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


def ask_question(project_folder, context_name, question):
    """Ask a question using the specified context."""
    config_path = os.path.join(project_folder, "tabletext.yaml")
    with open(config_path, "r") as file:
        defaults = yaml.safe_load(file)

    # Initialize the LLM once using the project-wide configuration
    llm_config = defaults.get("llm", {})
    llm_provider = get_llm_provider(llm_config)

    # Load the specified context from the manifest
    manifest_folder = os.path.join(project_folder, "manifest")
    context_path = os.path.join(manifest_folder, f"{context_name}.json")
    with open(context_path, "r") as file:
        context_data = json.load(file)

    compact_tables = context_data["compact_tables"]

    # Generate the prompt with table schemas and the question
    prompt = "Given the following table schemas:\n\n"
    for table in compact_tables:
        prompt += f"Table: {table['t']}\n"
        if table["d"]:
            prompt += f"Description: {table['d']}\n"
        prompt += "Fields:\n"
        for field in table["f"]:
            prompt += f"  {field['n']}: {field['t']}\n"
        prompt += "\n"
    prompt += f"Answer the following question: {question}"

    # Get the response from the LLM
    response = llm_provider.generate_response(prompt)
    print(response)
