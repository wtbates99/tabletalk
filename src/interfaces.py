from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


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

    @abstractmethod
    def get_response(self, prompt: str) -> str:
        """Get a response from the LLM for a given prompt"""
        pass


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
