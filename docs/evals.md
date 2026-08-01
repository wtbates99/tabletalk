# Execution-based evals

Suites may declare `kind: regression|capability`, a human-readable `description`, and `trials: 1..20`.
Regression suites are expected to remain nearly perfect; capability suites hold harder tasks with room
to improve. Repeated trials are persisted separately, all must pass for the command to succeed, and the
terminal reports aggregate trial pass rate.

Create evals interactively with `tabletalk eval create AGENT`. The default is the generated SQL the
user just reviewed, so changing warehouse data is compared by executing candidate and golden queries
against the same snapshot. The proposed case runs immediately and automated authoring refuses to save
an already-failing case. Literal rows remain available for stable fixtures and CI.

```yaml
name: revenue-regression
agent: revenue
cases:
  - name: july-revenue
    question: What was recognized revenue in July 2026?
    expect:
      reference_sql: |
        select sum(recognized_revenue) as recognized_revenue
        from {{ ref('fct_orders') }}
        where order_date >= '2026-07-01'
          and order_date < '2026-08-01'
      result:
        comparison: scalar
        tolerance: 0.01
      models:
        required: [model.analytics.fct_orders]
      columns:
        required: [recognized_revenue, order_date]
```

Result modes are `scalar`, `ordered`, `ordered_values`, `unordered`, and `keyed`; keyed comparisons
require `keys`. `ordered_values` deliberately ignores presentation-only aliases while preserving row
and value order.
Expectations may also include literal `value` or `rows`, `row_count`, exact result `columns`, forbidden
models/columns, `allow_extra_columns: true` for harmless additional evidence fields, and
`outcome: ambiguity|rejection`. Reference SQL supports only dbt `ref()` templating,
is parsed with the same read-only scope validator, and executes against the same warehouse snapshot.

Hard gates are execution, SQL safety/scope, expected models/columns, expected rejection, result
comparison and tolerance, shape/count, and evidence-linked claims. Latency and token usage are recorded
but informational. Empty literal row sets are enforced rather than treated as a missing expectation.
Results are written to `.tabletalk/eval-results/AGENT` and failures exit with code 3.

Live correctness uses exact normalized question matching, not semantic guesswork. A covered question is
`VERIFIED` only when every hard gate passes. Uncovered questions still return their inspectable trace,
but are labeled `UNVERIFIED` and exit with code 4.
