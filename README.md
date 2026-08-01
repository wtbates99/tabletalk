# TableTalk

TableTalk is an evaluation and observability framework for natural-language agents built on existing
dbt projects. `manifest.json` is the complete authority for what an agent may query; warehouse
connections are read-only execution adapters for those dbt relations.

## Start from a dbt project

```console
cd my-dbt-project
dbt parse
dbt docs generate  # recommended: adds physical warehouse types
tabletalk init
tabletalk agent create
tabletalk eval create revenue
tabletalk eval run revenue
tabletalk ask revenue "What was recognized revenue last month?"
```

`tabletalk init` finds `dbt_project.yml` and `target/manifest.json`, resolves a supported SQLite,
DuckDB, or Snowflake target from the dbt profile, and writes only non-secret TableTalk settings.
Agent creation asks the user to choose a displayed dbt group, tag, model, path, package, or source and
whether to include lineage. It previews the exact resolved models, descriptions, catalog types, tests,
constraints, and lineage before writing a small selector-based resource. Raw selectors remain available
for automation.

```yaml
name: revenue
description: Answers questions about recognized revenue.
select:
  - group:finance
  - tag:revenue
exclude:
  - model:customer_sensitive
instructions:
  - Use recognized revenue unless the user explicitly requests bookings.
sample_questions:
  - What was recognized revenue last month?
```

Descriptions, columns, tests, constraints, ownership, access, tags, and lineage stay in dbt. They are
never duplicated into an agent file.

## Correctness and provenance

Live questions and eval cases call the exact same `Runtime.answer` path. Before execution, TableTalk
parses generated SQL and requires one read-only query, in-scope manifest relations, known columns,
explicit join conditions, row and timeout limits, and explicit sensitive-data permission. It derives
used dbt nodes and columns from that parsed SQL—not from model output.

Every result includes the answer, interpretation, assumptions, generated and executed SQL, dbt nodes,
columns, relevant test health, bounded evidence, evidence-linked claims, verification outcomes,
manifest and agent fingerprints, model and warehouse identity, latency, and token usage. Terminal and
web views expose “How this answer was formed” without separate flags.

Eval correctness is determined by execution, scope, model/column expectations, result equality,
reference-query matching, shape/count assertions, tolerance, and claim evidence. Eval creation uses the
reviewed SQL as the default changing-data reference, runs it immediately, and writes a verification
record. A live answer is labeled `VERIFIED` only when its exact normalized question matches an approved
eval case and all hard checks pass; otherwise it is visibly `UNVERIFIED` and exits nonzero.

See the dbt-generated [reference project](examples/dbt-analytics/README.md) and the
[workflow guide](docs/getting-started.md).

## Commands

- `tabletalk init`
- `tabletalk agent create|list|show`
- `tabletalk eval create|run`
- `tabletalk ask`
- `tabletalk doctor`

There is no compile/plan/apply lifecycle or applied-artifact authorization state. Source changes are
active immediately and reproducibility records live under `.tabletalk/runs` and
`.tabletalk/eval-results`.
