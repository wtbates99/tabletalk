# Contributing

TableTalk requires Python 3.10+ and uses `uv`:

```console
uv sync --all-extras
uv run pytest
uv run ruff check .
uv run mypy tabletalk
```

The acceptance fixture is a real dbt project under `examples/dbt-analytics`. When its dbt resources
change, regenerate the checked-in artifact instead of hand-editing it:

```console
cd examples/dbt-analytics
uv run dbt deps
uv run dbt parse --profiles-dir . --no-partial-parse
uv run dbt docs generate --profiles-dir .
```

Keep these boundaries intact: manifest selection is the only source of query scope; database
introspection cannot add resources; all live and eval questions use `Runtime.answer`; model and column
usage comes from parsed SQL; result comparison is the hard correctness gate; and persisted records are
observability evidence, never deployment authorization.

Tests should cover manifest selection, SQL safety/scope, connectors, deterministic comparisons,
evidence-linked traces, and the complete dbt → agent → eval → ask journey. Snowflake behavior should use
a mocked connector contract unless a test is explicitly marked for live credentials.
