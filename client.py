import json
from google.cloud import bigquery
import openai  # Assuming OpenAI as the LLM provider


class BIQLClient:
    def __init__(self, context_path):
        # Load the context
        with open(context_path, "r") as file:
            self.context = json.load(file)

        self.provider = self.context["provider"]
        self.llm = self.context["llm"]
        self.tables = self.context["tables"]
        self.focus_tables = None

        # Initialize BigQuery client
        if self.provider.get("use_default_credentials", False):
            # Use default credentials
            self.bq_client = bigquery.Client(project=self.provider["project_id"])
        else:
            # Use service account credentials file
            self.bq_client = bigquery.Client.from_service_account_json(
                self.provider["credentials"], project=self.provider["project_id"]
            )

        # Initialize LLM client (OpenAI assumed)
        if self.llm["provider"] == "openai":
            openai.api_key = self.llm["api_key"]
        else:
            raise ValueError("Unsupported LLM provider")

    def set_focus(self, tables):
        """Set focus on specific tables to prioritize in queries"""
        if isinstance(tables, str):
            self.focus_tables = [tables]
        else:
            self.focus_tables = tables

    def ask(self, question):
        """
        Process a natural language question, convert to SQL, and return results
        """
        # Create a compact schema representation to minimize tokens
        type_map = {
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

        # Filter tables by focus if specified
        tables_to_include = self.tables
        if self.focus_tables:
            tables_to_include = [
                t for t in self.tables if t["name"] in self.focus_tables
            ]
            if not tables_to_include:
                tables_to_include = self.tables  # Fallback if no focus tables match

        # Create compact representation of tables
        compact_tables = []
        for table in tables_to_include:
            fields = [
                {"n": field["name"], "t": type_map.get(field["type"], field["type"])}
                for field in table["fields"]
            ]
            compact_tables.append({"t": table["name"], "f": fields})
        compact_schema = json.dumps(compact_tables, separators=(",", ":"))

        # Build the prompt with focus information if available
        focus_str = (
            f"Focus tables: {', '.join(self.focus_tables)}" if self.focus_tables else ""
        )

        # Get SQL query from LLM
        if self.llm["provider"] == "openai":
            # Use the latest OpenAI client API
            client = openai.OpenAI(api_key=self.llm["api_key"])
            response = client.chat.completions.create(
                model=self.llm.get("model", "gpt-3.5-turbo"),
                messages=[
                    {
                        "role": "system",
                        "content": "You are a BigQuery SQL expert. Your goal is to generate a valid BigQuery SQL query that answers the question.",
                    },
                    {
                        "role": "user",
                        "content": f"""
                        The schema below is in compact JSON:
                        - "t": table name
                        - "f": fields, with "n" (name) and "t" (type: S=STRING, F=FLOAT, D=DATE, I=INTEGER, TS=TIMESTAMP, B=BOOLEAN)

                        Schema: {compact_schema}

                        {focus_str}

                        Question: "{question}"

                        Generate a valid BigQuery SQL query.
                    """,
                    },
                ],
                max_tokens=self.llm.get("max_tokens", 150),
                temperature=self.llm.get("temperature", 0),
            )
            sql_query = response.choices[0].message.content.strip()
        else:
            raise ValueError("Unsupported LLM provider")

        # Execute the query
        query_job = self.bq_client.query(sql_query)
        results = query_job.result()

        # Return results as list of dictionaries
        return [dict(row) for row in results]


def load_context(context_path):
    """Load the BIQL context and return a client instance"""
    return BIQLClient(context_path)
