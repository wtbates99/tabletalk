# Ecommerce eval fixture

This suite exercises TableTalk against a deterministic DuckDB fixture with
2,500 customers, 100 products, 12,003 orders, roughly 36,000 line items,
refunds, null dimensions, UTC boundary rows, customers with no orders, and a
sensitive table that is deliberately absent from the agent manifest.

Build the fixture:

```bash
cd examples/ecommerce
python evals/seed_fixture.py
```

Run the suite with the Ollama-backed LLM configured in `../tabletalk.yaml`
(`qwen2.5-coder:7b` by default):

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
