import json
import os
import sys
from typing import Any  # Added for type hinting

import yaml
from factories import get_llm_provider
from utils import apply_schema, initialize_project


def print_usage() -> None:
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
    print(
        "  query [project_folder] Start an interactive session to query manifests with an LLM."
    )
    print(
        "                      If project_folder is not provided, the current working directory is used."
    )
    print("  help                Display this help message.")


def format_schema(manifest_data: dict[str, Any]) -> str:  # Changed 'any' to 'Any'
    """Format the manifest schema into a string for the LLM prompt."""
    schema_str = ""
    for table in manifest_data["tables"]:
        schema_str += f"Table: {table['t']}\n"
        schema_str += f"Description: {table['d']}\n"
        schema_str += "Fields:\n"
        for field in table["f"]:
            schema_str += f"  - {field['n']} ({field['t']})\n"
        schema_str += "\n"
    return schema_str.strip()


def query_project(project_folder: str) -> None:
    """Start an interactive session to query manifests with an LLM."""
    # Load the configuration
    config_path = os.path.join(project_folder, "tabletext.yaml")
    if not os.path.exists(config_path):
        print(f"Config file '{config_path}' not found. Please run 'init' first.")
        sys.exit(1)
    with open(config_path, "r") as file:
        config = yaml.safe_load(file)
    llm_config = config.get("llm", {})
    if not llm_config or "provider" not in llm_config or "api_key" not in llm_config:
        print("LLM configuration missing or incomplete in 'tabletext.yaml'.")
        sys.exit(1)

    # Initialize LLM provider
    try:
        llm_provider = get_llm_provider(llm_config)
    except Exception as e:
        print(f"Failed to initialize LLM provider: {str(e)}")
        sys.exit(1)

    # List available manifest files
    manifest_folder = os.path.join(project_folder, "manifest")
    if not os.path.exists(manifest_folder):
        print(
            f"Manifest folder '{manifest_folder}' not found. Please run 'apply' first."
        )
        sys.exit(1)
    manifest_files = [f for f in os.listdir(manifest_folder) if f.endswith(".json")]
    if not manifest_files:
        print("No manifest files found in the manifest folder.")
        sys.exit(1)

    print("Available manifest files:")
    for i, manifest_file in enumerate(manifest_files, 1):
        print(f"{i}. {manifest_file}")
    while True:
        selection = input("Select a manifest file by number: ")
        try:
            selected_file = manifest_files[int(selection) - 1]
            break
        except (IndexError, ValueError):
            print("Invalid selection. Please enter a valid number.")

    # Load the selected manifest
    manifest_path = os.path.join(manifest_folder, selected_file)
    with open(manifest_path, "r") as file_handle:
        manifest_data = json.load(file_handle)

    # Start interactive query loop
    print(f"\nUsing manifest: {selected_file}")
    print("Type your question below. Enter 'exit' to quit.")
    schema_str = format_schema(manifest_data)
    while True:
        question = input("Ask a question: ")
        if question.lower() == "exit":
            print("Exiting query session.")
            break
        prompt = (
            "Given the following database schema:\n\n"
            f"{schema_str}\n\n"
            "Generate an SQL query to answer the following question:\n\n"
            f"{question}"
        )
        try:
            response = llm_provider.generate_response(prompt)
            print("\nGenerated SQL:")
            print(response)
            print()
        except Exception as e:
            print(f"Error generating SQL: {str(e)}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) == 0:
        print_usage()
        sys.exit(1)

    command = args[0]

    if command == "init":
        if len(args) > 1:
            print("Error: 'init' command takes no additional arguments.")
            print_usage()
            sys.exit(1)
        initialize_project()

    elif command == "apply":
        project_folder = args[1] if len(args) == 2 else os.getcwd()
        if not os.path.isdir(project_folder):
            print(f"Error: '{project_folder}' is not a valid directory.")
            sys.exit(1)
        config_path = os.path.join(project_folder, "tabletext.yaml")
        if not os.path.exists(config_path):
            print(f"Config file '{config_path}' not found.")
            sys.exit(1)
        apply_schema(project_folder)
        print("Schema applied successfully.")

    elif command == "query":
        project_folder = args[1] if len(args) == 2 else os.getcwd()
        if not os.path.isdir(project_folder):
            print(f"Error: '{project_folder}' is not a valid directory.")
            sys.exit(1)
        config_path = os.path.join(project_folder, "tabletext.yaml")
        if not os.path.exists(config_path):
            print(f"Config file '{config_path}' not found.")
            sys.exit(1)
        query_project(project_folder)

    elif command == "help":
        print_usage()
        sys.exit(0)

    else:
        print(f"Unknown command: '{command}'")
        print_usage()
        sys.exit(1)
