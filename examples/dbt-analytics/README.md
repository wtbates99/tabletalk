# Commerce and supply-chain example

This dbt project contains one finance mart. The mart includes booked revenue,
recognized revenue, cancelled orders, inventory, work in progress, late orders,
and sensitive customer identifiers.

The default model is `gemma4:31b-cloud` through an Ollama-compatible endpoint.
TableTalk fails if that endpoint is unavailable. It does not create a fabricated
answer.

## See drift caught in 10 seconds

From the repository root:

```console
make demo
```

The script copies this project to a scratch directory and builds it on DuckDB.
It locks the `revenue` agent's scope, then applies `demo/pr.patch`. The patch is
a routine-looking dbt PR: it renames `recognized_revenue` and adds a customer
model to `group:finance`. Every dbt test still passes. `tabletalk plan --check`
shows the removed column, the expanded scope with sensitive columns, and the eval
case at risk, then exits 5. It needs no model and no network.

## Build the dbt project

Run these commands from this directory:

```console
dbt deps --profiles-dir .
dbt seed --profiles-dir .
dbt build --profiles-dir .
dbt docs generate --profiles-dir .
tabletalk init --project-dir . --no-input
```

The checked-in dbt artifacts let you inspect proposals before you build the
warehouse. Regenerate the artifacts after every dbt model change.

## Create and review a proposal

Run the deterministic proposal command first:

```console
tabletalk propose \
  --sql-import imports/finance-recognized-revenue.sql \
  --answer "time_basis:model.analytics.fct_orders.booked_revenue=booked_date"
```

The answer confirms one missing semantic definition. The imported query remains
evidence until approval. TableTalk previews every resource and prints its exact
digest. It does not write an agent or eval resource.

For this project, the group boundary produces a `finance` agent proposal. The
proposal contains the confirmed booked-revenue time basis. It also contains the
approved SQL definition for recognized revenue.

Use model inference only when you want untrusted hypotheses:

```console
tabletalk propose --infer
```

The checked-in `semantics/revenue.yaml` is a separate approved example. It shows both states. The
`recognized_revenue` statement is confirmed. The `late_order` statement is
inferred and cannot support an answer.

Approve the exact digest that the proposal command prints:

```console
tabletalk approve prp_REPLACE_ME \
  --yes \
  --digest sha256:REPLACE_ME \
  --actor "$USER"
```

Approval writes only the reviewed bytes. Approval fails if the dbt manifest or
the base file changes. TableTalk runs each approved eval immediately.

## Run, replay, and compare evaluations

Run three independent trials:

```console
tabletalk eval run finance --trials 3
```

The command writes JSON, Markdown, and JUnit reports under
`.tabletalk/reports/`. It also stores one immutable inference artifact for each
trial.

Repeat the same run without a model request:

```console
tabletalk eval run finance --trials 3 --cache-policy replay
```

Replay fails on a cache miss. It never calls the model.

Approve a report as a comparison baseline:

```console
tabletalk baseline approve finance-gemma4 \
  --from .tabletalk/reports/REPORT_ID/report.json \
  --yes \
  --digest sha256:REPORT_DIGEST \
  --actor "$USER"
```

After you change one fixed model, prompt, agent, or dbt configuration, run the
paired comparison:

```console
tabletalk eval run finance --trials 3 --baseline finance-gemma4
```

The report includes pass-rate effects, confidence intervals, McNemar results,
latency effects, Wilcoxon results, and small-sample warnings.

## Exercise the trust boundaries

The checked-in `revenue` agent and eval show the four user-facing trust
outcomes. They represent reviewed project resources.

Ask a covered question:

```console
tabletalk ask revenue "What was recognized revenue in July 2026?"
```

Ask for sensitive data:

```console
tabletalk ask revenue "List customer IDs and recognized revenue."
```

Ask an ambiguous question:

```console
tabletalk ask revenue "What was revenue in July 2026?"
```

Ask about the inferred late-order definition:

```console
tabletalk ask revenue "How many late orders were there?"
```

The first question has approved eval coverage. The other questions must request
clarification or return a safe refusal.

To demonstrate a caught agent regression, edit the proposed
`semantics/finance.yaml` in a temporary branch. Change the recognized-revenue
`time_column` from `order_date` to `booked_date`. Keep only the cancelled-order
filter. Then run this command:

```console
tabletalk eval run finance --case approved-query-1 --cache-policy write-only
```

The fixed July reference query fails because booked and recognized dates produce
different values. In a real project, make the semantic change through a new
proposal and review it before merge.
