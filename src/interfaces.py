import json
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import yaml


class DatabaseProvider(ABC):
    @abstractmethod
    def execute_query(self, sql_query: str) -> List[Dict[str, Any]]:
        """Execute SQL query and return results"""
        pass

    @abstractmethod
    def get_client(self) -> Any:
        """Return the database client instance"""
        pass

    @abstractmethod
    def get_database_type_map(self) -> Dict[str, str]:
        """Return the database types"""
        pass

    @abstractmethod
    def get_compact_tables(
        self, schema_name: str, table_names: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch table schemas in a compact format.

        Args:
            schema_name (str): The schema (or dataset) to query.
            table_names (Optional[List[str]]): Specific tables to fetch; if None, fetch all tables.

        Returns:
            List of dictionaries, each with:
            - 't': table name (e.g., 'schema.table')
            - 'd': table description (optional)
            - 'f': list of fields, each with 'n' (name) and 't' (type)
        """
        pass


class Parser:
    def __init__(self, project_folder: str, db_provider: DatabaseProvider):
        """
        Initialize the Parser with a project folder and database provider.

        Args:
            project_folder (str): Path to the project folder containing 'tabletext.yaml' and a 'contexts' subfolder.
            db_provider (DatabaseProvider): An instance of a database provider implementing the DatabaseProvider interface.
        """
        self.project_folder = project_folder
        self.db_provider = db_provider

    def apply_schema(self) -> None:
        """
        Process the project folder and generate JSON schemas for all contexts.

        This method:
        1. Reads 'tabletext.yaml' for configuration.
        2. Processes YAML files in the 'contexts' folder.
        3. Uses the DBProvider to fetch table schemas.
        4. Writes compact schemas to JSON files in the 'output' folder.

        Expected 'tabletext.yaml' structure:
            provider:
                type: "bigquery"  # e.g., "bigquery", "redshift"
                # provider-specific settings
            contexts: "contexts"  # folder with context YAML files
            output: "output"  # folder for generated JSON files

        Expected context YAML structure (e.g., 'my_context.yaml'):
            schemas:
                - name: "my_schema"
                  tables:
                    - "table1"
                    - "table2"
            version: "1.0"  # optional
        """
        # Load and validate tabletext.yaml
        config_path = os.path.join(self.project_folder, "tabletext.yaml")
        try:
            with open(config_path, "r") as file:
                defaults = yaml.safe_load(file)
            if not isinstance(defaults, dict):
                raise ValueError("'tabletext.yaml' must be a valid YAML dictionary.")
            required_keys = ["provider", "contexts", "output"]
            for key in required_keys:
                if key not in defaults:
                    raise ValueError(
                        f"'tabletext.yaml' is missing required key: '{key}'."
                    )
        except (FileNotFoundError, yaml.YAMLError, ValueError) as e:
            print(f"Error loading configuration: {str(e)}")
            return

        # Set up folder paths
        contexts_folder = os.path.join(self.project_folder, defaults["contexts"])
        output_folder = os.path.join(self.project_folder, defaults["output"])
        os.makedirs(output_folder, exist_ok=True)

        # Process each context file
        for context_file in os.listdir(contexts_folder):
            if not context_file.endswith(".yaml"):
                continue
            context_path = os.path.join(contexts_folder, context_file)
            try:
                with open(context_path, "r") as file:
                    context_config = yaml.safe_load(file)
                if (
                    not isinstance(context_config, dict)
                    or "schemas" not in context_config
                    and "datasets" not in context_config
                ):
                    print(
                        f"Warning: Invalid format in context file '{context_file}', skipping."
                    )
                    continue
            except (FileNotFoundError, yaml.YAMLError) as e:
                print(f"Error reading context file '{context_file}': {str(e)}")
                continue

            # Fetch compact schemas
            compact_tables = []
            # Accept either "schemas" or "datasets" key
            schema_list = context_config.get("schemas") or context_config.get(
                "datasets", []
            )
            for schema_item in schema_list:
                schema_name = schema_item.get("name")
                table_names = schema_item.get("tables", None)
                if not schema_name:
                    print(
                        f"Warning: Missing schema name in '{context_file}', skipping item."
                    )
                    continue
                try:
                    compact_tables.extend(
                        self.db_provider.get_compact_tables(schema_name, table_names)
                    )
                except Exception as e:
                    print(f"Error fetching tables for schema '{schema_name}': {str(e)}")
                    continue

            # Write output JSON
            context_data = {
                "tables": compact_tables,
                "version": context_config.get("version", "1.0"),
            }
            output_file = os.path.join(
                output_folder, context_file.replace(".yaml", ".json")
            )
            try:
                with open(output_file, "w") as file:
                    json.dump(context_data, file, indent=2)
                print(f"Successfully generated schema for '{context_file}'")
            except Exception as e:
                print(f"Error writing output file '{output_file}': {str(e)}")
