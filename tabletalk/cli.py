import json
import os

import click
import yaml

from tabletalk.factories import get_llm_provider
from tabletalk.utils import apply_schema, format_schema, initialize_project


@click.group()
def cli() -> None:
    """tabletalk CLI tool.

    This tool helps you manage and query your database schemas using natural language.
    """
    pass


@cli.command()
def init() -> None:
    """Initialize a new project in the current directory."""
    initialize_project()


@cli.command()
@click.argument("project_folder", default=os.getcwd())
def apply(project_folder: str) -> None:
    """Apply the schema from tabletalk.yaml in the specified project folder.

    If project_folder is not provided, the current working directory is used.
    """
    if not os.path.isdir(project_folder):
        click.echo(f"Error: '{project_folder}' is not a valid directory.")
        return
    config_path = os.path.join(project_folder, "tabletalk.yaml")
    if not os.path.exists(config_path):
        click.echo(f"Config file '{config_path}' not found.")
        return
    apply_schema(project_folder)
    click.echo("Schema applied successfully.")


@cli.command()
@click.argument("project_folder", default=os.getcwd())
def query(project_folder: str) -> None:
    """Start an interactive session to query manifests with an LLM.

    If project_folder is not provided, the current working directory is used.
    """
    if not os.path.isdir(project_folder):
        click.echo(f"Error: '{project_folder}' is not a valid directory.")
        return
    config_path = os.path.join(project_folder, "tabletalk.yaml")
    if not os.path.exists(config_path):
        click.echo(f"Config file '{config_path}' not found.")
        return
    with open(config_path, "r") as file:
        config = yaml.safe_load(file)
    llm_config = config.get("llm", {})
    if not llm_config or "provider" not in llm_config or "api_key" not in llm_config:
        click.echo("LLM configuration missing or incomplete in 'tabletalk.yaml'.")
        return
    try:
        llm_provider = get_llm_provider(llm_config)
    except Exception as e:
        click.echo(f"Failed to initialize LLM provider: {str(e)}")
        return
    manifest_folder = os.path.join(project_folder, "manifest")
    if not os.path.exists(manifest_folder):
        click.echo(
            f"Manifest folder '{manifest_folder}' not found. Please run 'apply' first."
        )
        return
    manifest_files = [f for f in os.listdir(manifest_folder) if f.endswith(".json")]
    if not manifest_files:
        click.echo("No manifest files found in the manifest folder.")
        return
    click.echo("Available manifest files:")
    for i, manifest_file in enumerate(manifest_files, 1):
        click.echo(f"{i}. {manifest_file}")
    while True:
        selection = click.prompt("Select a manifest file by number", type=str)
        try:
            selected_file = manifest_files[int(selection) - 1]
            break
        except (IndexError, ValueError):
            click.echo("Invalid selection. Please enter a valid number.")
    manifest_path = os.path.join(manifest_folder, selected_file)
    with open(manifest_path, "r") as file_handle:
        manifest_data = json.load(file_handle)
    click.echo(f"\nUsing manifest: {selected_file}")
    click.echo("Type your question below. Enter 'exit' to quit.")
    schema_str = format_schema(manifest_data)
    while True:
        question = click.prompt("Ask a question", type=str)
        if question.lower() == "exit":
            click.echo("Exiting query session.")
            break
        prompt = (
            "Given the following database schema:\n\n"
            f"{schema_str}\n\n"
            "Generate an SQL query to answer the following question:\n\n"
            f"{question}"
        )
        try:
            response = llm_provider.generate_response(prompt)
            click.echo("\nGenerated SQL:")
            click.echo(response)
            click.echo()
        except Exception as e:
            click.echo(f"Error generating SQL: {str(e)}")
