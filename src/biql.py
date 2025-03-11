import sys
from utils import apply_schema

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python biql.py apply [path/to/biql.yaml]")
        sys.exit(1)
    command = sys.argv[1]
    if command == "apply":
        config_path = sys.argv[2] if len(sys.argv) > 2 else "biql.yaml"
        apply_schema(config_path)
    else:
        print("Unknown command")
        sys.exit(1)
