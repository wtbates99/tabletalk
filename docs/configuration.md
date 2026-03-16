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
safe_mode: true               # restrict execution to SELECT queries only
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

Profiles are stored in `~/.tabletalk/profiles.yml`. Create them with `tabletalk connect` or `tabletalk connect --from-dbt`. See [Profile Management](profiles.md).

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

**Ollama example:**

```yaml
llm:
  provider: ollama
  api_key: ollama                      # placeholder — not validated by Ollama
  model: qwen2.5-coder:7b
  base_url: http://localhost:11434/v1
```

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

---

### `description`

```yaml
description: "Production analytics database — Snowflake, updated nightly"
```

A human-readable description of the database. Included in the manifest and injected into the LLM system prompt — helps the agent understand the overall context.

---

### `contexts`

```yaml
contexts: contexts    # default
```

Path to the directory containing agent context YAML files, relative to the project root. Defaults to `contexts`.

---

### `output`

```yaml
output: manifest    # default
```

Path to the directory where compiled manifests are written, relative to the project root. Defaults to `manifest`.

---

### `safe_mode`

```yaml
safe_mode: true
```

When `true`, the agent refuses to execute any SQL that is not a `SELECT` (or `WITH`, `EXPLAIN`, `SHOW`, `DESCRIBE`). Write operations raise an error before they reach the database. Defaults to `false`.

Strongly recommended for any agent connected to a production database. See [Safe Mode](safe-mode.md).

---

## Environment variable substitution

Any value in `tabletalk.yaml` can reference an environment variable using `${VAR_NAME}` syntax:

```yaml
password: ${DB_PASSWORD}
api_key: ${OPENAI_API_KEY}
host: ${DB_HOST}
```

Variables are resolved at startup. If a referenced variable is not set, tabletalk raises an error with the variable name — it never silently falls back to an empty string.

**Supported in:** any string value in the `provider` and `llm` blocks.

---

## Multiple environments

The recommended pattern for multiple environments (dev/staging/prod) is to use profiles rather than multiple config files:

```yaml
# tabletalk.yaml — always points to the right profile for the environment
profile: ${TABLETALK_PROFILE}    # set in your CI/CD env
```

Or maintain separate project directories:

```
projects/
├── dev/
│   └── tabletalk.yaml    # profile: analytics_dev
├── staging/
│   └── tabletalk.yaml    # profile: analytics_staging
└── prod/
    └── tabletalk.yaml    # profile: analytics_prod, safe_mode: true
```
