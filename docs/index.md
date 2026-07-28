# TableTalk

> Trusted data agents as code.

TableTalk connects governed SQLite, DuckDB, or Snowflake data to declarative
agents and returns inspectable SQL and database evidence. Models are replaceable
runtime dependencies accessed through one OpenAI-compatible contract.

Core principles:

- correct failure over false confidence;
- execution evidence over fluent unsupported prose;
- deterministic, reviewable agent artifacts;
- required evals before apply;
- no hidden model, provider, SQL, or answer fallback.

Start with [Getting Started](getting-started.md), then read
[Configuration](configuration.md), [Databases](databases.md),
[LLM Providers](llm-providers.md), [dbt Integration](dbt-integration.md), and
[Evals](evals.md).

The product thesis and migration architecture live in `docs/refactor/`.

```{toctree}
:maxdepth: 2

getting-started
concepts
configuration
contexts
commands
databases
llm-providers
dbt-integration
evals
safe-mode
web-ui
api-reference
architecture
profiles
contributing
refactor/product-thesis
refactor/market-positioning
refactor/user-journey
refactor/reliability-model
refactor/target-architecture
refactor/evaluation-strategy
refactor/current-state-audit
refactor/deletion-plan
refactor/migration-plan
refactor/licensing-audit
refactor/progress-audit
```
