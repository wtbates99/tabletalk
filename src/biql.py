import sys
import yaml
import json
from utils import generate_compact_tables
from factories import get_db_provider


def apply(config_path):
    with open(config_path, "r") as file:
        config = yaml.safe_load(file)
    provider = config["provider"]
    llm = config.get("llm", {})
    datasets = config["datasets"]
    db_provider = get_db_provider(provider)
    client = db_provider.get_client()
    compact_tables = generate_compact_tables(client, datasets)
    context = {"provider": provider, "llm": llm, "compact_tables": compact_tables}
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
