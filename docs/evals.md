# EvalSuites

EvalSuites are versioned YAML resources executed against an exact candidate
Agent artifact.

```yaml
kind: EvalSuite
name: revenue_regression
agent: revenue
fixture:
  type: duckdb
  setup:
    - fixtures/schema.sql
    - fixtures/data.sql
cases:
  - name: january_revenue
    question: What was revenue in January 2026?
    expected_interpretation:
      metric: revenue
      start_date: "2026-01-01"
      end_date: "2026-02-01"
      timezone: UTC
    expect:
      relations:
        required: [main.orders]
      columns:
        required: [recognized_revenue, order_date]
      joins:
        max: 0
      reference_sql: |
        SELECT sum(recognized_revenue) AS revenue
        FROM main.orders
        WHERE order_date >= '2026-01-01'
          AND order_date < '2026-02-01'
      result:
        comparison: scalar
        absolute_tolerance: 0.01
      answer:
        require_supported_claims: true
        require_evidence: true
```

Supported result modes include scalar, table, ordered rows, unordered rows,
keyed rows, approximate numeric rows, empty results, and shape checks. SQL
expectations can require or forbid relations and columns and constrain joins.
Budgets can constrain runtime observations.

```bash
tabletalk eval
tabletalk eval revenue
tabletalk eval --format json
tabletalk eval --format junit
```

An eval invokes the configured model; fixtures and reference SQL judge its
behavior and never generate a substitute answer. Passing receipts bind the
suite source digest, candidate artifact digest, Agent, runtime identity, and
observed result. A changed suite or artifact invalidates the receipt.

`tabletalk apply` runs required suites and refuses to update state if any suite
is absent, failing, stale, or tampered with.

See the three projects under `examples/` for complete fixtures, including the
DuckDB join-multiplication regression.
