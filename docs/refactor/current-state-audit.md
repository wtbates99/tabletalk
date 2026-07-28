# Current-state audit

Audit date: 2026-07-27. Baseline commit: `fceea72`. Production Python is 8,463
lines; Python tests are 5,892 lines. The package declares 7 core dependencies,
9 feature extras, and 7 development dependencies (counts are declaration entries,
not resolved transitive packages).

## Runtime and configuration

`QuerySession` loads `tabletalk.yaml`, eagerly constructs an LLM, loads text
manifests, prompts for SQL, optionally validates a prefix, executes through a
database adapter, and optionally asks the same LLM for prose. Flask and Click
duplicate parts of this flow. Evals inject a session, generate SQL, execute a
fixture, and calculate structural/result metrics.

Configuration fields observed include `provider` or `profile`; `llm.provider`,
`api_key`, `model`, `max_tokens`, `temperature`, `base_url`; `contexts`, `output`,
`safe_mode`, `max_conv_messages`, `max_rows`, `query_timeout`,
`slow_query_threshold_ms`, `audit_log`; state backend/bucket/prefix; and
web/runtime options. Database profiles add adapter-specific connection fields.
This permissive dictionary model is **rebuild**: validate one versioned schema,
retain secret references, and reject unknown/invalid fields.

State/history formats are `manifest/*.txt`, `.tabletalk.lock`,
`.tabletalk_history/`, `.tabletalk_history.jsonl`, `.tabletalk_audit.jsonl`,
`.tabletalk_agents.yaml`, schedules, favorites, and optional S3/GCS objects.
They are **replace/simplify** into canonical artifacts, applied state, eval
receipts, and invocation receipts.

## Commands

The root commands are `init`, `apply`, `validate`, `diff`, `test`, `eval run`,
`query`, `history`, `serve`, `connect`, `profiles list/delete/test`,
`schedule add/list/remove/run`, `plan`, `lint`, `check`, `lock`, `rollback`,
`promote`, `agents register/list/remove`, `discover`, `watch`, and `openapi`.
Their flags/arguments are defined in `tabletalk/cli.py`; notable flags cover
execution/explanation/output, eval report and gates, connection/dbt import,
schedules, server binding, discovery, rollback, promotion, and OpenAPI output.

**Preserve/rebuild:** `init`, `connect`, `discover`, `plan`, `eval`, `apply`,
query as `ask`, and `serve`. Add explicit `compile`. **Internalize:** validation,
locking, lint-like semantic checks, and artifact diff. **Delete/defer:** generic
test, history command, schedules, profile CRUD as a public subsystem, rollback,
promote until environment state is real, agent registry, watch, and OpenAPI
generation.

## Web/API

Routes are `/health`, `/`, `/manifests`, `/select_manifest`, `/chat/stream`,
`/fix/stream`, `/execute`, `/export`, `/api/query`, `/suggest`, `/reset`,
`/favorites` (GET/POST), `/favorites/<name>` (DELETE), `/history`, `/stats`,
`/config`, `/api/evals`, `/api/evals/run/stream`, legacy `/query`, `/metrics`,
`/metrics/json`, `/cache/stats`, `/cache/invalidate`, and `/webhooks`
(GET/POST/DELETE).

**Preserve/rebuild:** health, static application, one structured invocation
endpoint, configuration identity, eval list/run. **Simplify/internalize:** manifest
selection, fix/execute/export, metrics. **Delete:** duplicate/legacy query paths,
favorites, stats, reset, cache controls, webhooks, and ungrounded suggestions.

## Providers and model integrations

Database adapters are PostgreSQL, MySQL, BigQuery, Snowflake, DuckDB, Azure SQL,
and SQLite. **Preserve/rebuild:** SQLite, DuckDB, Snowflake. **Delete:** the other
four adapters, extras, docs, and tests.

LLM integrations are OpenAI, Anthropic, and Ollama implemented through the OpenAI
client. A heuristic router can choose `fast_model`. **Rebuild:** one
`openai-compatible` contract with configurable endpoint/auth/model/timeouts and an
explicit deterministic test fake. Ollama Cloud Free is the product default, while
local Ollama remains a first-class no-cost development path. **Delete:**
Anthropic-specific integration and heuristic model routing. Ollama defaults to
`gemma4:31b-cloud` by explicit product decision; a cloud failure must never trigger
automatic use of another model or locally generated logic. Existing
SQL/explanation/suggestion paths mostly propagate model errors, but the architecture
lacks typed stage errors and structured output guarantees.

## Evals, safety, partial systems, and examples

Evals support YAML cases, SQLite/DuckDB fixtures, expected values/reference SQL,
SQL structure/safety checks, score thresholds, terminal/JSON/JUnit reporting, and
usage metadata. **Preserve/rebuild** around artifact-linked receipts, semantic
interpretation, evidence, ambiguity, wrong metrics/dates, join multiplication, and
required apply gates.

Safety currently uses a first-token read-only allowlist and optional `safe_mode`;
manifest path traversal is blocked and result/time limits exist. **Rebuild:** safe
mode must be mandatory, use dialect-aware AST validation, enforce declared
relations/columns, prevent multi-statement escape, redact secrets, and fail closed.

Partial/non-core systems include caches, memory, generic tools, routing, schedules,
agent registry, remote state, webhooks, cost tables, favorites, and overlapping
metrics/history. They are **delete/defer** unless absorbed into receipts or the
focused lifecycle.

The sole example is a DuckDB/dbt ecommerce project with four agents, contexts,
eval, fixture, and pre-generated text manifests. **Rebuild** it as the DuckDB
example and add minimal SQLite and Snowflake journeys. Default automated tests
cover CLI, app, cache, dbt, evals, factories, sessions, memory, metrics, profiles,
registry, router, state, tools, utilities, and adapters. Coverage is broad around
legacy behavior but does not prove the target artifact/evidence lifecycle.

## Dependencies and classification

Keep or reassess PyYAML, Click, Rich, sqlglot, OpenAI, pytest, Ruff, mypy, DuckDB,
and Snowflake connector. Flask may be kept during the web rebuild. Remove
Anthropic and unused database/storage drivers after code removal. Sphinx and
keyring require a value/maintenance decision. Lockfile licenses require the
separate release audit.
