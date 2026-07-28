# Getting started

Install and authenticate the free local-development model:

```bash
uv sync --extra duckdb
ollama signin
ollama pull gemma4:31b-cloud
```

Create and inspect a complete SQLite project:

```bash
mkdir my-tabletalk-project
cd my-tabletalk-project
tabletalk init
tabletalk compile
tabletalk plan
```

`init` creates a seeded read-only SQLite database, `agents/sales.yaml`, and
`evals/starter.yaml`. Compilation is deterministic and offline.

Run the required suite and apply the exact passing artifact:

```bash
tabletalk eval
tabletalk apply
```

These steps invoke the configured model. If Ollama, the model, or the database
is unavailable, TableTalk exits with an explicit failure and leaves applied
state unchanged.

Ask through the CLI or web:

```bash
tabletalk ask sales "What was recognized revenue in January 2026?"
tabletalk serve
```

The response separates interpretation, verification, evidence, data, SQL and
sources, calculations, and a technical receipt.

For other databases, start with `examples/duckdb-analytics` or
`examples/snowflake-production` in the repository.
