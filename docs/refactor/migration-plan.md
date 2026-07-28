# Migration plan

1. Freeze the product, reliability, evaluation, architecture, deletion, and
   licensing decisions in this directory.
2. Introduce versioned domain types, typed errors, verification statuses, and
   canonical serialization without changing legacy invocation.
3. Add the single OpenAI-compatible model implementation, Ollama Cloud Free
   default, documented local Ollama option, deterministic fake, explicit failure
   tests, and opt-in live suites.
4. Build canonical agent compiler/artifact alongside legacy text manifests and
   prove deterministic compilation.
5. Normalize a narrow dbt manifest input and semantic precedence.
6. Add structured interpretation and deterministic semantic plans.
7. Implement scoped AST validation, read-only execution, evidence packaging,
   and claim verification for SQLite, then DuckDB and Snowflake.
8. Bind eval receipts to candidate digests and gate apply.
9. switch the focused CLI and public Python API to versioned artifacts.
10. Rebuild the HTTP API and web application around structured answer cards.
11. Add complete SQLite, DuckDB, and Snowflake examples and run walkthroughs.
12. Remove deferred providers, legacy formats, commands, routes, dependencies,
    docs, and compatibility code; build and install cleanly.
13. Make only ownership-supported licensing changes after legal review of the
    licensing audit.

Each checkpoint keeps formatter, linter, type checker, unit tests, local database
integrations, package build, and applicable example smoke tests green. Destructive
format removal waits until migration diagnostics and backup instructions exist.
