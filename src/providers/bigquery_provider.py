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
            # Use default credentials
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
            "INTEGER": "I",
            "TIMESTAMP": "TS",
            "BOOLEAN": "B",
            "NUMERIC": "N",
            "ARRAY": "A",
            "STRUCT": "ST",
            "BYTES": "BY",
            "GEOGRAPHY": "G",
        }
