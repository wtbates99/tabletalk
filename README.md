# TableTalk

TableTalk defines trusted data agents as code. An Agent declares the relations,
semantics, policies, and regression suites it may use. TableTalk compiles that
source into a content-addressed artifact, shows a semantic plan, runs
execution-based evals, and only applies the exact artifact that passed.

At runtime, an applied Agent produces a structured interpretation and query
plan, validates generated SQL against its compiled scope, executes a read-only
query, and returns claims tied to evidence and reproducible calculations.
Missing models, database failures, empty evidence, and unsupported claims are
reported explicitly. They are never replaced by heuristic or locally generated
answers.

## Free local development

Local development defaults to Ollama's free cloud model
`gemma4:31b-cloud`. Ollama runs the OpenAI-compatible endpoint locally; the
model itself is the cloud model.

```bash
ollama signin
ollama pull gemma4:31b-cloud

uv sync --extra duckdb
uv run tabletalk init
uv run tabletalk compile
uv run tabletalk plan
uv run tabletalk eval
uv run tabletalk apply
uv run tabletalk ask starter "How many customers are there?"
```

The generated `tabletalk.yaml` contains:

```yaml
llm:
  provider: ollama
  model: gemma4:31b-cloud
  base_url: http://localhost:11434/v1
  api_key: ollama
  temperature: 0
```

Production is not tied to Gemma or Ollama. Configure any compatible model
endpoint explicitly. TableTalk does not silently switch models or providers.

## Install

```bash
pip install tabletalk
pip install "tabletalk[duckdb]"     # optional DuckDB driver
pip install "tabletalk[snowflake]"  # optional Snowflake driver
```

SQLite is included with Python. The supported database surface is deliberately
limited to SQLite, DuckDB, and Snowflake.

## Project layout

```text
.
├── tabletalk.yaml
├── agents/
│   └── starter.yaml
├── evals/
│   └── starter.yaml
└── .tabletalk/              # generated, content-addressed local state
    ├── artifacts/
    ├── evals/
    ├── history/
    └── state.json
```

An Agent is a versioned resource:

```yaml
kind: Agent
version: "1"
name: starter
description: Answers customer questions from the approved dataset.
connection: default
relations:
  include:
    - main.customers
semantics:
  metrics:
    customer_count:
      expression: count(id)
      relation: main.customers
policies:
  read_only: true
  require_evidence: true
  max_rows: 500
  timeout_seconds: 30
evals:
  - starter_regression
```

An EvalSuite binds executable expectations to that Agent:

```yaml
kind: EvalSuite
version: 1
name: starter_regression
agent: starter
environment:
  connection: default
cases:
  - name: customer_count
    messages:
      - role: user
        content: How many customers are there?
    expected:
      result:
        type: scalar
        value: 3
```

## Lifecycle

```bash
tabletalk connect
tabletalk discover
tabletalk compile
tabletalk plan
tabletalk eval
tabletalk apply
tabletalk ask AGENT "QUESTION"
tabletalk serve
```

- `connect` creates or tests a project connection without writing secrets into
  generated artifacts.
- `discover` shows visible database metadata and can write a scoped Agent.
- `compile` is deterministic and does not invoke a model.
- `plan` compares canonical candidate and applied artifacts.
- `eval` runs the exact candidate through the real structured runtime.
- `apply` requires passing receipts for every required suite and updates all
  selected Agents atomically.
- `ask` and `serve` use applied artifacts only and expose evidence, SQL,
  verification, and technical receipts.

Supporting inspection commands are available under `agents` and `connections`.
Run `tabletalk COMMAND --help` for exact options. Exit codes distinguish usage
errors, configuration failures, eval failures, and runtime failures.

## Database configuration

SQLite:

```yaml
connections:
  default:
    type: sqlite
    database_path: ./data.db
    read_only: true
```

DuckDB:

```yaml
connections:
  default:
    type: duckdb
    database_path: ./analytics.duckdb
    read_only: true
```

Snowflake secrets should come from environment variables, not source files:

```yaml
connections:
  default:
    type: snowflake
    account: ${SNOWFLAKE_ACCOUNT}
    user: ${SNOWFLAKE_USER}
    password: ${SNOWFLAKE_PASSWORD}
    database: ANALYTICS
    warehouse: COMPUTE_WH
    schema: PUBLIC
    role: TABLETALK_READER
```

Use a database identity that is independently restricted to read-only access.
SQL AST validation is an additional boundary, not a replacement for database
permissions.

## dbt metadata

Point TableTalk at a dbt project or manifest:

```yaml
dbt:
  project_dir: ../analytics
  target_dir: target
```

Compilation incorporates available descriptions, lineage, tests,
materializations, tags, groups, and ownership into the canonical artifact.
A changed dbt manifest changes the candidate digest and must pass evals again.

## Examples and verification

- [`examples/sqlite-starter`](examples/sqlite-starter) — smallest local path
- [`examples/duckdb-analytics`](examples/duckdb-analytics) — analytics joins and
  a join-multiplication regression
- [`examples/snowflake-production`](examples/snowflake-production) — env-only
  Snowflake credentials with a local DuckDB eval fixture

Run the deterministic suite:

```bash
uv run pytest -q
uv run ruff check tabletalk
uv run mypy tabletalk
```

The live Gemma smoke test is opt-in because it needs an authenticated Ollama
daemon:

```bash
TABLETALK_RUN_LIVE_OLLAMA=1 \
uv run pytest -q -m live_ollama tabletalk/tests/test_live_ollama.py
```

## Security and data handling

- Query execution is read-only and limited to one parsed query.
- Agent relation, column, join, row, and timeout policies are enforced before
  execution.
- Applied state references immutable artifact and eval-receipt digests.
- Local invocation history stores metadata and receipts, not raw result rows.
- Credential-shaped values are redacted from history and HTTP responses.
- A model or database outage produces a typed failure; no cached prose,
  alternate provider, or heuristic response is substituted.

## License

The current package metadata retains CC BY-NC 4.0 while the repository's
ownership and third-party asset audit remains unresolved. See
[`docs/refactor/licensing-audit.md`](docs/refactor/licensing-audit.md).
