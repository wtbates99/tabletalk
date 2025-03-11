from typing import Any, Dict, List, Optional

from google.cloud import bigquery
from google.oauth2 import service_account
from interfaces import DatabaseProvider


class BigQueryProvider(DatabaseProvider):
    def __init__(self, project_id: str, credentials_path: Optional[str] = None):
        if credentials_path:
            credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
                credentials_path
            )
            self.client = bigquery.Client(project=project_id, credentials=credentials)
        else:
            self.client = bigquery.Client(project=project_id)

    def execute_query(self, sql_query: str) -> List[Dict[str, Any]]:
        query_job = self.client.query(sql_query)
        results = query_job.result()
        return [dict(row) for row in results]

    def get_client(self) -> bigquery.Client:
        """Return the BigQuery client instance"""
        return self.client

    def get_database_type_map(self) -> Dict[str, str]:
        """Return the database types"""
        return {
            "STRING": "S",
            "FLOAT": "F",
            "DATE": "D",
            "DATETIME": "DT",
            "INTEGER": "I",
            "TIMESTAMP": "TS",
            "BOOLEAN": "B",
            "NUMERIC": "N",
            "ARRAY": "A",
            "STRUCT": "ST",
            "BYTES": "BY",
            "GEOGRAPHY": "G",
            "BOOL": "B",
            "INT64": "I",
            "INT": "I",
            "SMALLINT": "I",
            "FLOAT64": "F",
            "DECIMAL": "N",
            "BIGNUMERIC": "BN",
            "BIGDECIMAL": "BN",
            "TIME": "T",
            "INTERVAL": "IV",
            "JSON": "J",
            "RANGE": "R",
        }

    def get_compact_tables(
        self, schema_name: str, table_names: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch table schemas from a BigQuery dataset in a compact format.

        Args:
            schema_name (str): The dataset name in BigQuery.
            table_names (Optional[List[str]]): Specific table names; if None, fetch all tables.

        Returns:
            List of table schemas in compact format.
        """
        client = self.get_client()
        type_map = self.get_database_type_map()

        if table_names:
            tables = [
                client.get_table(f"{schema_name}.{table_id}")
                for table_id in table_names
            ]
        else:
            dataset_ref = client.dataset(schema_name)
            tables = [
                client.get_table(table_ref)
                for table_ref in client.list_tables(dataset_ref)
            ]

        compact_tables = []
        for table in tables:
            fields = [
                {"n": field.name, "t": type_map.get(field.field_type, field.field_type)}
                for field in table.schema
            ]
            compact_tables.append(
                {
                    "t": f"{table.dataset_id}.{table.table_id}",
                    "d": table.description or "",
                    "f": fields,
                }
            )
        return compact_tables
