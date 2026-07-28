# CLI reference

The public CLI follows one lifecycle:

```text
connect → discover → compile → plan → eval → apply → ask
```

Use `tabletalk COMMAND --help` for the authoritative option list.

## Core commands

### `tabletalk init`

Creates a working SQLite project, Agent, EvalSuite, fixture, and configuration
using `gemma4:31b-cloud` through the local Ollama endpoint.

### `tabletalk connect`

Creates and tests a SQLite, DuckDB, or Snowflake project connection. It can
import a connection from dbt. Secrets are resolved at runtime and are not
written to compiled artifacts, plans, receipts, or history.

### `tabletalk discover`

Lists visible database relations and columns. It can write a scoped Agent
resource from selected relations.

### `tabletalk compile`

Introspects declared relations and writes canonical, content-addressed candidate
artifacts. Compilation is deterministic and does not invoke a model or update
applied state. `--check` exits nonzero if committed candidates differ.

### `tabletalk plan`

Compares candidates with applied artifacts and reports semantic additions,
changes, removals, policy changes, and required-eval changes.

### `tabletalk eval`

Runs versioned EvalSuite resources against exact candidate digests using the
structured runtime and real database fixtures. Passing receipts bind the suite
source digest, Agent name, and candidate artifact digest.

### `tabletalk apply`

Requires passing receipts for every declared suite. It validates the complete
candidate set before atomically updating `.tabletalk/state.json`; a partial
multi-Agent apply cannot occur. Interactive confirmation is required unless
explicitly approved.

### `tabletalk ask AGENT QUESTION`

Runs an applied Agent and prints its verified answer, interpretation, SQL,
sources, evidence, calculations, verification checks, and technical receipt.
Model, configuration, database, safety, and insufficient-evidence failures are
explicit and never replaced by fallback answers.

### `tabletalk serve`

Starts the trust-centered local web application. Set
`TABLETALK_PROJECT_FOLDER` to select the project.

## Inspection commands

- `tabletalk agents list`
- `tabletalk agents inspect NAME`
- `tabletalk connections list`
- `tabletalk connections test NAME`
- `tabletalk connections inspect NAME`

The removed profiles, schedules, registry, cache, history-query, promotion,
rollback, watch, and direct text-to-SQL commands are not part of the supported
surface.
