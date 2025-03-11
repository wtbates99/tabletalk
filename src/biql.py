import sys
from utils import apply_schema, initialize_project

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python biql.py <command> [args]")
        print("Commands: init, apply <project_folder>")
        sys.exit(1)
    command = sys.argv[1]
    if command == "init":
        initialize_project()
    elif command == "apply":
        if len(sys.argv) < 3:
            print(
                "Please specify the project folder (e.g., python biql.py apply batesql)"
            )
            sys.exit(1)
        project_folder = sys.argv[2]
        apply_schema(project_folder)
    else:
        print("Unknown command")
        sys.exit(1)
