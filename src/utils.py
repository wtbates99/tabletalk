import yaml
import json
from factories import get_db_provider


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


def apply_schema(config_path, output_path=None):
    with open(config_path, "r") as file:
        config = yaml.safe_load(file)

    if "contexts" in config:
        contexts = config["contexts"]
    else:
        contexts = [config]

    results = []
    total_tables = 0

    for idx, context_config in enumerate(contexts):
        provider = context_config["provider"]
        llm = context_config.get("llm", {})
        datasets = context_config["datasets"]
        context_name = context_config.get("name", f"context_{idx}")

        db_provider = get_db_provider(provider)
        client = db_provider.get_client()
        type_map = db_provider.get_database_type_map()
        compact_tables = generate_compact_tables(client, datasets, type_map)

        context_data = {
            "name": context_name,
            "provider": provider,
            "llm": llm,
            "compact_tables": compact_tables,
        }
        results.append(context_data)

        context_output_path = f"{context_name}.json"
        with open(context_output_path, "w") as outfile:
            json.dump(context_data, outfile, indent=2)

        total_tables += len(compact_tables)
        print(
            f"Successfully generated {context_output_path} with {len(compact_tables)} tables"
        )

    if output_path is not None:
        with open(output_path, "w") as outfile:
            json.dump(results, outfile, indent=2)
        print(f"Successfully generated combined file {output_path}")

    print(f"Total: {total_tables} tables across {len(results)} contexts")
