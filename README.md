# TableTalk

**Mount a dbt project. Review proposed agents and evaluations. Record evidence of whether your data agents stay in scope and give correct answers.**

[![Tests](https://github.com/wtbates99/tabletalk/actions/workflows/db_provider_tests.yml/badge.svg)](https://github.com/wtbates99/tabletalk/actions/workflows/db_provider_tests.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![PyPI](https://img.shields.io/pypi/v/tabletalk)](https://pypi.org/project/tabletalk/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

TableTalk is the dbt-native development, evaluation, and observability layer for
trusted data agents. It uses the analytical knowledge that a company already
maintains in dbt. It creates reproducible agents and replayable evaluation
evidence. It does not require a second large semantic configuration system.

TableTalk is not a general agent framework. It is not a BI dashboard. It does
not replace dbt.

## Status

**Alpha, in maintenance mode.** There is no active feature development. Bug
fixes and security fixes are still reviewed, slowly (see
[contributing](docs/contributing.md)).

What works, verified offline on DuckDB with the example project in this repo
(`uv run pytest -q`, `make demo`, `make replay`, and CI on Python 3.11 to 3.13):

- The SQL guard: scope, columns, sensitive columns, the row limit, and a
  result-column backstop, tested with 87 adversarial cases.
- `tabletalk lock` and `tabletalk plan`, including `--check` failing on drift.
- Strict eval replay and `tabletalk receipt replay` with no model call.
- The GitHub Action, which runs on this repository's own pull requests.
- `tabletalk mcp`, tested with an in-process MCP client.
- `tabletalk score` and `tabletalk fix`.

Not verified:

- A real Snowflake account. Snowflake auth, the scan budget, timeouts, and query
  tags are tested only against a fake connector. `scripts/snowflake_check.py`
  exists to check a real account, but it has not been run against one.
- The Claude Desktop MCP configuration in [docs/mcp.md](docs/mcp.md).
- The shields.io badge that `tabletalk score --badge` writes.
- Source freshness in receipts from a real `dbt source freshness` run. It is
  tested only with a hand-written `sources.json`, and the example project has
  none.
- Live models other than the recorded `gemma4:31b-cloud` runs.

## What TableTalk records

- `manifest.json` defines the queryable resources.
- Agent files contain dbt selectors and small TableTalk policies.
- Every agent and eval starts as a review-only proposal.
- Approval binds the exact proposal, manifest, base files, and resource bytes.
- Trusted semantic statements record source evidence.
- Inferred statements remain untrusted until a user confirms them.
- SQL validation runs before every read-only warehouse query.
- Live questions and evals use the same runtime.
- Approved reference queries provide changing-data ground truth.
- Inference artifacts support strict replay with no model request.
- Reports include hard checks, trial metrics, uncertainty, paired comparisons,
  and failure ownership.

## Install

```console
pip install tabletalk
pip install "tabletalk[duckdb]"
pip install "tabletalk[snowflake]"
```

TableTalk requires Python 3.11 or newer. The supported warehouses are DuckDB
and Snowflake. SQLite works for tests and local experiments only.

## Start from an existing dbt project

```console
cd my-dbt-project
dbt parse
dbt docs generate
tabletalk init
tabletalk propose
tabletalk approve
tabletalk eval run
tabletalk ask revenue "What was recognized revenue last month?"
```

`tabletalk propose` analyzes dbt groups, tags, packages, paths, owners, access,
metrics, semantic models, tests, constraints, descriptions, and lineage. It
creates at most three coherent proposals. It prefers curated marts. It shows
semantic gaps and exact file diffs. It does not write project resources.

The approval command requires the exact proposal digest for automated use:

```console
tabletalk approve prp_EXAMPLE \
  --yes \
  --digest sha256:EXAMPLE \
  --actor "$USER"
```

Approval fails if the manifest or a reviewed base file changed. TableTalk runs
approved evals immediately.

## Acquire missing meaning

Provide a targeted answer:

```console
tabletalk propose \
  --answer "time_basis:model.analytics.orders.booked_revenue=booked_date"
```

Preview an organizational query as evidence:

```console
tabletalk propose --sql-import finance/revenue.sql
```

Request optional model hypotheses:

```console
tabletalk propose --infer
```

TableTalk marks every model hypothesis as inferred. An inferred statement cannot
support an answer. A user must confirm its meaning through a new proposal.

## Evaluate and replay

```console
tabletalk eval run revenue --trials 3 --workers 2
tabletalk eval run revenue --trials 3 --cache-policy replay
tabletalk eval run revenue --baseline revenue-v1
```

Each run writes a concise terminal result and three detailed artifacts:

- versioned JSON for tools
- JUnit XML for CI
- Markdown for a pull request.

The report identifies the model, agent, manifest, semantic knowledge, prompt,
runtime, warehouse, and inference fingerprint. It reports pass rate, a Wilson
confidence interval, latency, tokens, configured cost, flaky outcomes, and
deterministic failure diagnoses.

## Answer receipts

`tabletalk ask` prints a receipt for each answer and stores it as JSON. A receipt
shows the trust level, the executed SQL, the evidence rows, and the dbt models
and column descriptions the agent used. It also shows their tests, lineage,
source freshness, and dependent dashboards, plus a replay command.

```console
tabletalk ask revenue "What was recognized revenue in July 2026?"
tabletalk receipt replay run_5c0b6a582ba84c14
```

`verified` means that an approved eval matched the question and passed.
`grounded` means that every hard check passed but no eval covers the question.
`unverified` means that a check failed. The format is an open specification:
[`tabletalk.receipt/v1`](docs/spec/receipt-v1.md).

## Score readiness

```console
tabletalk score --badge .github/badges/agent-ready.json
```

The score is a fixed rubric over descriptions, tests, sensitive-data flags,
resolved semantics, the lock, and eval coverage. Each missing point lists its
fix. See [the rubric](docs/score.md).

`tabletalk fix` turns the fixes it can make with certainty into a patch for your
dbt project: it flags columns that look sensitive but have no flag, and copies a
column description from upstream when every upstream description agrees. It
never invents a description and never edits dbt files itself.

```console
tabletalk fix --write fix.patch && git apply fix.patch
```

## Use agents from Claude, Cursor, or any MCP client

```console
pip install "tabletalk[mcp]"
tabletalk mcp --project-folder .
```

The MCP server serves only agents that match `tabletalk.lock.yaml`, and every
answer includes its receipt. If a dbt change alters an agent's scope, that agent
stops answering until the change is reviewed and locked. See
[MCP server](docs/mcp.md).

## Check every dbt pull request

```yaml
- uses: wtbates99/tabletalk@main
  with:
    evals: replay
```

The action posts one comment with the agent plan and a model-free eval replay.
See [pull-request checks](docs/ci.md).

## Security and retention

The warehouse identity's permissions are the security boundary. TableTalk adds
checks in front of it; it does not replace it. Use a role that can only read
the models in the agents' scope. See the [security model](docs/security-model.md).

What TableTalk enforces:

- **SQL AST guard.** One read-only query statement. Writes, commands, a fixed
  denylist of functions, and table functions in `FROM` are rejected.
- **Scope.** Every table must resolve to a dbt resource in the agent's selector
  scope, checked per CTE and subquery. Every column must exist on that resource.
  Joins need a condition.
- **Sensitive columns.** Columns flagged sensitive are rejected anywhere in the
  query, including through `*`, unless the agent allows them. The returned
  column names are checked again before rows reach the model, a trace, or MCP.
- **Row limits.** `LIMIT max_rows` is added, or an integer `LIMIT` above it is
  lowered.
- **Scan budget.** On Snowflake, `max_scan_bytes` blocks a query whose
  estimated scan is over budget. Every query is tagged and has a server-side timeout. See
  [Snowflake controls](docs/configuration.md#snowflake-cost-and-audit-controls).
- TableTalk never treats lineage as proof that a join is correct.

Known limitations, none fixed yet:

- The guard uses a function denylist, not an allowlist. Unlisted scalar
  functions are allowed.
- There is no connection-level hardening. DuckDB external access is not
  disabled, and no read-only Snowflake role is created or checked.
- On DuckDB and SQLite the timeout stops waiting but may not cancel the running
  query. Not verified.
- Sensitivity is also guessed from English keywords in
  `semantic_guard.is_sensitive`. Flag sensitive columns in dbt instead.
- A computed `LIMIT` such as `limit 10 + 999990` is not capped.

Retention:

- TableTalk does not store credentials in traces, reports, or fingerprints.
- Inference artifacts hash prompts by default. Set `persist_prompts: true` only
  when exact raw-prompt retention is acceptable.
- Traces and reports retain bounded evidence rows. Exact evidence is required
  for deterministic replay and claim validation.
- TableTalk does not delete artifacts automatically. The operator must apply a
  retention policy to `.tabletalk/`.

See the [commerce example](examples/dbt-analytics/README.md), the
[getting-started guide](docs/getting-started.md), and the
[research evidence](docs/research.md).

## Ideas, not built

- [MetricFlow spike](docs/notes/metricflow-spike.md): the model picks dbt
  metrics, MetricFlow compiles the SQL, and the guard re-validates it. An
  unmerged experiment.

## Development

```console
uv sync --locked --all-groups
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy tabletalk  # strict mode, from pyproject.toml
uv build
```

The live Ollama smoke test is opt-in. It uses only `gemma4:31b-cloud`:

```console
TABLETALK_LIVE_OLLAMA=1 uv run pytest -m live_ollama
```

## License and services

TableTalk is open source under the [Apache License 2.0](LICENSE), including
for commercial use. Earlier releases keep their earlier license grants. See
[LICENSE_HISTORY.md](LICENSE_HISTORY.md).

William Bates offers implementation, integration, semantic onboarding, and
custom eval-suite development for teams adopting data agents on dbt. Contact
[William Bates](mailto:wbates@palanbates.com).
