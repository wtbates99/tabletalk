# Configuration Reference

All project settings live in `tabletalk.yaml` in your project root. This file is read every time you run `tabletalk apply`, `tabletalk query`, or `tabletalk serve`.

---

## Full example

```yaml
# ── Database connection ────────────────────────────────────────────────────────
provider:
  type: postgres
  host: localhost
  port: 5432
  database: analytics
  user: analyst
  password: ${DB_PASSWORD}    # resolved from environment at startup

# Or reference a saved profile (recommended for production)
# profile: my_prod_postgres

# ── LLM ───────────────────────────────────────────────────────────────────────
llm:
  provider: openai            # openai | anthropic | ollama
  api_key: ${OPENAI_API_KEY}
  model: gpt-4o
  max_tokens: 1000
  temperature: 0

# ── Project ───────────────────────────────────────────────────────────────────
description: "Production analytics database"
contexts: contexts            # directory containing agent context definitions
output: manifest              # directory where compiled manifests are written

# ── Safety & limits ───────────────────────────────────────────────────────────
safe_mode: true               # restrict execution to SELECT queries only
max_rows: 500                 # cap result set size (default: 500)
query_timeout: 30             # kill queries after N seconds (default: no timeout)
max_conv_messages: 20         # conversation history window in messages (default: 20)

# ── Observability ─────────────────────────────────────────────────────────────
audit_log: false              # write .tabletalk_audit.jsonl for every execute
```

---

## Fields

### `provider`

Inline database connection. Use this or `profile`, not both.

| Field | Required | Description |
|-------|----------|-------------|
| `type` | Yes | Database type — see [Databases](databases.md) for all options |
| `host` | Varies | Hostname (Postgres, MySQL, Azure SQL) |
| `port` | No | Port number (defaults vary by database) |
| `database` | Varies | Database name |
| `user` | Varies | Username |
| `password` | Varies | Password |
| `database_path` | Varies | File path (DuckDB, SQLite) |
| `account` | Varies | Account identifier (Snowflake) |
| `warehouse` | Varies | Warehouse name (Snowflake) |
| `project_id` | Varies | GCP project ID (BigQuery) |

See the [Databases](databases.md) page for the complete field list per database type.

### `profile`

Reference a saved connection profile instead of inlining credentials.

```yaml
profile: my_prod_snowflake
```

Profiles are stored in `~/.tabletalk/profiles.yml`. Create them with `tabletalk connect` or import them from dbt with `tabletalk connect --from-dbt`. See [Profile Management](profiles.md).

When `profile` is set, the `provider` block is ignored.

---

### `llm`

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `provider` | Yes | — | LLM backend: `openai`, `anthropic`, or `ollama` |
| `api_key` | Yes | — | API key (use `${ENV_VAR}` to read from environment) |
| `model` | No | varies | Model name — see [LLM Providers](llm-providers.md) |
| `max_tokens` | No | `1000` | Maximum tokens in the LLM response |
| `temperature` | No | `0` | Sampling temperature (`0` = deterministic) |
| `base_url` | No | — | Custom endpoint URL (required for Ollama) |

**OpenAI example:**

```yaml
llm:
  provider: openai
  api_key: ${OPENAI_API_KEY}
  model: gpt-4o
  max_tokens: 2000
  temperature: 0
```

**Anthropic example:**

```yaml
llm:
  provider: anthropic
  api_key: ${ANTHROPIC_API_KEY}
  model: claude-sonnet-4-6
  max_tokens: 1000
  temperature: 0
```

**Ollama example:**

```yaml
llm:
  provider: ollama
  api_key: ollama                      # placeholder — not validated by Ollama
  model: qwen2.5-coder:7b
  base_url: http://localhost:11434/v1
```

---

### `description`

```yaml
description: "Production analytics database — Snowflake, updated nightly"
```

Included in the manifest and injected into the LLM system prompt. Helps the agent understand the broader context of the data source.

---

### `contexts`

```yaml
contexts: contexts    # default
```

Path to the directory containing agent context YAML files, relative to the project root.

---

### `output`

```yaml
output: manifest    # default
```

Path to the directory where compiled manifests are written, relative to the project root.

---

### `safe_mode`

```yaml
safe_mode: true
```

When `true`, the agent refuses to execute any SQL that is not a `SELECT` (or `WITH`, `EXPLAIN`, `SHOW`, `DESCRIBE`). Write operations raise an error before they reach the database. Defaults to `false`.

Strongly recommended for any agent connected to a production database. See [Safe Mode](safe-mode.md).

---

### `max_rows`

```yaml
max_rows: 500    # default
```

Maximum number of rows returned from any query execution. Results larger than this are silently truncated. Protects against accidental full-table scans flooding memory or the UI.

```yaml
max_rows: 100    # tight limit for dashboard agents
max_rows: 5000   # larger limit for data export use cases
```

---

### `query_timeout`

```yaml
query_timeout: 30    # seconds; default: no timeout
```

Kill any query that takes longer than this many seconds. Implemented at the Python layer (thread timeout) so it is database-agnostic — works with all providers.

When a timeout fires, the user sees:
```
Query timed out after 30s. Increase 'query_timeout' in tabletalk.yaml or optimise the query.
```

---

### `max_conv_messages`

```yaml
max_conv_messages: 20    # default
```

Maximum number of messages to retain in the conversation history window. Each question + answer pair uses 2 messages. Higher values give the agent more context for follow-up questions but increase LLM token consumption.

```yaml
max_conv_messages: 6     # tight window, minimal tokens
max_conv_messages: 40    # longer sessions, more context
```

---

### `audit_log`

```yaml
audit_log: false    # default
```

When `true`, every SQL execution is appended to `.tabletalk_audit.jsonl` in the project directory. Each entry records:

```json
{
  "timestamp": "2026-03-01T14:32:01.123456+00:00",
  "action": "execute",
  "sql": "SELECT ...",
  "row_count": 42
}
```

Useful for compliance, debugging, and cost attribution in multi-user environments.

---

## Environment variable substitution

Any value in `tabletalk.yaml` can reference an environment variable using `${VAR_NAME}` syntax:

```yaml
password: ${DB_PASSWORD}
api_key: ${OPENAI_API_KEY}
host: ${DB_HOST}
```

Variables are resolved at startup. If a referenced variable is not set, tabletalk raises an error with the variable name and a fix instruction — it never silently falls back to an empty string.

---

## Rate limiting (web server)

The web server's `/chat/stream` endpoint enforces a per-session sliding-window rate limit. Configure via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `TABLETALK_RATE_LIMIT` | `30` | Max requests per window |
| `TABLETALK_RATE_WINDOW` | `60` | Window size in seconds |

```bash
export TABLETALK_RATE_LIMIT=10   # tighter limit for shared deployments
export TABLETALK_RATE_WINDOW=60
tabletalk serve
```

---

---

### `slow_query_threshold_ms`

```yaml
slow_query_threshold_ms: 5000    # default: 5000ms (5 seconds)
```

Queries that exceed this threshold are logged as warnings and appended to the audit log as `slow_query` events. Set to a lower value for stricter monitoring.

---

### `state`

Remote state backend configuration. Defaults to local filesystem.

```yaml
state:
  backend: local         # local | s3 | gcs
  bucket: my-state       # s3 / gcs only
  prefix: myproject      # optional key prefix
```

- `local` — manifests live in the `manifest/` directory (default)
- `s3` — requires `uv add 'tabletalk[s3]'`
- `gcs` — requires `uv add 'tabletalk[gcs]'`

---

### `llm.router`

LLM complexity router — routes simple queries to a fast model and complex queries to a powerful model.

```yaml
llm:
  provider: openai
  model: gpt-4o             # powerful model for complex queries
  fast_model: gpt-4o-mini   # fast model for simple queries
  router:
    enabled: true
    threshold: 0.5          # complexity score 0–1; above → powerful model
```

See `tabletalk/router.py` for the scoring heuristic.

---

## Multiple environments

The recommended pattern for multiple environments is to use profiles:

```yaml
# tabletalk.yaml — environment set via CI/CD
profile: ${TABLETALK_PROFILE}
```

Or maintain separate project directories:

```
projects/
├── dev/
│   └── tabletalk.yaml    # profile: analytics_dev
├── staging/
│   └── tabletalk.yaml    # profile: analytics_staging
└── prod/
    └── tabletalk.yaml    # profile: analytics_prod
                          # safe_mode: true
                          # max_rows: 100
                          # query_timeout: 15
```
