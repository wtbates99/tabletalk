# Configuration and resources

`tabletalk.yaml` is small and versioned:

```yaml
schema_version: tabletalk.project/v1
dbt:
  project_dir: .
  manifest: target/manifest.json
  catalog: target/catalog.json
  run_results: target/run_results.json
  target: dev

llm:
  provider: ollama
  model: gemma4:31b-cloud
  base_url: http://localhost:11434/v1
  temperature: 0

inference:
  cache_policy: normal
  persist_prompts: false
```

All editable TableTalk resources require a `schema_version`. Run
`tabletalk migrate` to add version identifiers to pre-0.6 editable YAML files.
Immutable inference, report, result, and baseline artifacts fail on an unknown
schema. TableTalk does not rewrite those records.

## dbt ownership

dbt remains authoritative for resources, columns, types, tests, constraints,
owners, access, tags, groups, packages, descriptions, and lineage. TableTalk
agent files use selectors. They do not copy dbt metadata.

Supported selectors are `group:`, `tag:`, `model:`, `source:`, `path:`, and
`package:`. Lineage expansion never grants access to a source. Ephemeral and
disabled models are not queryable.

## Agent policy

Agent files may contain these TableTalk-specific fields:

- `supported_questions` and `unsupported_questions`
- `instructions`
- `knowledge` references
- `allow_sensitive` approvals
- `reject_if_contains` for exact missing-data boundaries
- row and timeout limits: `max_rows` (default 1000) and `timeout_seconds`
  (default 60)
- `max_scan_bytes`, a Snowflake scan budget in bytes.

### Snowflake authentication

TableTalk reads Snowflake credentials from the dbt profile target, with the
same field names as dbt-snowflake. It never stores credentials in traces,
receipts, or fingerprints. Use `{{ env_var('...') }}` for secrets.

| Method | Profile fields | Use for |
|---|---|---|
| Password | `password` | Local development |
| SSO | `authenticator: externalbrowser` | A person at a workstation. Opens a browser. |
| Key pair | `private_key_path`, optional `private_key_passphrase` | CI and long-running servers such as `tabletalk mcp` |

Any other `authenticator` value is passed to the Snowflake connector unchanged.
OAuth tokens (`token`) are not supported yet.

To check a real account, run the read-only check script. It confirms the
connection, the EXPLAIN estimate that the scan budget reads, the server-side
timeout, and the query tag:

```console
uv run --extra snowflake python scripts/snowflake_check.py --project-folder path/to/project
```

### Snowflake cost and audit controls

```yaml
max_scan_bytes: 10_000_000_000  # 10 GB
timeout_seconds: 60
```

Before TableTalk runs a query on Snowflake, it compiles the query with
`EXPLAIN USING JSON`. If `GlobalStats.bytesAssigned` exceeds `max_scan_bytes`,
TableTalk blocks the query. EXPLAIN uses no warehouse compute. If Snowflake
cannot return an estimate, TableTalk blocks the query instead of guessing. DuckDB
and SQLite return no estimate, so the budget does not apply to them, and the
`scan_budget` check in the receipt says so.

Every Snowflake query also sets two session parameters:

- `STATEMENT_TIMEOUT_IN_SECONDS` equal to `timeout_seconds`. Snowflake cancels the
  query on the server, so a client timeout does not leave it running.
- `QUERY_TAG`, a JSON object with `app`, `agent`, and `inference_fingerprint`. It
  joins `QUERY_HISTORY` to the receipt's `replay.inference_fingerprint`:

```sql
select query_text, bytes_scanned, total_elapsed_time
from snowflake.account_usage.query_history
where try_parse_json(query_tag):app = 'tabletalk';
```

Use `reject_if_contains` only for a known concept that the selected dbt scope
cannot answer. Do not use it as a general intent classifier.

## Semantic status

| Status | Meaning | Can support an answer |
|---|---|---:|
| `trusted` | The information comes from an approved source with provenance. | Yes |
| `confirmed` | A user explicitly accepted the meaning. | Yes |
| `inferred` | A model proposed the meaning. A user has not confirmed it. | No |
| `missing` | TableTalk lacks required meaning. | No |
| `conflicting` | Approved sources give incompatible meanings. | No |

## Secrets and evidence retention

TableTalk resolves credentials at runtime. It removes secret-like model fields
from fingerprints. It never writes credentials into agent resources.

Inference artifacts store a digest of model messages by default. Set
`persist_prompts: true` only when exact prompt replay is more important than raw
prompt retention risk.

An expected clarification or refusal is also an inference artifact. TableTalk
stores its typed outcome and usage. Strict replay reproduces that outcome without
a model request.

Trace and report files store bounded evidence rows. Claim checks and result
replay require these rows. Sensitive columns still require explicit agent
approval. TableTalk has no automatic deletion service. The project operator
must set file access and retention rules for `.tabletalk/`.
