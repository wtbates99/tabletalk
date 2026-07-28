# Contributing

tabletalk is a Python project managed with [uv](https://github.com/astral-sh/uv). Contributions are welcome — this guide covers environment setup, adding new providers, and the pull request process.

---

## Development setup

**Prerequisites:** Python 3.10+, [uv](https://github.com/astral-sh/uv)

```bash
git clone https://github.com/wtbates99/tabletalk.git
cd tabletalk

# Install the package and all dev dependencies in one step
uv sync --extra all
```

This installs tabletalk in editable mode along with all optional database drivers plus the dev tools (pytest, ruff, mypy, duckdb).

---

## Running tests

```bash
# All tests (DuckDB and SQLite run without extra setup)
uv run pytest

# Specific test file
uv run pytest tabletalk/tests/test_providers.py -v
```

Tests live in `tabletalk/tests/`. PostgreSQL and MySQL tests require a running local instance — configure credentials in the test config if you need them.

---

## Code style

```bash
# Lint and auto-fix
uv run ruff check --fix tabletalk/

# Type checking
uv run mypy tabletalk/
```

Line length is 100. The linter enforces `pyflakes`, `pycodestyle`, `isort`, and `pyupgrade` rules. Run both before pushing.

---

## Adding a database provider

All database providers implement the `DatabaseProvider` ABC in `tabletalk/interfaces.py`.

**1. Create the provider file** at `tabletalk/providers/mydb_provider.py`:

```python
from typing import Any, Dict, List, Optional
from tabletalk.interfaces import DatabaseProvider


class MyDBProvider(DatabaseProvider):
    def __init__(self, config: Dict[str, Any]):
        # initialize connection from config dict
        ...

    def execute_query(self, sql_query: str) -> List[Dict[str, Any]]:
        """Execute SQL and return a list of row dicts."""
        ...

    def get_client(self) -> Any:
        """Return the raw connection/client object."""
        ...

    def get_database_type_map(self) -> Dict[str, str]:
        """Map native type names to compact codes used in manifests.

        Standard codes: I=integer, S=string/text, N=numeric/float,
        B=boolean, TS=timestamp, D=date, J=json, X=other
        """
        return {
            "integer": "I",
            "text": "S",
            # ...
        }

    def get_compact_tables(
        self, schema_name: str, table_names: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Fetch table schemas in compact format for manifest generation.

        Returns a list of dicts:
          {
            't': 'schema.table_name',
            'd': 'table description or empty string',
            'f': [
              {'n': 'col_name', 't': 'I', 'pk': True},
              {'n': 'other_col', 't': 'S', 'fk': 'other_table.id'},
            ]
          }
        """
        ...
```

**2. Register the provider** in `tabletalk/factories.py` — add a branch to `get_db_provider()` that maps your type string to your class.

**3. Add the optional dependency** in `pyproject.toml` under `[project.optional-dependencies]`:

```toml
mydb = ["mydb-driver>=1.0"]
```

And add it to the `all` extras list.

**4. Add a test** in `tabletalk/tests/` following the pattern of the existing provider tests.

---

## Adding an LLM provider

LLM providers implement the `LLMProvider` ABC in `tabletalk/interfaces.py`.

**1. Create the provider file** at `tabletalk/providers/myllm_provider.py`:

```python
from typing import Dict, Generator, List
from tabletalk.interfaces import LLMProvider


class MyLLMProvider(LLMProvider):
    def __init__(self, config: Dict):
        # initialize client from config dict
        ...

    def generate_response(self, prompt: str) -> str:
        """Single-turn: return the full response as a string."""
        ...

    def generate_response_stream(self, prompt: str) -> Generator[str, None, None]:
        """Single-turn: yield response tokens as they arrive."""
        ...

    def generate_chat_stream(
        self, messages: List[Dict[str, str]]
    ) -> Generator[str, None, None]:
        """Multi-turn: yield tokens given a full messages list (OpenAI format).

        messages is a list of {'role': 'system'|'user'|'assistant', 'content': '...'}.
        """
        ...
```

**2. Register the provider** in `tabletalk/factories.py` in `get_llm_provider()`.

---

## Project structure

```
tabletalk/
├── cli.py          — Click CLI (all user-facing commands)
├── app.py          — Flask web server + SSE streaming endpoints
├── interfaces.py   — DatabaseProvider, LLMProvider, QuerySession, Parser ABCs
├── factories.py    — Provider instantiation from config dicts
├── profiles.py     — ~/.tabletalk/profiles.yml management + dbt import
├── utils.py        — Project scaffolding helpers
├── providers/      — One file per database or LLM backend
└── tests/          — pytest test suite
```

The `QuerySession` class in `interfaces.py` is the core orchestrator — it owns SQL generation, execution, history, favorites, and explain/suggest logic. `Parser` handles `tabletalk apply` (schema introspection → manifest files).

---

## Pull requests

- Open an issue first for non-trivial changes
- Keep PRs focused — one feature or fix per PR
- Ensure `ruff` and `mypy` pass with no new errors
- Add or update tests for changed behavior
- Update the relevant docs page if your change affects user-visible behavior
