# Get started

TableTalk starts from a parsed dbt project. It does not discover arbitrary
warehouse tables.

## 1. Prepare dbt

```console
cd my-dbt-project
dbt parse
dbt docs generate
tabletalk init
```

Initialization finds `dbt_project.yml` and `manifest.json`. It selects a SQLite,
DuckDB, or Snowflake target from the dbt profile. It writes a small
`tabletalk.yaml` file. Credentials remain in the profile or environment.

## 2. Review semantic gaps and proposals

```console
tabletalk propose
```

The command prints the semantic quality summary first. It then prints concrete
questions that can unlock supported queries. Each proposal contains a small
agent, starter evals, and optional semantic or reference files.

Use explicit inputs when dbt lacks required meaning:

```console
tabletalk propose --answer "GAP_ID=VALUE"
tabletalk propose --sql-import path/to/query.sql
tabletalk propose --bi-import path/to/queries.json
tabletalk propose --history-import path/to/history.csv
```

An import is a preview. TableTalk normalizes the SQL and removes literal values
from the preview record. It does not execute the query. Imported behavior is not
trusted until approval.

`--infer` requests model hypotheses. Every hypothesis remains inferred and
untrusted.

## 3. Approve exact bytes

```console
tabletalk proposal show PROPOSAL_ID
tabletalk approve PROPOSAL_ID
```

The approval prompt displays exact resource bytes, source evidence, confidence,
and a unified diff. The command writes no resource before approval.

Automation must bind the proposal digest:

```console
tabletalk approve PROPOSAL_ID \
  --yes \
  --digest sha256:PROPOSAL_DIGEST \
  --actor ci-user
```

Approval runs each written eval immediately. A failing regression cannot become
a baseline unless its case has an explicit known-failure record.

## 4. Run and inspect evals

```console
tabletalk eval run revenue --trials 3
```

The terminal prints a short result. Detailed JSON, Markdown, and JUnit reports
appear under `.tabletalk/reports/`.

## 5. Ask a question

```console
tabletalk ask revenue "What was recognized revenue last month?"
```

TableTalk prints `VERIFIED` only when an approved eval has the same normalized
question and all hard checks pass. A different question is `UNVERIFIED`. This
label does not mean that the answer is false.

Use the [commerce example on GitHub](https://github.com/wtbates99/tabletalk/tree/main/examples/dbt-analytics)
for the complete workflow.
