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
