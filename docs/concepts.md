# Core Concepts

Understanding tabletalk's model takes about two minutes if you already know dbt or Terraform.

---

## The analogy

tabletalk is deliberately modelled on the dbt workflow:

| tabletalk | dbt | Terraform |
|-----------|-----|-----------|
| `contexts/*.yaml` | `sources.yml` | `resource "agent" {}` |
| `manifest/*.txt` | `manifest.json` | `.tfstate` |
| `tabletalk apply` | `dbt compile` | `terraform apply` |
| `tabletalk query` | `dbt run` | agent is live |

The idea: **define what data an agent can see, compile it, deploy it.** Redeploy any time your schema changes.

---

## Contexts

A **context** is a YAML file in `contexts/` that defines one agent — the tables it can see and human descriptions that become part of its system prompt.

```yaml
# contexts/sales.yaml
name: sales
description: "Order processing, revenue, and product analysis"
version: "1.0"

datasets:
  - name: public              # database schema / dataset name
    tables:
      - name: orders
        description: "Customer orders with status and totals"
      - name: products
        description: "Product catalogue with pricing"
```

One context file = one agent. You can have as many as you want. A large analytics database might have a dozen — one per business domain.

### Why scoped agents are better than one big agent

- **Accuracy** — the LLM only sees tables relevant to the question. Noise hurts SQL quality.
- **Safety** — a marketing analyst agent can't query payroll tables.
- **Performance** — smaller prompts are faster and cheaper.

---

## Manifests

When you run `tabletalk apply`, tabletalk introspects the live database and compiles each context into a **manifest** — a compact text file in `manifest/` that summarises the schema.

```
DATA_SOURCE: postgres - Production analytics database
CONTEXT: sales - Order processing, revenue, and product analysis (v1.0)
DATASET: public - Main schema
TABLES:
public.orders|Customer orders with status and totals|id:I[PK]|customer_id:I[FK:customers.id]|status:S|total_amount:N|created_at:TS
public.products|Product catalogue with pricing|id:I[PK]|name:S|price:N|cost:N|category_id:I[FK:categories.id]
```

This compact notation fits substantial schemas into the LLM context window efficiently. The format encodes:

- Table name and description
- Every column with its type code (`I`=Integer, `S`=String, `N`=Numeric, `D`=Date, `TS`=Timestamp, `B`=Boolean, etc.)
- Primary keys (`[PK]`)
- Foreign keys (`[FK:table.column]`) — the LLM uses these to construct JOINs

The manifest is what gets injected into the system prompt. The LLM never sees raw `CREATE TABLE` statements — it sees the pre-processed manifest.

---

## The deploy lifecycle

```
Edit context YAML  →  tabletalk apply  →  tabletalk query / serve
       ↑                                          │
       └──────────── schema changed? ─────────────┘
```

1. **Define** — write `contexts/sales.yaml`, declare which tables the agent can see and add descriptions
2. **Compile** — `tabletalk apply` introspects the DB, merges your descriptions with the live schema, writes `manifest/sales.txt`
3. **Deploy** — `tabletalk query` or `tabletalk serve` loads the manifest and starts accepting questions
4. **Redeploy** — if the schema changes, edit the context and re-run `tabletalk apply`. The agent picks up the new schema immediately.

tabletalk warns you when a context is stale (the YAML is newer than the manifest):

```bash
tabletalk apply
⚠ contexts/sales.yaml has changed since last apply — recompiling
```

---

## Agents

An **agent** in tabletalk is simply a loaded manifest + an LLM session. When you select a manifest in `tabletalk query` or the web UI, you're selecting an agent.

Agents are stateless across restarts — conversation history is stored on disk in `.tabletalk_history.jsonl`. Within a session, agents maintain **multi-turn context**: follow-up questions like "break that down by category" work because the conversation history is included in each LLM call.

---

## Profiles

A **profile** is a saved database connection stored in `~/.tabletalk/profiles.yml`. Profiles decouple credentials from project config — the same project can be pointed at dev or prod just by changing `profile: my_prod_snowflake` in `tabletalk.yaml`.

Profiles work like `~/.dbt/profiles.yml` and can be imported directly from dbt. See [Profile Management](profiles.md).

---

## Safe mode

Setting `safe_mode: true` in `tabletalk.yaml` restricts the agent to `SELECT` queries only. Any attempt to run `DELETE`, `UPDATE`, `DROP`, or `INSERT` raises an error before it reaches the database. Recommended for any agent connected to a production database. See [Safe Mode](safe-mode.md).

---

## Key files

| File | Purpose |
|------|---------|
| `tabletalk.yaml` | Project config — database connection, LLM, paths |
| `contexts/*.yaml` | Agent definitions — one file per agent |
| `manifest/*.txt` | Compiled schema summaries — auto-generated, don't edit |
| `~/.tabletalk/profiles.yml` | Saved database connections |
| `.tabletalk_history.jsonl` | Query history (per project directory) |
| `.tabletalk_favorites.json` | Saved queries (per project directory) |
