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
  model: qwen2.5-coder:7b
  base_url: http://localhost:11434/v1

description: "Analytics database — dbt project my_dbt_project"
contexts: contexts
output: manifest
```

### 4. Define contexts for your dbt models

Create `contexts/` files that reference your dbt models (they're just tables/views in the database):

```yaml
# contexts/marts.yaml
name: marts
description: "Business-ready dbt mart models for analytics"
version: "1.0"

datasets:
  - name: analytics              # your dbt output schema
    tables:
      - name: fct_orders
        description: >-
          Fact table for orders. One row per order.
          Joins orders + customers + order_items.
          Use for revenue, funnel, and customer analysis.

      - name: fct_sessions
        description: >-
          Web session fact table. Aggregated from raw events by dbt.
          channel: organic | paid_search | social | email | direct.

      - name: dim_customers
        description: >-
          Customer dimension. SCD Type 2 — use is_current = true for current records.
          Enriched with lifetime_value and cohort_month from dbt.
```

**Tip:** reference your dbt `schema.yml` descriptions — they're already good descriptions to copy into tabletalk contexts.

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
├── profiles.yml             # copy to ~/.dbt/profiles.yml
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

# 1. Update the path in dbt_project/profiles.yml:
#    path: /Users/yourname/tabletalk/examples/ecommerce/ecommerce.duckdb

# 2. Copy to ~/.dbt/
cp dbt_project/profiles.yml ~/.dbt/profiles.yml

# 3. (Optional) Run dbt to create staging + mart models
pip install dbt-duckdb
cd dbt_project && dbt run && cd ..

# 4. Import the connection
tabletalk connect --from-dbt ecommerce

# 5. Update tabletalk.yaml to use the profile
#    profile: ecommerce_dev

# 6. Compile and query
tabletalk apply
tabletalk serve
```

---

## Referencing dbt models in contexts

When you run `dbt run`, your models are materialized as views or tables in the database. tabletalk queries them the same way it queries raw tables.

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

When you add new dbt models, update `contexts/*.yaml` to include them, then run `tabletalk apply` to recompile. The workflow is intentionally similar to `dbt run`:

```bash
# dbt workflow
dbt run                        # update models in the database

# tabletalk workflow
vim contexts/marts.yaml        # add new model
tabletalk apply                # recompile agents
```

---

## dbt descriptions → tabletalk descriptions

Your dbt `schema.yml` already has model descriptions. Copy them into tabletalk context files:

**dbt schema.yml:**
```yaml
models:
  - name: fct_orders
    description: "One row per order, enriched with customer and product info"
    columns:
      - name: order_id
        description: "Surrogate key from orders source"
```

**tabletalk context:**
```yaml
- name: fct_orders
  description: >-
    One row per order, enriched with customer and product info.
    order_id is the surrogate key. Use created_at for time-series.
    status: completed | cancelled | returned.
```

The two sources of truth are intentional — dbt descriptions are for data engineers, tabletalk descriptions are optimised for LLM SQL generation.
