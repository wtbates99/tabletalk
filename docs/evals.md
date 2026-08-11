# Evaluation and reports

An eval is a versioned test case that measures agent behavior. A trial is one
execution of one eval case with one fixed configuration.

## Eval content

Each generated case states its protected behavior, generation reason, source
evidence, determinism, warehouse-data dependency, category, and failure rule.

```yaml
schema_version: tabletalk.eval/v1
name: revenue-capability
agent: revenue
kind: capability
trials: 3
budgets:
  latency_ms: 120000
  total_tokens: 12000
cases:
  - name: july-recognized-revenue
    kind: regression
    question: What was recognized revenue in July 2026?
    protects: The approved measure and recognition date.
    reason: Finance approved the reference query.
    deterministic: true
    warehouse_data: true
    failure: The result differs from the reference query.
    expect:
      reference: references/july-recognized-revenue.sql
      result:
        comparison: scalar
        tolerance: 0.01
      models:
        required: [model.analytics.fct_orders]
      columns:
        required: [recognized_revenue, order_date]
```

Case categories are regression, capability, safety, ambiguity, and refusal.
Suite categories are regression and capability.

## Hard checks

Hard checks cover read-only execution, SQL scope, resource and column use,
expected refusal, result values, result shape, evidence-linked claims, and
configured budgets. Budget checks fail when the provider does not report the
required measure.

Result comparison modes are `scalar`, `ordered`, `ordered_values`, `unordered`,
and `keyed`. A keyed comparison requires keys. A case may use a project-relative
reference file, inline reference SQL, or literal rows. It must not combine
ground-truth sources.

## Inference and replay

TableTalk fingerprints the question, resolved agent, manifest, semantic
knowledge, model configuration, prompt version, runtime version, warehouse
identity, execution limits, and trial number.

The normal cache policy reads and writes immutable artifacts. Read-only policy
reads but does not write. Write-only policy always requests inference and writes
the result. Replay reads only and fails on a miss. Disabled policy does not use
the store.

Artifacts store successful traces and typed non-answer outcomes. A typed outcome
is an ambiguity request, rejection, or provider failure. Replayed ambiguity and
rejection cases keep their original latency, token, retry, and cost measures.

Metrics and reports load saved result records. They can change without another
model request.

## Statistics and baselines

Reports include a Wilson confidence interval for pass rate. A paired baseline
comparison uses McNemar's exact test for binary outcomes. It uses a Wilcoxon
signed-rank test for paired latency values. The report includes effect sizes and
intervals. It warns when fewer than 20 paired observations exist.

An approved baseline binds exact report bytes. A failed regression case requires
an explicit known-failure record. TableTalk never creates a passing baseline by
weakening an expectation.

## Failure diagnosis

Every deterministic diagnosis contains the failed stage, evidence, corrective
action, and likely owner. Owners are dbt metadata, semantic knowledge, agent
definition, eval definition, runtime code, or warehouse.

The stable taxonomy includes unsupported questions, ambiguity, clarification,
model and column selection, joins, filters, time boundaries, aggregation and
grain, unsafe access, SQL validation, execution, result mismatch, unsupported
claims, missing output, budget violations, flaky outcomes, and invalid ground
truth.
