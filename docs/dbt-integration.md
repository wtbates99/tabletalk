# dbt Integration

tabletalk is designed as a complement to dbt. If you already have a dbt project, you can connect tabletalk to the same database in one command — no re-entering credentials.

---

## The connection

tabletalk reads `~/.dbt/profiles.yml` and converts your dbt connection to a tabletalk profile:

```bash
tabletalk connect --from-dbt my_dbt_project
tabletalk connect --from-dbt my_dbt_project --target prod
```

This finds the named profile in `~/.dbt/profiles.yml`, reads the target's connection details, and saves a tabletalk profile to `~/.tabletalk/profiles.yml`.

### Supported dbt adapters

| dbt adapter | tabletalk provider |
|-------------|-------------------|
| `postgres` | `postgres` |
| `snowflake` | `snowflake` |
| `duckdb` | `duckdb` |
| `bigquery` | `bigquery` |
| `sqlserver` | `azuresql` |

MySQL is not a standard dbt adapter — configure it directly with `tabletalk connect`.

---

## Step-by-step workflow

### 1. Import the connection

```bash
tabletalk connect --from-dbt my_dbt_project
# or for a specific target:
tabletalk connect --from-dbt my_dbt_project --target prod
```

You'll be prompted to confirm the profile name:

```
Importing dbt profile my_dbt_project (target: dev)…
Profile name [my_dbt_project_dev]: analytics_dev
✓ Connection successful — profile saved as 'analytics_dev'
```

### 2. Create a tabletalk project

```bash
mkdir tabletalk_agents && cd tabletalk_agents
tabletalk init
```

### 3. Configure tabletalk.yaml to use the imported profile

```yaml
profile: analytics_dev

llm:
  provider: ollama
  api_key: ollama
  model: gemma4:31b-cloud
  base_url: http://localhost:11434/v1

dbt:
  manifest: ../my_dbt_project/target/manifest.json

description: "Analytics database — dbt project my_dbt_project"
contexts: contexts
output: manifest
```

### 4. Define contexts for your dbt models

Create `contexts/` files that define the hard table boundary for each agent.
The dbt artifact supplies semantics; the TableTalk context decides which
relations that agent may see:

```yaml
# contexts/marts.yaml
name: marts
description: "Business-ready dbt mart models for analytics"
version: "1.0"

datasets:
  - name: analytics              # your dbt output schema
    tables:
      - name: fct_orders
        # Description is inherited from dbt when omitted.

      - name: fct_sessions
        description: >-
          Web session fact table. Aggregated from raw events by dbt.
          channel: organic | paid_search | social | email | direct.

      - name: dim_customers
        description: >-
          Customer dimension. SCD Type 2 — use is_current = true for current records.
          Enriched with lifetime_value and cohort_month from dbt.
```

Run `dbt compile` or `dbt build` before `tabletalk apply`. TableTalk reads the
compiled `manifest.json` and matches its nodes to the live relations returned
by database introspection.

### 5. Compile and query

```bash
tabletalk apply
tabletalk serve
```

---

## The ecommerce demo

The `examples/ecommerce/` directory includes a minimal dbt project at `examples/ecommerce/dbt_project/` that demonstrates the full workflow:

```
dbt_project/
├── dbt_project.yml          # profile: ecommerce
├── profiles.yml             # portable local demo profile
└── models/
    ├── sources.yml          # declares 8 raw tables as dbt sources
    ├── staging/
    │   ├── stg_orders.sql
    │   └── stg_customers.sql
    └── marts/
        ├── fct_orders.sql   # enriched orders with customer + item count
        └── fct_orders.yml   # schema tests
```

To run the demo:

```bash
cd examples/ecommerce

# 1. Seed the database
uv run python seed.py

# 2. Build models, tests, and manifest.json with the bundled profile
cd dbt_project
uv run --with dbt-duckdb dbt build --profiles-dir .
cd ..

# 3. Enable native dbt context in tabletalk.yaml
#    dbt:
#      manifest: dbt_project/target/manifest.json

# 4. Compile and query
uv run tabletalk apply .
uv run tabletalk serve
```

The bundled profile defaults to `../ecommerce.duckdb`. To point it elsewhere,
set `TABLETALK_ECOMMERCE_DB` to an absolute path.

---

## Referencing dbt models in contexts

When you run `dbt build`, your models are materialized as views or tables in
the database and their semantic metadata is compiled to `manifest.json`.
TableTalk queries the live relations and sends the matching dbt context to
Ollama.

**Good context strategy for dbt projects:**

- **Raw sources** — useful for debugging and data exploration contexts
- **Staging models** — cleaned and typed data; good for operational agents
- **Mart models** — business-ready aggregations; best for business user agents

```yaml
# contexts/raw.yaml — for data engineers
datasets:
  - name: raw
    tables:
      - name: raw_orders
        description: "Raw orders from the source system, unmodified"

# contexts/marts.yaml — for business users
datasets:
  - name: analytics
    tables:
      - name: fct_orders
        description: "Business-ready orders fact table, built by dbt"
```

---

## Keeping contexts in sync with dbt models

When you add new dbt models, update `contexts/*.yaml` to include them, then run
`tabletalk apply` to recompile:

```bash
# dbt workflow
dbt build                      # update models, tests, and manifest.json

# tabletalk workflow
vim contexts/marts.yaml        # add new model
tabletalk apply                # recompile agents
```

---

## What reaches Ollama

For every relation included by a TableTalk context, the compiled prompt can
contain:

- `DBT_DESCRIPTION` for model or source meaning;
- `DBT_COLUMN` for business definitions;
- `DBT_LINEAGE` for upstream provenance;
- `DBT_TESTS` for constraints such as uniqueness and non-null expectations.

TableTalk context descriptions still take precedence when supplied, so a team
can add agent-specific guidance without duplicating normal dbt documentation.
If `dbt.manifest` is configured but absent or invalid, `tabletalk apply` stops.
It never quietly compiles a less-informed agent.
