"""
BigQuery provider tests.

These tests are skipped unless google-cloud-bigquery is installed:
    uv add "tabletalk[bigquery]"

They also require valid GCP credentials:
    GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
    BIGQUERY_PROJECT_ID=my-project

Or simply run with Application Default Credentials:
    gcloud auth application-default login
"""

import os

import pytest

bigquery = pytest.importorskip(
    "google.cloud.bigquery",
    reason="google-cloud-bigquery not installed — pip install tabletalk[bigquery]",
)


@pytest.fixture(scope="module")
def bq_provider():
    """Live BigQuery provider — skipped if env vars are not set."""
    project_id = os.environ.get("BIGQUERY_PROJECT_ID")
    if not project_id:
        pytest.skip("BIGQUERY_PROJECT_ID env var not set")

    from tabletalk.providers.bigquery_provider import BigQueryProvider

    return BigQueryProvider(project_id=project_id)


class TestBigQueryProvider:
    def test_client_is_bigquery_client(self, bq_provider):
        client = bq_provider.get_client()
        assert client is not None

    def test_execute_simple_query(self, bq_provider):
        results = bq_provider.execute_query("SELECT 1 AS n")
        assert len(results) == 1
        assert results[0]["n"] == 1

    def test_type_map_completeness(self, bq_provider):
        type_map = bq_provider.get_database_type_map()
        for key in ("STRING", "INTEGER", "INT64", "FLOAT64", "BOOL", "DATE", "TIMESTAMP"):
            assert key in type_map, f"Type map missing key: {key}"

    def test_compact_tables_returns_list(self, bq_provider):
        dataset = os.environ.get("BIGQUERY_DATASET")
        if not dataset:
            pytest.skip("BIGQUERY_DATASET env var not set")
        tables = bq_provider.get_compact_tables(dataset)
        assert isinstance(tables, list)
        for t in tables:
            assert "t" in t
            assert "f" in t
