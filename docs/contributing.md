# Contributing

## Maintenance mode

TableTalk is in maintenance mode. There is no active feature development, and
responses are slow: expect days to weeks.

Pull requests that will be reviewed:

- Security fixes, especially to the SQL guard. A failing test that reproduces
  the bypass is the most useful part. Private vulnerability reporting is not
  enabled on the repository, so open an issue that describes the class of
  bypass without a working query, and ask for a private channel.
- Bug fixes with a test that fails before the fix.
- Compatibility fixes for new versions of dbt, sqlglot, DuckDB, or the
  Snowflake connector.
- Corrections to the documentation.

New features, new adapters, and large refactors will probably not be reviewed.
Open an issue first if you are unsure.

## Development

TableTalk requires Python 3.11+ and uses `uv`:

```console
uv sync --locked --all-groups
uv run pytest -q
uv run ruff check .
uv run mypy --strict tabletalk scripts
uv run ruff format --check .
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
