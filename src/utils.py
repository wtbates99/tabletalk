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
