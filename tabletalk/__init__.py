"""
tabletalk — dbt for agents.

Define your data sources once. Deploy an AI agent for every dataset.
Redeploy anytime your schema changes — like Terraform for analytics agents.

Quick start::

    from tabletalk.interfaces import QuerySession
    from tabletalk.utils import apply_schema

    apply_schema("/path/to/project")
    session = QuerySession("/path/to/project")
    for chunk in session.generate_sql("sales.txt", "What is total revenue this month?"):
        print(chunk, end="", flush=True)
"""

from tabletalk.interfaces import DatabaseProvider, LLMProvider, Parser, QuerySession

__version__ = "0.2.0"
__all__ = [
    "QuerySession",
    "Parser",
    "DatabaseProvider",
    "LLMProvider",
    "__version__",
]
