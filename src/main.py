import sys
import os
from utils import apply_schema, initialize_project


def print_usage():
    """Display how to use the script with descriptions of available commands."""
    print("Usage: python main.py <command> [args]")
    print("Commands:")
    print("  init                Initialize a new project in the current directory.")
    print(
        "  apply [project_folder] Apply the schema from tabletext.yaml in the specified project folder."
    )
    print(
        "                      If project_folder is not provided, the current working directory is used."
    )
    print("  help                Display this help message.")


if __name__ == "__main__":
    # Get command-line arguments excluding the script name
    args = sys.argv[1:]

    # If no command is provided, show usage and exit with 1
    if len(args) == 0:
        print_usage()
        sys.exit(1)

    command = args[0]

    if command == "init":
        # Ensure 'init' has no extra arguments
        if len(args) > 1:
            print("Error: 'init' command takes no additional arguments.")
            print_usage()
            sys.exit(1)
        initialize_project()

    elif command == "apply":
        # Determine project_folder based on number of arguments
        if len(args) == 1:
            project_folder = os.getcwd()
        elif len(args) == 2:
            project_folder = args[1]
        else:
            print(
                "Error: 'apply' command takes at most one additional argument (project_folder)."
            )
            print_usage()
            sys.exit(1)

        # Verify that project_folder is a valid directory
        if not os.path.isdir(project_folder):
            print(f"Error: '{project_folder}' is not a valid directory.")
            sys.exit(1)

        # Check for the config file
        config_path = os.path.join(project_folder, "tabletext.yaml")
        if not os.path.exists(config_path):
            print(f"Config file '{config_path}' not found.")
            print(
                "Please ensure the project is initialized with 'init' or verify the project folder path."
            )
            sys.exit(1)

        # Apply the schema if all checks pass
        apply_schema(project_folder)
        print("Schema applied successfully.")

    elif command == "help":
        print_usage()
        sys.exit(0)

    else:
        # Handle unknown commands
        print(f"Unknown command: '{command}'")
        print_usage()
        sys.exit(1)
