# Architecture

This document describes tabletalk's internal structure for contributors and integrators.

---

## Package layout

```
tabletalk/
├── cli.py              # Click CLI — all user-facing commands
├── app.py              # Flask web server — REST API + SSE streaming
├── interfaces.py       # Core abstractions — QuerySession, Parser, abstract base classes
├── utils.py            # Project scaffolding and apply helpers
├── factories.py        # Provider factories — instantiate LLM and DB providers from config
├── profiles.py         # ~/.tabletalk/profiles.yml read/write, dbt import
├── static/
│   └── index.html      # Single-page web UI (vanilla JS, no build step)
└── providers/
    ├── sqlite_provider.py
    ├── duckdb_provider.py
    ├── postgres_provider.py
    ├── mysql_provider.py
    ├── snowflake_provider.py
    ├── bigquery_provider.py
    ├── azuresql_provider.py
    ├── openai_provider.py
    └── anthropic_provider.py
```

---

## Core abstractions (`interfaces.py`)

### `DatabaseProvider` (abstract)

All database backends implement this interface:

```python
class DatabaseProvider(ABC):
    @abstractmethod
    def execute_query(self, sql: str) -> List[Dict[str, Any]]:
        """Execute SQL, return list of dicts (column → value)."""

    @abstractmethod
    def get_client(self):
        """Return the underlying database connection/client."""

    @abstractmethod
    def get_database_type_map(self) -> Dict[str, str]:
        """Map native type names to compact codes (e.g. 'varchar' → 'S')."""

    @abstractmethod
    def get_compact_tables(
        self,
        schema_name: str,
        table_names: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Introspect the database. Returns a list of table dicts:
        [
            {
                "t": "orders",
                "d": "Customer orders",
                "f": [
                    {"n": "id", "t": "I", "pk": True},
                    {"n": "customer_id", "t": "I", "fk": "customers.id"},
                    ...
                ]
            }
        ]
        """
```

### `LLMProvider` (abstract)

All LLM backends implement this interface:

```python
class LLMProvider(ABC):
    @abstractmethod
    def generate_response(self, prompt: str) -> str:
        """Single-turn text completion."""

    @abstractmethod
    def generate_response_stream(self, prompt: str) -> Iterator[str]:
        """Single-turn streaming completion."""

    @abstractmethod
    def generate_chat_stream(
        self,
        messages: List[Dict[str, str]],
    ) -> Iterator[str]:
        """
        Multi-turn streaming completion.
        messages = [{"role": "system"|"user"|"assistant", "content": "..."}]
        Yields text chunks as they arrive.
        """
```

### `QuerySession`

The central orchestration class. One instance per project directory. In the web server, it is a lazily-initialised module-level singleton.

**Responsibilities:**
- Load and cache `tabletalk.yaml`
- Initialise LLM and DB providers via `factories.py`
- Manage multi-turn conversation history
- Generate SQL (single-turn and conversational)
- Execute SQL with safe mode enforcement
- Stream explanations and suggestions
- Persist history and favorites to disk

**State:**
- `self.config` — parsed `tabletalk.yaml`
- `self.llm_provider` — LLM instance
- `self._db_provider` — DB instance (lazily initialised)
- Manifest cache (dict of filename → manifest text)

### `Parser`

Compiles context definitions into manifest files.

**`Parser.apply_schema()`** flow:
1. Read `tabletalk.yaml` — find `contexts` and `output` directories
2. For each `contexts/*.yaml`:
   a. Parse the YAML
   b. For each dataset/table, call `db_provider.get_compact_tables()`
   c. Merge YAML descriptions with introspected schema
   d. Format in compact notation
   e. Write to `manifest/<name>.txt`

---

## Compact schema notation

Manifests use a compact single-line format per table:

```
schema.table_name|Table description|col1:TYPE[CONSTRAINTS]|col2:TYPE|...
```

Type codes: `I` Integer, `S` String, `F` Float, `N` Numeric, `D` Date, `DT` DateTime, `TS` Timestamp, `T` Time, `B` Boolean, `BY` Binary, `J` JSON, `U` UUID, `A` Array, `IV` Interval, `G` Geography

Constraints: `[PK]` primary key, `[FK:table.column]` foreign key

Example:
```
public.orders|Customer orders|id:I[PK]|customer_id:I[FK:customers.id]|status:S|total_amount:N|created_at:TS
```

A full manifest:
```
DATA_SOURCE: postgres - Production analytics database
CONTEXT: sales - Order processing and revenue (v1.0)
DATASET: public - Main schema
TABLES:
public.orders|Customer orders|id:I[PK]|customer_id:I[FK:customers.id]|status:S|total_amount:N
public.products|Product catalogue|id:I[PK]|name:S|price:N|cost:N|category_id:I[FK:categories.id]
```

---

## LLM prompts

All prompts are defined as module-level constants in `interfaces.py`:

### `_SYSTEM_PROMPT`

Instructs the LLM to generate valid SQL given the manifest schema. Key directives:
- Output only SQL, no explanation or markdown fences
- Use FK relationships for JOINs
- Apply table descriptions when interpreting ambiguous questions
- Multi-turn: use conversation history for follow-up questions

### `_EXPLAIN_PROMPT`

Instructs the LLM to explain query results in plain English (2–3 sentences, no SQL).

### `_SUGGEST_PROMPT`

Instructs the LLM to output exactly 3 follow-up questions as a JSON array.

### `_FIX_PROMPT`

Instructs the LLM to correct a failing SQL query given the error message, outputting only the corrected SQL.

---

## Web server (`app.py`)

Flask application with a module-level `QuerySession` singleton:

```python
_session: Optional[QuerySession] = None

def _get_session() -> QuerySession:
    global _session
    if _session is None:
        _session = QuerySession(PROJECT_FOLDER)
    return _session
```

`PROJECT_FOLDER` is resolved when the Flask app is created (from CLI args or current directory).

### SSE streaming

`/chat/stream` and `/fix/stream` use Flask's `Response` with a generator function:

```python
def generate():
    for chunk in qs.generate_sql_conversational(question, manifest_text, history):
        yield f"data: {json.dumps({'type': 'sql_chunk', 'content': chunk})}\n\n"
    yield f"data: {json.dumps({'type': 'sql_done', 'sql': full_sql})}\n\n"
    ...

return Response(generate(), mimetype="text/event-stream")
```

Conversation history is stored in the Flask session (server-side, signed cookie). Maximum 20 messages to bound prompt size.

---

## Provider factories (`factories.py`)

`get_llm_provider(config)` and `get_db_provider(config)` map config dicts to provider instances. Both functions:
1. Call `_resolve_profile()` — if config has `"profile"` key, load from `~/.tabletalk/profiles.yml`
2. Call `resolve_env_vars()` on all string values — substitute `${VAR}` from environment
3. Instantiate and return the appropriate provider class

---

## Data flow

```
User question
    │
    ▼
QuerySession.generate_sql_conversational()
    │
    ├─ Load manifest text (cached)
    ├─ Build messages list:
    │   [system_prompt + manifest, ...history..., user_question]
    │
    ├─ LLMProvider.generate_chat_stream(messages)
    │   └─ yields SQL tokens
    │
    ├─ (if auto_execute) DatabaseProvider.execute_query(sql)
    │   └─ returns List[Dict]
    │
    ├─ (if explain) LLMProvider.generate_chat_stream(explain_messages)
    │   └─ yields explanation tokens
    │
    ├─ (if suggest) LLMProvider.generate_chat_stream(suggest_messages)
    │   └─ returns JSON array of questions
    │
    └─ save_history(question, sql, manifest)
```

---

## Adding a new database provider

1. Create `tabletalk/providers/myprovider_provider.py`
2. Subclass `DatabaseProvider` and implement all four abstract methods
3. Add the type mapping in `get_database_type_map()`
4. Register in `factories.get_db_provider()`:
   ```python
   elif db_type == "myprovider":
       from tabletalk.providers.myprovider_provider import MyProvider
       return MyProvider(...)
   ```
5. Add the optional dependency in `pyproject.toml`
6. Add tests in `tabletalk/tests/providers/test_myprovider.py`

See `providers/sqlite_provider.py` for the simplest reference implementation and `providers/postgres_provider.py` for a more complete example with FK/PK introspection.

---

## Adding a new LLM provider

1. Create `tabletalk/providers/myllm_provider.py`
2. Subclass `LLMProvider` and implement all three abstract methods
3. Register in `factories.get_llm_provider()`:
   ```python
   elif provider == "myllm":
       from tabletalk.providers.myllm_provider import MyLLMProvider
       return MyLLMProvider(config)
   ```

See `providers/openai_provider.py` for the reference implementation.
