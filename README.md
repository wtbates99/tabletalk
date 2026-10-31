# TableTalk

**Mount a dbt project. Review proposed agents and evaluations. Prove that your data agents are safe, grounded, and correct.**

[![Tests](https://github.com/wtbates99/tabletalk/actions/workflows/db_provider_tests.yml/badge.svg)](https://github.com/wtbates99/tabletalk/actions/workflows/db_provider_tests.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![PyPI](https://img.shields.io/pypi/v/tabletalk)](https://pypi.org/project/tabletalk/)
[![License](https://img.shields.io/badge/license-PolyForm%20Noncommercial-c9a84c)](LICENSE)

TableTalk is the dbt-native development, evaluation, and observability layer for
trusted data agents. It uses the analytical knowledge that a company already
maintains in dbt. It creates reproducible agents and defensible evaluation
evidence. It does not require a second large semantic configuration system.

TableTalk is not a general agent framework. It is not a BI dashboard. It does
not replace dbt.

## What TableTalk proves

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

## Check every dbt pull request

```yaml
- uses: wtbates99/tabletalk@main
  with:
    evals: replay
```

The action posts one comment with the agent plan and a model-free eval replay.
See [pull-request checks](docs/ci.md).

## Security and retention

- Use a warehouse identity with physical read-only permissions.
- Treat connector permissions as the final security boundary.
- TableTalk rejects writes, unsafe reads, unknown resources, unknown columns,
  unsafe joins, excessive limits, and unapproved sensitive data.
- TableTalk never treats lineage as proof that a join is correct.
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

TableTalk is source-available under the
[PolyForm Noncommercial License 1.0.0](LICENSE). The license permits the uses
that its text defines. Commercial use requires separate written permission.

William Bates offers implementation, integration, semantic onboarding, and
custom eval-suite development. See the
[commercial notice](COMMERCIAL-LICENSE.md). Earlier releases keep their earlier
license grants. See [LICENSE_HISTORY.md](LICENSE_HISTORY.md).
