import yaml
import json
from factories import get_db_provider

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


def get_type_explanation():
    return ", ".join([f"{v}={k}" for k, v in type_map.items()])


def generate_compact_tables(client, datasets):
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


def apply_schema(config_path, output_path=None):
    with open(config_path, "r") as file:
        config = yaml.safe_load(file)

    # Check if config has contexts or is a single context
    if "contexts" in config:
        contexts = config["contexts"]
    else:
        # Treat the entire config as a single context
        contexts = [config]

    results = []
    for idx, context_config in enumerate(contexts):
        provider = context_config["provider"]
        llm = context_config.get("llm", {})
        datasets = context_config["datasets"]
        context_name = context_config.get("name", f"context_{idx}")

        db_provider = get_db_provider(provider)
        client = db_provider.get_client()
        compact_tables = generate_compact_tables(client, datasets)

        context_data = {
            "name": context_name,
            "provider": provider,
            "llm": llm,
            "compact_tables": compact_tables,
        }
        results.append(context_data)

    # Determine output path if not provided
    if output_path is None:
        output_path = config_path.replace(".yaml", ".json")
        if output_path == config_path:  # If no .yaml extension
            output_path = f"{config_path}.json"

    with open(output_path, "w") as outfile:
        json.dump(results, outfile, indent=2)

    total_tables = sum(len(context["compact_tables"]) for context in results)
    print(
        f"Successfully generated {output_path} with {total_tables} tables across {len(results)} contexts"
    )
