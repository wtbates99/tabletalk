import sys
import os
from utils import apply_schema, initialize_project

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python biql.py <command> [args]")
        print("Commands: init, apply [project_folder]")
        sys.exit(1)

    command = sys.argv[1]

    if command == "init":
        initialize_project()
    elif command == "apply":
        # If only "apply" is provided, use the current working directory
        if len(sys.argv) == 2:
            project_folder = os.getcwd()
        # If a project folder is specified, use that
        elif len(sys.argv) == 3:
            project_folder = sys.argv[2]
        # If too many arguments are provided, show usage
        else:
            print("Usage: python biql.py apply [project_folder]")
            sys.exit(1)

        # Check if tabletext.yaml exists in the project folder
        config_path = os.path.join(project_folder, "tabletext.yaml")
        if not os.path.exists(config_path):
            print(f"Config file {config_path} not found.")
            sys.exit(1)

        # Proceed with applying the schema
        apply_schema(project_folder)
    else:
        print("Unknown command")
        sys.exit(1)
