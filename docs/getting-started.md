# Get started

TableTalk starts with a parsed, runnable dbt project. It does not discover arbitrary database tables.

```console
cd my-dbt-project
dbt parse
dbt docs generate
tabletalk init
```

Initialization finds the dbt project and manifest, reads the project profile, asks for a target, checks
that its adapter is SQLite, DuckDB, or Snowflake, summarizes dbt version/models/groups/tags, and writes
`tabletalk.yaml`. Credentials stay in environment variables and `profiles.yml`.

Create an agent from dbt selectors:

```console
tabletalk agent create
```

Choose a displayed dbt group, tag, model, path, package, or source, then choose whether to include its
lineage. The guided preview shows exact resources, catalog types, tests, constraints, and metadata gaps.
Create the first eval when prompted. TableTalk shows the interpretation and SQL before read-only
execution, uses that reviewed SQL as the default golden query, verifies it immediately, and saves both
the case and result record.

```console
tabletalk eval run revenue
tabletalk ask revenue "What was recognized revenue last month?"
```

Both commands use the same runtime. `ask` prints `VERIFIED` only for an exact normalized question match
against an approved, passing eval; new or changed questions print `UNVERIFIED` and exit nonzero. Run
`tabletalk doctor` in CI or locally to detect missing/stale
artifacts, unsupported targets, connectivity failures, broken selectors, metadata gaps, and uncovered
agents. Metadata gaps are warnings; conditions that prevent safe or verified execution fail the command.
The complete runnable journey is in `examples/dbt-analytics`.
