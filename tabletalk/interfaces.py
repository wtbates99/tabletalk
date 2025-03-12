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
            schema_name (str): The name of the schema/dataset.
            table_names (Optional[List[str]]): List of table names to fetch; if None, fetch all.

        Returns:
            List[Dict[str, Any]]: A list of dictionaries, each containing:
                - 't': str - Full table name (e.g., 'schema.table')
                - 'd': str - Table description (may be empty)
                - 'f': List[Dict] - List of field dictionaries with:
                    - 'n': str - Field name
                    - 't': str - Field type
        """
        pass


class LLMProvider(ABC):
    @abstractmethod
    def generate_response(self, prompt: str) -> str:
        """Generate a response from the LLM based on the given prompt."""
        pass


class Parser:
    def __init__(self, project_folder: str, db_provider: DatabaseProvider):
        """
        Initialize the Parser with a project folder and database provider.

        Args:
            project_folder (str): Path to the project directory containing configuration files.
            db_provider (DatabaseProvider): Instance of a DatabaseProvider implementation.
        """
        self.project_folder = project_folder
        self.db_provider = db_provider

    def apply_schema(self) -> None:
        """
        Process the project folder and generate compact text schemas for all contexts.
        Reads 'tabletalk.yaml' and context YAML files, fetches table schemas via DatabaseProvider,
        and writes output in a custom text format to the 'output' folder.
        """
        config_path = os.path.join(self.project_folder, "tabletalk.yaml")
        try:
            with open(config_path, "r") as file:
                defaults = yaml.safe_load(file)
            if not isinstance(defaults, dict):
                raise ValueError("Invalid 'tabletalk.yaml' format.")
            required_keys = ["provider", "contexts", "output"]
            for key in required_keys:
                if key not in defaults:
                    raise ValueError(f"Missing key '{key}' in 'tabletalk.yaml'.")
            data_source_desc = defaults.get("description", "")
            provider_type = defaults["provider"].get("type", "unknown")
            data_source_line = f"DATA_SOURCE: {provider_type} - {data_source_desc}"
        except Exception as e:
            print(f"Error loading configuration: {str(e)}")
            return

        contexts_folder = os.path.join(self.project_folder, defaults["contexts"])
        output_folder = os.path.join(self.project_folder, defaults["output"])
        os.makedirs(output_folder, exist_ok=True)

        for context_file in os.listdir(contexts_folder):
            if not context_file.endswith(".yaml"):
                continue
            context_path = os.path.join(contexts_folder, context_file)
            try:
                with open(context_path, "r") as file:
                    context_config = yaml.safe_load(file)
                if not isinstance(context_config, dict):
                    print(f"Warning: Invalid format in '{context_file}', skipping.")
                    continue
            except Exception as e:
                print(f"Error reading '{context_file}': {str(e)}")
                continue

            context_name = context_config.get("name", "unnamed_context")
            context_desc = context_config.get("description", "")
            version = context_config.get("version", "1.0")
            context_line = f"CONTEXT: {context_name} - {context_desc} (v{version})"

            output_lines = [data_source_line, context_line]

            schema_list = context_config.get("datasets") or context_config.get(
                "schemas", []
            )
            for schema_item in schema_list:
                schema_name = schema_item.get("name")
                schema_desc = schema_item.get("description", "")
                if not schema_name:
                    print(
                        f"Warning: Missing schema name in '{context_file}', skipping."
                    )
                    continue
                output_lines.append(f"DATASET: {schema_name} - {schema_desc}")
                output_lines.append("TABLES:")

                tables = schema_item.get("tables", [])
                yaml_table_desc: Dict[str, Optional[str]] = {}
                table_names: List[str] = []
                for table in tables:
                    if isinstance(table, str):
                        table_name = f"{schema_name}.{table}"
                        yaml_table_desc[table_name] = None
                        table_names.append(table)
                    elif isinstance(table, dict):
                        table_name = f"{schema_name}.{table['name']}"
                        yaml_table_desc[table_name] = table.get("description", "")
                        table_names.append(table["name"])
                    else:
                        print(f"Warning: Invalid table entry in '{schema_name}'.")
                        continue

                try:
                    compact_tables = self.db_provider.get_compact_tables(
                        schema_name, table_names
                    )
                    for compact_table in compact_tables:
                        table_name = compact_table["t"]
                        yaml_desc = yaml_table_desc.get(table_name)
                        desc = (
                            yaml_desc
                            if yaml_desc is not None
                            else compact_table.get("d", "")
                        )
                        fields = "|".join(
                            [f"{f['n']}:{f['t']}" for f in compact_table["f"]]
                        )
                        table_line = f"{table_name}|{desc}|{fields}"
                        output_lines.append(table_line)
                except Exception as e:
                    print(f"Error fetching tables for '{schema_name}': {str(e)}")
                    continue

            output_file = os.path.join(
                output_folder, context_file.replace(".yaml", ".txt")
            )
            try:
                with open(output_file, "w") as file:
                    file.write("\n".join(output_lines))
                print(f"Successfully generated schema for '{context_file}'")
            except Exception as e:
                print(f"Error writing '{output_file}': {str(e)}")
