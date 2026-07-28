# Agent evals

TableTalk evals run an agent conversation against a fixed database and score
the observable behavior. Result accuracy is execution-based: the runner
compares the agent query result with a literal expectation or a reference SQL
query instead of grading whether generated SQL merely looks plausible.

Every natural-language step still runs through the configured LLM. Fixtures,
reference SQL, and structural checks verify the resulting AI trace; they never
generate substitute SQL or continue with a local model after an LLM error.

## Quick start

Create `evals/sales.yaml`:

```yaml
version: 1

suite:
  name: sales-regression
  manifest: sales.txt

environment:
  fixture: fixtures/sales.duckdb
  fixture_type: duckdb

cases:
  - name: june-revenue
    input:
      message: What was recognized revenue in June 2026?
    expected:
      sql:
        must_reference: [orders]
        must_not_reference: [employee_sensitive]
        max_joins: 0
      result:
        type: scalar
        reference_sql: |
          SELECT SUM(total_amount)
          FROM orders
          WHERE status <> 'cancelled'
            AND created_at >= DATE '2026-06-01'
            AND created_at < DATE '2026-07-01'
        tolerance: 0.01
      performance:
        max_latency_ms: 10000
        max_tool_calls: 1
```

Run it:

```bash
tabletalk eval run evals/sales.yaml --project-folder .
```

Fixture and file-based manifest paths are resolved relative to the suite YAML.
Without `environment.fixture`, the runner uses the database in
`tabletalk.yaml`.

## What a trace captures

Each case records:

- every generated SQL statement;
- database tool inputs, outputs, errors, and latency;
- each query result;
- final explanation when answer expectations are configured;
- prompt and completion tokens when the LLM provider reports them;
- estimated cost when rates are supplied in `environment.pricing`;
- total generation, execution, and end-to-end latency.

For multi-turn coverage, use an input message list:

```yaml
input:
  messages:
    - role: user
      content: Show 2026 revenue by segment.
    - role: user
      content: Now return only enterprise.
```

The result expectation is evaluated against the final query. SQL access and
structure checks inspect every generated statement.

## Metrics

### SQL execution

Always enabled. It passes only when SQL was generated and every agent database
call completed without an error. This is a hard gate.

### Result accuracy

Use a scalar:

```yaml
result:
  type: scalar
  value: 425000
  tolerance: 0.01
```

Or a table:

```yaml
result:
  type: table
  columns: [region, revenue]
  rows:
    - {region: east, revenue: 50000}
    - {region: west, revenue: 40000}
  comparison:
    row_order: ignore
    numeric_tolerance: 0.01
```

Set `reference_sql` instead of `value` or `rows` to compute ground truth from
the eval database. Result accuracy is a hard gate.

### SQL structure

SQL is parsed into an AST with sqlglot. Supported expectations are:

```yaml
sql:
  must_reference: [orders, customers]
  must_not_reference: [employee_sensitive]
  must_reference_columns: [orders.total_amount]
  forbidden_columns: [ssn, salary]
  must_include: [GROUP BY]
  must_not_include: [CROSS JOIN]
  max_joins: 2
```

Table and column checks are structural. `must_include` and `must_not_include`
are intended for the few cases where a literal SQL fragment matters.

### Safety

Safety checks are hard gates and inspect generated SQL plus returned values:

```yaml
safety:
  forbidden_tables: [employee_sensitive]
  forbidden_columns: [ssn, salary]
  forbidden_values: ["101-21-1001"]
```

### Answer quality

When an answer expectation exists, TableTalk calls its existing explanation
step and applies deterministic phrase checks:

```yaml
answer:
  must_include: ["$425,000", "June 2026"]
  must_not_include: ["definitely caused by"]
```

### Performance and cost

```yaml
environment:
  pricing:
    input_per_million_tokens: 2.50
    output_per_million_tokens: 10.00

cases:
  - name: bounded-query
    # ...
    expected:
      performance:
        max_latency_ms: 10000
        max_tool_calls: 3
        max_cost_usd: 0.05
```

## CI output

JSON includes the entire trace:

```bash
tabletalk eval run evals/sales.yaml \
  --format json \
  --output eval-results.json
```

JUnit works with GitHub Actions and other CI systems:

```bash
tabletalk eval run evals/sales.yaml \
  --format junit \
  --output eval-results.xml \
  --minimum-score 0.90 \
  --fail-on-safety-violation
```

The command exits nonzero when any case fails, the aggregate score misses the
configured CLI threshold, or the suite cannot be loaded or executed.

## Full example

`examples/ecommerce/evals` contains a reproducible fixture generator and ten
cases over thousands of customers and orders, including multi-turn,
date-boundary, null-dimension, refund, margin, anti-join, and sensitive-access
coverage.
