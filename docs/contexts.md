# Agent resources

Legacy context files have been replaced by first-class `kind: Agent` resources
under `agents/`.

```yaml
kind: Agent
version: "1"
name: revenue
description: Answers governed revenue questions.
connection: warehouse
owner: analytics
relations:
  include: [analytics.orders]
  exclude: [analytics.orders_private]
semantics:
  metrics:
    recognized_revenue:
      expression: sum(recognized_revenue)
      relation: analytics.orders
      time_dimension: analytics.orders.order_date
  relationships: []
  rules:
    - Exclude cancelled orders unless explicitly requested.
policies:
  read_only: true
  require_evidence: true
  max_rows: 500
  timeout_seconds: 30
evals: [revenue_regression]
```

Relation patterns must be schema-qualified. Compilation rejects empty or
ambiguous scopes. Policies cannot disable read-only execution or required
evidence.
