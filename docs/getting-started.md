# Getting Started

This guide takes you from zero to a running agent in about five minutes.

---

## 1. Install

tabletalk's core is pure Python with no database driver required. Install the extras for whichever database you're connecting to:

```bash
pip install tabletalk                    # SQLite only (built-in)
pip install "tabletalk[duckdb]"          # + DuckDB
pip install "tabletalk[postgres]"        # + PostgreSQL
pip install "tabletalk[mysql]"           # + MySQL
pip install "tabletalk[snowflake]"       # + Snowflake
pip install "tabletalk[bigquery]"        # + BigQuery
pip install "tabletalk[azuresql]"        # + Azure SQL / SQL Server
pip install "tabletalk[all]"             # all drivers
```

Requires Python 3.10+.

---

## 2. Choose an LLM

tabletalk works with Ollama (local, no API key), OpenAI, or Anthropic.

### Option A — Ollama (recommended for getting started)

No API key, runs on your machine. Install [Ollama](https://ollama.com), then pull a model:

```bash
ollama pull qwen2.5-coder:7b    # excellent SQL generation, 7B params (~4 GB)
```

Ollama runs at `http://localhost:11434` by default — no other configuration needed.

### Option B — OpenAI

```bash
export OPENAI_API_KEY=sk-...
```

### Option C — Anthropic

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

---

## 3. Initialize a project

```bash
mkdir my_project && cd my_project
tabletalk init
```

This creates:

```
my_project/
├── tabletalk.yaml          # database + LLM config
├── contexts/
│   └── default_context.yaml
└── manifest/               # empty — populated by `tabletalk apply`
```

---

## 4. Configure your database

### Interactive wizard

```bash
tabletalk connect
```

Follow the prompts to select your database type and enter credentials. The connection is tested before saving.

### Import from dbt

If you already have a dbt project:

```bash
tabletalk connect --from-dbt my_dbt_project
```

This reads `~/.dbt/profiles.yml` and imports the connection automatically. See [dbt Integration](dbt-integration.md).

### Manual — edit tabletalk.yaml

For quick setup, edit `tabletalk.yaml` directly:

```yaml
# PostgreSQL
provider:
  type: postgres
  host: localhost
  port: 5432
  database: analytics
  user: analyst
  password: ${DB_PASSWORD}    # reads from environment variable

# Or reference a saved profile
# profile: my_postgres_prod
```

See [Configuration](configuration.md) for the full reference.

---

## 5. Define your first context

Edit `contexts/default_context.yaml`:

```yaml
name: sales
description: "Order processing, revenue, and product performance"
version: "1.0"

datasets:
  - name: public              # schema name in your database
    tables:
      - name: orders
        description: >-
          Customer orders. status: pending | shipped | delivered | cancelled.
          total_amount is the final charged amount.

      - name: products
        description: >-
          Product catalogue. price is current retail price.
          cost is COGS for margin analysis.
```

**The description is the most important field.** It tells the LLM what each table is *for*, not just what it contains. See [Writing Contexts](contexts.md) for best practices.

---

## 6. Compile manifests

```bash
tabletalk apply
```

This:
1. Reads every `.yaml` in `contexts/`
2. Connects to the database and introspects the live schema
3. Detects primary keys, foreign keys, and column types automatically
4. Writes `manifest/*.txt` — the schema summaries injected into the LLM prompt

You'll see output like:

```
✓ Compiled contexts/sales.yaml → manifest/sales.txt  (3 tables, 22 columns)
```

---

## 7. Start querying

### CLI (interactive session)

```bash
tabletalk query
```

```
tabletalk › sales

> What is total revenue this month?
SELECT SUM(total_amount) FROM orders
WHERE DATE_TRUNC('month', created_at) = DATE_TRUNC('month', CURRENT_DATE)

> Which products drive the most revenue?
SELECT p.name, SUM(oi.unit_price * oi.quantity) AS revenue
FROM order_items oi
JOIN products p ON p.id = oi.product_id
GROUP BY p.name ORDER BY revenue DESC LIMIT 10

> Break that down by category
...
```

To execute SQL and see results inline:

```bash
tabletalk query --execute --explain
```

### Web UI

```bash
tabletalk serve
```

Open [http://localhost:5000](http://localhost:5000). The web UI provides streaming SQL generation, auto-execution, charts, saved queries, and history. See [Web UI](web-ui.md).

---

## Try the ecommerce demo

If you want to explore tabletalk without connecting to a real database:

```bash
cd examples/ecommerce
pip install "tabletalk[duckdb]"
python seed.py
tabletalk apply
tabletalk serve
```

This spins up a DuckDB-backed ecommerce database with 4 pre-configured agents (sales, customers, inventory, marketing) and ~60 rows of seed data.

---

## Next steps

- [Writing Contexts](contexts.md) — how to write agent scopes that produce great SQL
- [Configuration](configuration.md) — full `tabletalk.yaml` reference
- [CLI Reference](commands.md) — all commands and options
