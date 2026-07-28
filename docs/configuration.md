# Configuration

`tabletalk.yaml` defines the database, exact model runtime, semantic inputs, and
execution limits.

```yaml
provider:
  type: sqlite
  database_path: ./analytics.db

llm:
  provider: ollama
  api_key: ollama
  model: gemma4:31b-cloud
  base_url: http://localhost:11434/v1
  max_tokens: 2000
  temperature: 0
  reasoning_effort: none
  request_timeout_seconds: 60

dbt:
  manifest: ../analytics/target/manifest.json

contexts: contexts
agents: agents
output: manifest
safe_mode: true
max_rows: 500
query_timeout: 30
max_conv_messages: 20
audit_log: false
```

Use either an inline `provider` or a named `profile`. Supported database types
are `sqlite`, `duckdb`, and `snowflake`; see [Databases](databases.md).

## Model runtime

All model paths use one OpenAI-compatible implementation.

| Field | Required | Default | Description |
|---|---:|---|---|
| `provider` | yes | — | `ollama`, `openai-compatible`, or `openai` alias |
| `api_key` | yes | — | Literal or `${ENV_VAR}` reference |
| `model` | yes for compatibility endpoints | `gemma4:31b-cloud` for Ollama | Exact model name |
| `base_url` | yes for `openai-compatible` | Ollama local endpoint for `ollama` | Exact API root |
| `temperature` | no | `0` | Low/deterministic sampling |
| `max_tokens` | no | `1000` | Completion token budget |
| `reasoning_effort` | no | `none` for Ollama | Reserve output budget for structured results |
| `request_timeout_seconds` | no | `60` | Model request timeout |

Production compatibility configuration:

```yaml
llm:
  provider: openai-compatible
  base_url: ${TABLETALK_LLM_BASE_URL}
  api_key: ${TABLETALK_LLM_API_KEY}
  model: ${TABLETALK_LLM_MODEL}
  temperature: 0
  request_timeout_seconds: 60
```

The three identity fields are mandatory. TableTalk never tries another endpoint
or model if the configured request fails.

## Secrets

`${NAME}` placeholders resolve at runtime and fail when unset. Never put literal
production credentials in project files. Compiled artifacts, plans, receipts,
history, and logs must contain non-secret runtime identity only.
