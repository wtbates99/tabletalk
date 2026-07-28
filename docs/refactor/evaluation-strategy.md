# Evaluation strategy

Evals test whether an agent answers the intended business question, not merely
whether SQL parses. Default automation uses deterministic fake models and local
fixtures; live Ollama, Ollama-hosted, Snowflake, and production-model suites are
separate and explicitly enabled.

Fixtures are small, reviewable SQLite or DuckDB datasets designed to expose wrong
metrics, date boundaries, null handling, join fan-out, undeclared resources, and
unsupported claims. Assertions include expected/forbidden SQL structure, exact or
tolerant result comparison, row/grain invariants, source and column scope,
read-only safety, interpretation fields, evidence coverage, verification status,
and expected typed failures.

Join-multiplication checks compare pre/post-join keys and aggregates at declared
grain. Model, schema, and dbt regressions compare the candidate artifact and
behavior against versioned cases. Every suite emits a receipt containing suite
digest, candidate artifact digest, runtime/model identity, fixture digest,
assertions, results, timestamps, and TableTalk version.

CI compiles twice for determinism, runs unit/integration/safety/no-fallback tests,
then required evals. Apply rejects missing, failed, stale, or digest-mismatched
receipts. Production promotion reuses the exact evaluated artifact; optional live
model checks are clearly labeled and never replace deterministic automation.
