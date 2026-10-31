# MetricFlow spike (unmerged experiment)

**Status: an experiment that was not merged. None of this code is in TableTalk.**
This note keeps what the spike learned so nobody has to repeat it. The results
below come from the spike itself. They were not re-run when this note was
written.

## Question

Can TableTalk let the model pick dbt metrics instead of writing SQL, have
MetricFlow compile the SQL, and still run every query through TableTalk's SQL
guard?

## Versions

| Package | Version |
|---|---|
| metricflow | 0.213.0 |
| dbt-metricflow | 0.15.0 |
| dbt-core | 1.12.6 |
| dbt-duckdb | 1.11.1 |

## Setup

On the example project (`examples/dbt-analytics`), the spike added one
semantic model, one `sum` metric, and a time spine. In `models/semantic.yml`:

- a semantic model `orders` on `ref('fct_orders')`;
- a metric `recognized_revenue_total` (type `sum`);
- a `metricflow_time_spine` model built with `generate_series`, declared with
  `time_spine: {standard_granularity_column: date_day}`.

`dbt seed` must run before `dbt build`, because the models read the seed.

## Result

- `mf query --metrics recognized_revenue_total ... --explain` compiled
  deterministic SQL offline, with no warehouse query.
- TableTalk's `validate_sql` accepted the compiled SQL.
- dbt 1.12 also writes `target/osi_document.json`.

The Python API that worked:

```python
cfg = CLIConfiguration()
cfg.setup(...)  # project dir, profiles dir, target
req = MetricFlowQueryRequest.create(...)  # metrics, group-by, filters, limit
sql = cfg.mf.explain(req).sql_statement.sql
```

## Not verified

- Snowflake. The spike ran on DuckDB only.
- Metric types other than `sum` (ratio, derived, cumulative, conversion).
- Whether this API is stable. MetricFlow's Python API is not documented as
  public, and the spike pinned exact versions.
- Whether nested SQL from filtered queries (`--where`) passes the guard. The
  guard's per-scope resolution should handle it, but nobody ran it.

## Design idea, not built

1. The model picks metrics, dimensions, and filters from the agent's scope
   instead of writing SQL.
2. MetricFlow compiles them to SQL.
3. TableTalk's guard validates the compiled SQL exactly as it does model-written
   SQL. MetricFlow is never trusted to stay in scope.
4. The receipt records the metric definition (name, type, measure, and the
   semantic model's YAML digest) next to the SQL, so a reviewer can see which
   definition produced the number.

This would make the dbt semantic layer the source of metric logic, which fits
TableTalk's "no second semantic layer" rule. The cost is a heavy optional
dependency and an API that may change.
