import sys
import yaml
from google.cloud import bigquery
import json


def apply(config_path):
    """
    Parse biql.yaml, authenticate with GCP, extract BigQuery metadata, and generate biql_context.json with compact schema.
    """
    # Load the configuration from biql.yaml
    with open(config_path, "r") as file:
        config = yaml.safe_load(file)

    provider = config["provider"]
    llm = config.get("llm", {})  # Optional LLM configuration
    datasets = config["datasets"]

    # Authenticate with GCP and initialize BigQuery client
    if provider.get("use_default_credentials", False):
        client = bigquery.Client(project=provider["project_id"])
    else:
        client = bigquery.Client.from_service_account_json(
            provider["credentials"], project=provider["project_id"]
        )

    # Define type map for compacting field types
    type_map = {
        "STRING": "S",
        "FLOAT": "F",
        "DATE": "D",
        "INTEGER": "I",
        "TIMESTAMP": "TS",
        "BOOLEAN": "B",
        "NUMERIC": "N",
        "ARRAY": "A",
        "STRUCT": "ST",
        "BYTES": "BY",
        "GEOGRAPHY": "G",
    }

    # Extract metadata and create compact table representations
    compact_tables = []
    for dataset_item in datasets:
        dataset_id = dataset_item["dataset"]

        dataset_ref = client.dataset(dataset_id)
        if "tables" in dataset_item and dataset_item["tables"]:
            # Process specified tables
            for table_id in dataset_item["tables"]:
                table_ref = client.get_table(f"{dataset_id}.{table_id}")
                compact_fields = [
                    {
                        "n": field.name,
                        "t": type_map.get(field.field_type, field.field_type),
                    }
                    for field in table_ref.schema
                ]
                compact_tables.append(
                    {
                        "t": f"{dataset_id}.{table_id}",
                        "d": table_ref.description if table_ref.description else "",
                        "f": compact_fields,
                    }
                )
        else:
            # Process all tables in the dataset
            dataset_tables = client.list_tables(dataset_ref)
            for table in dataset_tables:
                table_ref = client.get_table(table)
                compact_fields = [
                    {
                        "n": field.name,
                        "t": type_map.get(field.field_type, field.field_type),
                    }
                    for field in table_ref.schema
                ]
                compact_tables.append(
                    {
                        "t": f"{dataset_id}.{table.table_id}",
                        "d": table_ref.description if table_ref.description else "",
                        "f": compact_fields,
                    }
                )

    # Structure the context with compact tables
    context = {"provider": provider, "llm": llm, "compact_tables": compact_tables}

    # Write to biql_context.json
    with open("biql_context.json", "w") as outfile:
        json.dump(context, outfile, indent=2)

    print(f"Successfully generated biql_context.json with {len(compact_tables)} tables")


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
