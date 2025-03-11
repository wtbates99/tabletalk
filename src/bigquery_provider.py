from interfaces import DatabaseProvider
from typing import List, Dict, Any, Optional
from google.cloud import bigquery
from google.oauth2 import service_account


class BigQueryProvider(DatabaseProvider):
    def __init__(self, project_id: str, credentials_path: Optional[str] = None):
        if credentials_path:
            credentials = service_account.Credentials.from_service_account_file(
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

    def get_client(self):
        """Return the BigQuery client instance"""
        return self.client
