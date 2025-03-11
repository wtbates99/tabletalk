from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional


class LLMProvider(ABC):
    @abstractmethod
    def generate_sql(
        self,
        schema: str,
        question: str,
        focus_tables: Optional[List[str]] = None,
        max_tokens: int = 150,
        temperature: float = 0,
    ) -> str:
        """Generate SQL from natural language using the LLM"""
        pass


class DatabaseProvider(ABC):
    @abstractmethod
    def execute_query(self, sql_query: str) -> List[Dict[str, Any]]:
        """Execute SQL query and return results"""
        pass

    @abstractmethod
    def get_client(self):
        """Return the database client instance"""
        pass

    @abstractmethod
    def get_database_type_map(self) -> Dict[str, str]:
        """Return the database types"""
        pass
