import os

import click

from tabletalk.app import app
from tabletalk.interfaces import QuerySession
from tabletalk.utils import apply_schema, initialize_project


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
    """Apply the schema from tabletalk.yaml in the specified project folder."""
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
    """Start an interactive session to query manifests with an LLM."""
    # Validate project folder
    if not os.path.isdir(project_folder):
        click.echo(f"Error: '{project_folder}' is not a valid directory.")
        return

    # Check manifest folder existence
    manifest_folder = os.path.join(project_folder, "manifest")
    if not os.path.exists(manifest_folder):
        click.echo(
            f"Manifest folder '{manifest_folder}' not found. Please run 'apply' first."
        )
        return

    # List available manifest files
    manifest_files = [f for f in os.listdir(manifest_folder) if f.endswith(".txt")]
    if not manifest_files:
        click.echo("No manifest files found in the manifest folder.")
        return

    # Helper function to select a manifest
    def select_manifest() -> str:
        click.echo("Available manifest files:")
        for i, manifest_file in enumerate(manifest_files, 1):
            click.echo(f"{i}. {manifest_file}")
        while True:
            selection = click.prompt("Select a manifest file by number", type=str)
            try:
                return manifest_files[int(selection) - 1]
            except (IndexError, ValueError):
                click.echo("Invalid selection. Please enter a valid number.")

    # Initialize QuerySession
    try:
        session = QuerySession(project_folder)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        click.echo(f"Error initializing session: {str(e)}")
        return

    # Select initial manifest
    current_manifest = select_manifest()
    try:
        manifest_data = session.load_manifest(current_manifest)
    except FileNotFoundError as e:
        click.echo(f"Error loading manifest: {str(e)}")
        return

    # Start interactive session
    click.echo(f"\nUsing manifest: {current_manifest}")
    click.echo(
        "Type your question, 'change' to select a new manifest, or 'exit' to quit."
    )

    while True:
        user_input = click.prompt(">", type=str).strip().lower()
        if user_input == "exit":
            click.echo("Exiting query session.")
            break
        elif user_input == "change":
            # Allow changing the manifest
            new_manifest = select_manifest()
            try:
                new_manifest_data = session.load_manifest(new_manifest)
                manifest_data = new_manifest_data
                current_manifest = new_manifest
                click.echo(f"Switched to manifest: {current_manifest}")
            except FileNotFoundError as e:
                click.echo(f"Error loading '{new_manifest}': {str(e)}")
                click.echo(f"Keeping current manifest: {current_manifest}")
        else:
            # Treat input as a question
            question = user_input
            try:
                sql = session.generate_sql(manifest_data, question)
                click.echo("\nGenerated SQL:")
                click.echo(sql)
                click.echo()
            except RuntimeError as e:
                click.echo(f"Error generating SQL: {str(e)}")


@cli.command()
@click.option("--port", default=5000, type=int, help="Port to run the server on.")
def serve(port: int) -> None:
    """Start the Flask web server."""
    app.run(debug=True, port=port)


if __name__ == "__main__":
    cli()
