# DuckDB analytics

This example demonstrates a multi-relation analytical Agent, declared join
paths, a semantic revenue metric, and an eval fixture designed to catch join
multiplication.

```bash
uv run python seed.py
ollama signin
ollama pull gemma4:31b-cloud
tabletalk compile revenue
tabletalk plan revenue
tabletalk apply revenue
tabletalk ask revenue \
  "What was recognized revenue by product category in January 2026?"
```

The production database opens read-only. Evals use a fresh isolated DuckDB
fixture, so regression runs cannot mutate the development database.
