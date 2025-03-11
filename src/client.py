import json
from typing import List, Dict, Any, Union
from interfaces import LLMProvider, DatabaseProvider
from factories import get_llm_provider, get_db_provider


class BIQLClient:
    def __init__(self, context_path: str):
        with open(context_path, "r") as file:
            self.context = json.load(file)
        self.compact_tables = self.context["compact_tables"]
        self.focus_tables = None
        self.llm_provider: LLMProvider = get_llm_provider(self.context["llm"])
        self.db_provider: DatabaseProvider = get_db_provider(self.context["provider"])

    def set_focus(self, tables: Union[str, List[str]]):
        """Set focus on specific tables to prioritize in queries"""
        if isinstance(tables, str):
            self.focus_tables = [tables]
        else:
            self.focus_tables = tables

    def ask(self, question: str) -> List[Dict[str, Any]]:
        """
        Process a natural language question, convert to SQL, and return results
        """
        tables_to_include = self.compact_tables
        if self.focus_tables:
            tables_to_include = [
                t for t in self.compact_tables if t["t"] in self.focus_tables
            ]
            if not tables_to_include:
                tables_to_include = self.compact_tables
        compact_schema = json.dumps(tables_to_include, separators=(",", ":"))
        sql_query = self.llm_provider.generate_sql(
            schema=compact_schema,
            question=question,
            focus_tables=self.focus_tables,
            max_tokens=self.context["llm"].get("max_tokens", 150),
            temperature=self.context["llm"].get("temperature", 0),
        )
        return self.db_provider.execute_query(sql_query)


def load_context(context_path: str) -> BIQLClient:
    """Load the BIQL context and return a client instance"""
    return BIQLClient(context_path)
