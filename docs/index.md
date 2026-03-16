# tabletalk

> **dbt for agents** — define your data sources once, deploy a natural-language SQL agent for every dataset.

tabletalk lets you declaratively scope what data an AI agent can see, then deploy that agent as an interactive SQL interface — from the CLI or a streaming web UI. The workflow mirrors tools you already know.

```
contexts/*.yaml   ≈  dbt sources.yml    — what data the agent can see
manifest/*.txt    ≈  dbt manifest.json  — compiled schema (auto-generated)
tabletalk apply   ≈  dbt compile        — introspect DB + compile agents
tabletalk query   ≈  dbt run            — agent is live
```

---

## Features

- **Declarative agents** — one YAML file = one scoped agent
- **Auto schema introspection** — PKs, FKs, column types detected automatically
- **7 databases** — Postgres, Snowflake, DuckDB, MySQL, SQLite, BigQuery, Azure SQL
- **3 LLM backends** — Ollama (local, no key), OpenAI, Anthropic
- **Streaming web UI** — token-by-token SQL generation, auto-execution, charts, history
- **Multi-turn conversations** — agent remembers context across questions
- **dbt integration** — import connections directly from `~/.dbt/profiles.yml`
- **Safe mode** — read-only enforcement for production databases
- **No infrastructure** — single `pip install`, runs anywhere Python runs

---

## Quick install

```bash
pip install tabletalk                   # SQLite included — no driver needed
pip install "tabletalk[duckdb]"         # + DuckDB
pip install "tabletalk[postgres]"       # + PostgreSQL
pip install "tabletalk[all]"            # all drivers
```

---

## Documentation

| Guide | Description |
|-------|-------------|
| [Getting Started](getting-started.md) | Install, connect, and run your first query |
| [Core Concepts](concepts.md) | The dbt analogy, contexts, manifests, and the deploy lifecycle |
| [Configuration](configuration.md) | Complete `tabletalk.yaml` reference |
| [Writing Contexts](contexts.md) | How to define agent scopes and write effective descriptions |
| [CLI Reference](commands.md) | Every command, option, and flag |
| [Web UI](web-ui.md) | Features, navigation, and keyboard shortcuts |
| [Databases](databases.md) | Setup guide for all 7 supported databases |
| [LLM Providers](llm-providers.md) | Ollama, OpenAI, and Anthropic configuration |
| [Profile Management](profiles.md) | Save and reuse database connections |
| [dbt Integration](dbt-integration.md) | Import connections from `~/.dbt/profiles.yml` |
| [Safe Mode](safe-mode.md) | Read-only enforcement and production deployment |
| [REST API](api-reference.md) | Full API reference for the web server |
| [Architecture](architecture.md) | Internals for contributors |
| [Contributing](contributing.md) | How to add providers, tests, and features |

---

## Example

```bash
cd examples/ecommerce
pip install "tabletalk[duckdb]"
python seed.py          # create the demo database
tabletalk apply         # compile agents
tabletalk serve         # open http://localhost:5000
```

Ask anything:

```
> What is total revenue by month?
> Which products drive the most revenue?
> Break that down by category
```

---

## License

CC BY-NC 4.0 — free for non-commercial use.
Commercial licensing: wtbates99@gmail.com

---

```{toctree}
:maxdepth: 1
:caption: Getting Started
:hidden:

getting-started
concepts
```

```{toctree}
:maxdepth: 1
:caption: Reference
:hidden:

configuration
contexts
commands
profiles
```

```{toctree}
:maxdepth: 1
:caption: Databases & LLMs
:hidden:

databases
llm-providers
dbt-integration
```

```{toctree}
:maxdepth: 1
:caption: Features
:hidden:

web-ui
safe-mode
api-reference
```

```{toctree}
:maxdepth: 1
:caption: Internals
:hidden:

architecture
contributing
```
