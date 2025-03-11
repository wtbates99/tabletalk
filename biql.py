import sys
import yaml
from google.cloud import bigquery
import json


def apply(config_path):
    """
    Parse biql.yaml, authenticate with GCP, extract BigQuery metadata, and generate biql_context.json.
    """
    # Load the configuration from biql.yaml
    with open(config_path, "r") as file:
        config = yaml.safe_load(file)

    provider = config["provider"]
    llm = config.get("llm", {})  # Optional LLM configuration
    datasets = config["datasets"]

    # Authenticate with GCP and initialize BigQuery client
    if provider.get("use_default_credentials", False):
        # Use default credentials
        client = bigquery.Client(project=provider["project_id"])
    else:
        # Use service account credentials file
        client = bigquery.Client.from_service_account_json(
            provider["credentials"], project=provider["project_id"]
        )

    # Extract metadata for all tables in the specified datasets
    tables = []
    for dataset_item in datasets:
        dataset_id = dataset_item["dataset"]  # Get the dataset ID as a string

        # List all tables in the dataset or use specified tables if provided
        dataset_ref = client.dataset(dataset_id)

        if "tables" in dataset_item and dataset_item["tables"]:
            # Process only specified tables
            for table_id in dataset_item["tables"]:
                table_ref = client.get_table(f"{dataset_id}.{table_id}")
                fields = [
                    {
                        "name": field.name,
                        "type": field.field_type,
                        "description": field.description if field.description else "",
                    }
                    for field in table_ref.schema
                ]
                tables.append(
                    {
                        "name": f"{dataset_id}.{table_id}",
                        "description": table_ref.description
                        if table_ref.description
                        else "",
                        "fields": fields,
                    }
                )
        else:
            # Process all tables in the dataset
            dataset_tables = client.list_tables(dataset_ref)
            for table in dataset_tables:
                table_ref = client.get_table(table)
                fields = [
                    {
                        "name": field.name,
                        "type": field.field_type,
                        "description": field.description if field.description else "",
                    }
                    for field in table_ref.schema
                ]
                tables.append(
                    {
                        "name": f"{dataset_id}.{table.table_id}",
                        "description": table_ref.description
                        if table_ref.description
                        else "",
                        "fields": fields,
                    }
                )

    # Structure the context with provider, LLM config, and table metadata
    context = {"provider": provider, "llm": llm, "tables": tables}

    # Write the context to biql_context.json
    with open("biql_context.json", "w") as outfile:
        json.dump(context, outfile, indent=2)

    print(f"Successfully generated biql_context.json with {len(tables)} tables")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python biql.py apply [path/to/biql.yaml]")
        sys.exit(1)

    command = sys.argv[1]
    if command == "apply":
        config_path = sys.argv[2] if len(sys.argv) > 2 else "biql.yaml"
        apply(config_path)
    else:
        print("Unknown command")
        sys.exit(1)
