# dbt analytics example

This is the complete TableTalk workflow over an ordinary dbt project. Configure an `analytics`
DuckDB profile whose `dev` target points at `analytics.duckdb`, then run:

```console
dbt deps
dbt seed
dbt run
dbt parse
dbt docs generate
tabletalk init
tabletalk agent show revenue
tabletalk eval run revenue
tabletalk ask revenue "What was recognized revenue in July 2026?"
```

The agent contains only selection and behavior. Descriptions, columns, constraints, tests, ownership,
tags, and lineage remain in dbt and are loaded from `target/manifest.json`. Run records appear under
`.tabletalk/runs`; eval results appear under `.tabletalk/eval-results`.

The checked-in `target/manifest.json` and `target/catalog.json` are generated after `dbt deps` by
`dbt parse --profiles-dir .` and `dbt docs generate --profiles-dir .`; neither is hand-authored.
Regenerate both after changing the example models or warehouse schema.
