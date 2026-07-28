# Refactor progress audit

Audit date: 2026-07-27.

## Implemented and locally verified

- First-class Agent and EvalSuite YAML resources.
- SQLite, DuckDB, and Snowflake are the only database adapters in the public
  product surface.
- Free local development defaults to Ollama with
  `gemma4:31b-cloud`; production accepts explicitly configured compatible
  models.
- Missing or failed models and databases never trigger heuristic, cached, or
  alternate-provider answers.
- Deterministic canonical compilation, content-addressed artifacts, semantic
  plans, eval receipts, and atomic applied state.
- Strict applied-artifact runtime with structured interpretation, semantic
  plan, parsed SQL scope enforcement, database evidence, reproducible
  calculations, claim grounding, verification checks, and typed failures.
- Read-only query policy, row limits, timeouts, approved joins, relation and
  column scope, and single-query AST validation.
- Focused CLI and trust-centered web UI.
- dbt manifest normalization and artifact fingerprinting.
- SQLite starter, DuckDB analytics, and Snowflake production examples.
- Legacy providers and partial cache, memory, profile, registry, routing,
  scheduling, remote-state, and generic-tool systems removed.

The default test suite is deterministic and does not require network access.
The live Ollama/Gemma smoke test is opt-in.

Current verification: 246 tests pass and 3 environment-dependent tests skip;
Ruff, mypy, JavaScript syntax, warning-free Sphinx documentation, wheel/sdist
build, and isolated wheel installation pass. Production Python changed from
8,463 to 7,743 lines; test Python changed from 5,892 to 5,022 lines. Declared
core dependencies changed from 7 to 6, and database feature dependencies from
9 to 2; the 7 development-tool declarations remain.

## Environment-dependent verification

- Snowflake execution needs a user-supplied account and least-privilege
  credentials; it cannot be proven in an offline local test environment.
- The live Gemma check needs an authenticated Ollama daemon.
- Licensing remains CC BY-NC 4.0 because repository ownership and third-party
  asset provenance have not been established sufficiently to authorize a
  license change.

These limitations are surfaced rather than treated as successful verification.
