# Contributing

TableTalk requires Python 3.10+ and uses `uv`:

```console
uv sync --locked --all-groups
uv run pytest -q
uv run ruff check .
uv run mypy tabletalk
```

The acceptance fixture is a real dbt project under `examples/dbt-analytics`. When its dbt resources
change, regenerate the checked-in artifact instead of hand-editing it:

```console
cd examples/dbt-analytics
uv run dbt deps
uv run dbt seed --full-refresh --profiles-dir .
uv run dbt build --profiles-dir .
uv run dbt docs generate --profiles-dir .
```

Keep these boundaries intact:

- Manifest selection is the only source of query scope.
- Database introspection must not add resources.
- All live and eval questions use `Runtime.answer`.
- Parsed SQL defines actual model and column use.
- Result comparison is the hard correctness gate.
- Persisted records are observability evidence. They are not deployment authorization.

Tests must use fakes at model boundaries. The opt-in Ollama test must use
`gemma4:31b-cloud`. Normal CI must not require a network service. Snowflake
tests must use a connector contract unless a test explicitly requires live
credentials.
