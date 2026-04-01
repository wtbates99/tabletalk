# Architecture

This document describes tabletalk's internal structure for contributors and integrators.

tabletalk is "dbt and Terraform had a kid for agents" — it brings the declarative infrastructure model (define → compile → deploy) to AI data agents. You declare which tables an agent can see, `apply` compiles the live schema into a manifest, and agents query against that manifest.

---

## Package layout

```
tabletalk/
├── cli.py              # Click CLI — init, apply, validate, diff, test, query, serve, schedule
├── app.py              # Flask web server — REST API + SSE streaming
├── interfaces.py       # Core abstractions — QuerySession, Parser, QueryMetrics, base classes
├── utils.py            # Project scaffolding and apply helpers
├── factories.py        # Provider registry — instantiate LLM and DB providers from config
├── profiles.py         # ~/.tabletalk/profiles.yml read/write, keyring integration, dbt import
├── static/
│   └── index.html      # Single-page web UI (vanilla JS, no build step)
└── providers/
    ├── sqlite_provider.py
    ├── duckdb_provider.py
    ├── postgres_provider.py      # connection pooling via ThreadedConnectionPool
    ├── mysql_provider.py         # connection pooling via MySQLConnectionPool
    ├── snowflake_provider.py
    ├── bigquery_provider.py
    ├── azuresql_provider.py
    ├── openai_provider.py        # token usage tracking via stream_options
    └── anthropic_provider.py     # token usage tracking via get_final_message()
```

---

## Core abstractions (`interfaces.py`)

### `DatabaseProvider` (abstract)

All database backends implement this interface:

```python
class DatabaseProvider(ABC):
    @abstractmethod
    def execute_query(self, sql: str) -> List[Dict[str, Any]]: ...
    def get_client(self) -> Any: ...
    def get_database_type_map(self) -> Dict[str, str]: ...
    def get_compact_tables(self, schema_name, table_names=None) -> List[Dict]: ...

    # Built-in caching layer (300s TTL, no extra config required)
    def get_cached_compact_tables(self, schema_name, table_names=None, ttl=300): ...
    def invalidate_schema_cache(self) -> None: ...
```

`get_cached_compact_tables()` wraps `get_compact_tables()` with an in-process TTL cache. The `Parser` calls this during `apply` — repeated applies within the TTL window skip the database round-trip entirely.

### `LLMProvider` (abstract)

```python
class LLMProvider(ABC):
    last_usage: Dict[str, int]   # {"prompt_tokens": N, "completion_tokens": N}
                                  # populated after every call by concrete providers

    @abstractmethod
    def generate_response(self, prompt: str) -> str: ...
    def generate_response_stream(self, prompt: str) -> Generator[str, None, None]: ...
    def generate_chat_stream(self, messages: List[Dict]) -> Generator[str, None, None]: ...
```

`last_usage` is set by each provider after every streaming call. `QuerySession` reads it immediately after generation completes and stores it in the history entry's `metrics` dict.

### `QueryMetrics` (dataclass)

```python
@dataclass
class QueryMetrics:
    generation_ms: float = 0.0
    execution_ms: float = 0.0
    row_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
```

Attached to every history entry. Aggregated by `get_usage_stats()` and surfaced via `/stats`.

### `QuerySession`

The central orchestration class. One instance per project directory.

**Config-driven behaviour:**

| Config key | Default | Behaviour |
|------------|---------|-----------|
| `max_conv_messages` | 20 | Conversation history window |
| `max_rows` | 500 | Result set cap |
| `query_timeout` | None | Thread-based timeout (DB-agnostic) |
| `safe_mode` | false | Block non-SELECT queries |
| `audit_log` | false | Append to `.tabletalk_audit.jsonl` |

**State:**
- `self.config` — parsed `tabletalk.yaml`
- `self.llm_provider` — LLM instance
- `self._db_provider` — DB instance (lazily initialised)
- `self._manifest_cache` — dict of `filename → manifest text`

### `Parser`

Compiles context definitions into manifest files.

**`Parser.apply_schema()`** flow:
1. Read `tabletalk.yaml` — find `contexts` and `output` directories
2. For each `contexts/*.yaml`:
   a. Parse the YAML
   b. For each dataset/table, call `db_provider.get_cached_compact_tables()`
   c. Merge YAML descriptions with introspected schema
   d. Format in compact notation
   e. Write to `manifest/<name>.txt`

### `_collect_stream(generator)` helper

Consolidated streaming utility — exhausts any token generator while measuring wall-clock time. Used internally by `generate_sql()` and anywhere synchronous collection is needed.

---

## Compact schema notation

Manifests use a compact single-line format per table:

```
schema.table_name|Table description|col1:TYPE[CONSTRAINTS]|col2:TYPE|...
```

Type codes: `I` Integer, `S` String, `F` Float, `N` Numeric, `D` Date, `DT` DateTime, `TS` Timestamp, `T` Time, `B` Boolean, `BY` Binary, `J` JSON, `U` UUID, `A` Array, `IV` Interval

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

All prompts are defined as class-level constants in `QuerySession`:

| Constant | Purpose |
|----------|---------|
| `_SYSTEM_PROMPT` | Instructs the LLM to generate valid SQL given the manifest schema. Injected as the `system` message in every conversation. |
| `_EXPLAIN_PROMPT` | Generates a 1–2 sentence plain-English explanation of query results. |
| `_SUGGEST_PROMPT` | Returns 3 follow-up questions as a JSON array. |
| `_FIX_PROMPT` | Corrects a failing SQL query given the error message. |

---

## Web server (`app.py`)

Flask application with a module-level `QuerySession` singleton.

```python
_qs: Optional[QuerySession] = None

def _get_session() -> QuerySession:
    global _qs
    if _qs is None:
        _qs = QuerySession(project_folder)
    else:
        # Auto-reload: check manifest staleness every 30s
        if now - _qs._last_staleness_check > 30:
            if check_manifest_staleness(project_folder):
                _qs.invalidate_manifest_cache()
    return _qs
```

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Enhanced readiness probe (manifests + DB + LLM) |
| `GET` | `/config` | Active LLM provider, model, and runtime limits |
| `GET` | `/stats` | Aggregate token usage and latency stats |
| `GET` | `/manifests` | List compiled manifests |
| `POST` | `/select_manifest` | Load a manifest, reset conversation |
| `POST` | `/chat/stream` | Main SSE streaming endpoint (rate-limited) |
| `POST` | `/fix/stream` | Fix failing SQL (SSE) |
| `POST` | `/execute` | Execute SQL, return results |
| `POST` | `/export` | Execute SQL, return CSV or JSON download |
| `POST` | `/api/query` | Synchronous REST endpoint for integrations |
| `POST` | `/suggest` | Generate follow-up question suggestions |
| `POST` | `/reset` | Clear conversation context |
| `GET` | `/history` | Recent query history with metrics |
| `GET/POST/DELETE` | `/favorites` | Saved queries CRUD |
| `POST` | `/query` | Legacy non-streaming endpoint (backward compat) |

### Rate limiting

`/chat/stream` uses a per-session sliding-window limiter. State is held in `_rate_limit_store` (module-level dict). Configurable via `TABLETALK_RATE_LIMIT` and `TABLETALK_RATE_WINDOW` environment variables.

### SSE streaming

`/chat/stream` uses Flask's `Response` with a generator and `stream_with_context`:

```python
def generate():
    for chunk in qs.generate_sql_conversational(...):
        yield f"data: {json.dumps({'type': 'sql_chunk', 'content': chunk})}\n\n"
    generation_ms = ...
    usage = getattr(qs.llm_provider, "last_usage", {})
    yield f"data: {json.dumps({'type': 'sql_done', 'sql': sql, 'generation_ms': ..., **usage})}\n\n"
    ...

return Response(stream_with_context(generate()), content_type="text/event-stream")
```

---

## Provider factories (`factories.py`)

Uses a registry pattern — supported providers are declared as dicts rather than if/elif chains:

```python
_LLM_INSTALL_HINTS: Dict[str, str] = {
    "openai": "...",
    "anthropic": "...",
    "ollama": "...",
}

_DB_INSTALL_HINTS: Dict[str, str] = {
    "postgres": "uv add 'tabletalk[postgres]'",
    ...
}
```

Typed config shapes (TypedDict) document expected keys for all 8 database provider configs and the LLM config. IDE autocomplete and mypy use these without a runtime Pydantic dependency.

---

## Connection pooling

**Postgres** uses `psycopg2.pool.ThreadedConnectionPool` (min=1, max=5). A context manager handles borrow/return:

```python
@contextmanager
def _conn(self):
    conn = self._pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        self._pool.putconn(conn)
```

**MySQL** uses `mysql.connector.pooling.MySQLConnectionPool` (pool_size=5). `conn.close()` returns the connection to the pool rather than closing it.

Other providers (DuckDB, SQLite, Snowflake, BigQuery, Azure SQL) use single connections — their drivers either handle thread-safety internally or are not typically used in concurrent web contexts.

---

## Data flow

```
User question
    │
    ▼
QuerySession.generate_sql_conversational()
    │
    ├─ Load manifest text (in-memory cache, invalidated on staleness)
    ├─ Trim conversation history to max_conv_messages
    ├─ Build messages: [system+schema, ...history..., user_question]
    │
    ├─ LLMProvider.generate_chat_stream(messages)
    │   ├─ yields SQL tokens → SSE sql_chunk events
    │   └─ sets last_usage = {"prompt_tokens": N, "completion_tokens": N}
    │
    ├─ (if auto_execute) DatabaseProvider.execute_query(sql)
    │   ├─ Thread timeout wrapper (query_timeout)
    │   ├─ Row cap enforcement (max_rows)
    │   ├─ Audit log write (if audit_log: true)
    │   └─ returns List[Dict]
    │
    ├─ (if explain) LLMProvider.generate_response_stream(explain_prompt)
    │   └─ yields explanation tokens → SSE explain_chunk events
    │
    ├─ (if suggest) LLMProvider.generate_response(suggest_prompt)
    │   └─ returns JSON array of questions → SSE suggestions event
    │
    └─ save_history(question, sql, manifest, metrics=QueryMetrics(...))
```

---

## Adding a new database provider

1. Create `tabletalk/providers/myprovider_provider.py`
2. Subclass `DatabaseProvider` and implement all four abstract methods
3. Add the type mapping in `get_database_type_map()`
4. Register in `factories._build_db_provider()`:
   ```python
   if provider_type == "myprovider":
       from tabletalk.providers.myprovider_provider import MyProvider
       return MyProvider(...)
   ```
5. Add to `_DB_INSTALL_HINTS` in `factories.py`
6. Add the optional dependency in `pyproject.toml`
7. Add tests in `tabletalk/tests/providers/test_myprovider.py`

See `providers/sqlite_provider.py` for the simplest reference implementation.

---

## Adding a new LLM provider

1. Create `tabletalk/providers/myllm_provider.py`
2. Subclass `LLMProvider`, call `super().__init__()`, implement the abstract methods
3. Set `self.last_usage` after each call for token tracking
4. Register in `factories.get_llm_provider()` and add to `_LLM_INSTALL_HINTS`

See `providers/openai_provider.py` for the reference implementation including token tracking.
