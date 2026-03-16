# Writing Contexts

A context file defines one agent — the tables it can see and the descriptions that guide its SQL generation. This is the most important configuration in tabletalk: better descriptions produce dramatically better SQL.

---

## File format

```yaml
name: sales                                    # agent identifier (no spaces)
description: "Order processing and revenue"    # agent-level description
version: "1.0"                                 # optional, shown in manifest

datasets:
  - name: public                               # schema name in the database
    description: "Main analytics schema"       # optional schema description
    tables:
      - name: orders
        description: >-
          Customer orders. status: pending | shipped | delivered | cancelled.
          total_amount is the final charged amount after discounts.

      - name: order_items
        description: >-
          Individual line items. FK: order_id → orders.id, product_id → products.id.
          unit_price is the price at time of purchase (may differ from current price).
```

---

## Fields

### `name`

The agent's identifier. Used as the manifest filename (`manifest/<name>.txt`) and displayed in the web UI and CLI.

- No spaces — use underscores or hyphens: `sales_analyst`, `inventory-manager`
- Keep it short and descriptive

### `description`

The agent-level description becomes part of the LLM system prompt. Tell the agent what domain it covers:

```yaml
description: "Order processing, revenue analysis, and product sales performance"
```

### `version`

Optional semantic version string. Included in the manifest — useful for tracking context changes.

### `datasets`

A list of database schemas (PostgreSQL schema, MySQL database, Snowflake schema, BigQuery dataset, etc.).

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Schema/dataset name exactly as it appears in the database |
| `description` | No | Optional schema description |
| `tables` | Yes | List of tables to include |

### Tables

Each table entry under `datasets[].tables` can be:

**Simple (just the name):**
```yaml
tables:
  - orders
  - products
```

**With description:**
```yaml
tables:
  - name: orders
    description: "Customer orders with fulfillment status"
```

The description is the key to good SQL generation. See [Writing good descriptions](#writing-good-descriptions) below.

---

## Multiple datasets

A single context can span multiple schemas. This is useful when a business question requires joining across schemas:

```yaml
name: sales
description: "Revenue analysis across raw and aggregated tables"

datasets:
  - name: public
    tables:
      - name: orders
        description: "Raw transactional orders"

  - name: analytics
    tables:
      - name: daily_revenue
        description: "Pre-aggregated daily revenue by channel"
      - name: customer_cohorts
        description: "Weekly cohort retention metrics"
```

---

## Writing good descriptions

The description field is the single biggest lever on SQL quality. The LLM has no prior knowledge of your specific data — descriptions are how it learns.

### Tell the LLM what the table is *for*, not just what it contains

**Bad:**
```yaml
description: "Contains order data"
```

**Good:**
```yaml
description: >-
  Customer orders placed through the web storefront.
  status: pending | processing | shipped | delivered | cancelled | refunded.
  total_amount is the final charged amount after discounts and tax.
  Use created_at for time-series analysis.
```

### Document non-obvious column semantics

If a column name is ambiguous or the data has quirks, put that in the description:

```yaml
- name: subscriptions
  description: >-
    Active and cancelled subscription records. One row per subscription period —
    a customer can have multiple rows if they cancelled and resubscribed.
    status: active | trialing | cancelled | past_due | unpaid.
    current_period_end is the next billing date for active subs.
    Do NOT filter on deleted_at — use status = 'cancelled' instead.
```

### Name foreign key relationships explicitly

tabletalk auto-detects FKs via database introspection, but explicit description helps the LLM choose correct JOIN directions:

```yaml
- name: order_items
  description: >-
    Line items within an order. FK: order_id → orders.id, product_id → products.id.
    unit_price is the price at time of purchase (may differ from products.price).
    Use quantity * unit_price for revenue calculations on this table.
```

### Specify useful grouping and segmentation dimensions

```yaml
- name: customers
  description: >-
    Registered customer accounts. city and country can be used for geographic
    segmentation. plan_type: free | starter | pro | enterprise.
    Use signup_date for cohort analysis. lifetime_value is updated nightly.
```

### Document status enums

Whenever a column has a fixed set of values, list them:

```yaml
description: "order status values: pending | shipped | delivered | cancelled | returned"
```

---

## Choosing table scope

**Include only tables relevant to the agent's domain.** A focused agent with 5 tables will outperform a broad agent with 30. When in doubt:

- One context per business function: sales, inventory, marketing, finance, support
- Shared lookup tables (customers, products) can appear in multiple contexts
- Avoid including large tables the agent will never need for its domain questions

**Example — good scope separation:**

```yaml
# contexts/sales.yaml
# Access: orders, order_items, customers, products, categories

# contexts/inventory.yaml
# Access: inventory, products, categories, warehouses

# contexts/marketing.yaml
# Access: campaigns, campaign_conversions, customers
```

---

## Complete example

```yaml
name: customers
description: "Customer profiles, acquisition, account status, and lifetime value"
version: "2.1"

datasets:
  - name: public
    description: "Production customer database"
    tables:
      - name: customers
        description: >-
          One row per registered customer account.
          active: TRUE = account in good standing, FALSE = churned or suspended.
          lifetime_value is the sum of all completed order totals, updated nightly.
          Use city/country for geographic segmentation.
          Use signup_date for acquisition cohort analysis.

      - name: subscriptions
        description: >-
          Active and historical subscription records. One row per subscription period.
          FK: customer_id → customers.id.
          status: active | trialing | cancelled | past_due | unpaid.
          current_period_end is the next billing date for active subscriptions.
          A customer can have multiple rows if they have resubscribed.

      - name: customer_events
        description: >-
          Behavioural event log. FK: customer_id → customers.id.
          event_type: login | page_view | feature_used | export | api_call.
          Use for engagement and activity analysis. High volume — always filter
          by customer_id or date range, never do unfiltered full scans.
```

---

## Validating your context

After editing a context, run `tabletalk apply` and inspect the generated manifest:

```bash
tabletalk apply
cat manifest/customers.txt
```

Check that:
- All expected tables appear
- Column types look correct
- FK relationships are detected (`[FK:table.column]`)
- PK columns are marked (`[PK]`)

If a table is missing, confirm the `datasets[].name` matches the actual schema name in your database.
