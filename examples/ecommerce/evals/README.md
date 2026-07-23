# Ecommerce eval fixture

This suite sends every question through the configured Ollama model, executes
its generated SQL against a fixed DuckDB fixture, and verifies the resulting
trace. The fixture contains 2,500 customers, 100 products, 12,003 orders,
roughly 36,000 line items,
refunds, null dimensions, UTC boundary rows, customers with no orders, and a
sensitive table that is deliberately absent from the agent manifest.

Build the fixture:

```bash
cd examples/ecommerce
python evals/seed_fixture.py
```

Run the suite with the Ollama-backed LLM configured in `../tabletalk.yaml`
(`gemma4:31b-cloud` by default):

```bash
tabletalk eval run evals/sales_regression.yaml --project-folder .
```

Write CI output:

```bash
tabletalk eval run evals/sales_regression.yaml \
  --project-folder . \
  --format junit \
  --output eval-results.xml \
  --minimum-score 0.90 \
  --fail-on-safety-violation
```

The fixture path and manifest are resolved relative to the suite YAML, so the
command can be launched from any working directory.
