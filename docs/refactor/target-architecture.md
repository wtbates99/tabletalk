# Target architecture

## Package and contracts

```text
tabletalk/
  domain/       agent, semantics, plan, answer, evidence, verification, errors
  config/       project loading, environment resolution, secret references
  databases/    contract plus sqlite, duckdb, snowflake
  models/       one OpenAI-compatible contract plus deterministic test fake
  compiler/     load, normalize dbt, resolve, validate, canonicalize, hash
  runtime/      interpret, plan, generate, validate, execute, verify, package
  evals/        fixtures, assertions, runner, receipts
  state/        applied artifacts and promotion records
  cli/          init, connect, discover, compile, plan, eval, apply, ask, serve
  web/          structured API and trust-centered application
```

Public Python operations mirror the CLI and consume/return domain objects rather
than provider objects. Database contracts expose identity, dialect, introspection,
read-only execution, and capabilities. The model contract accepts structured
messages/schema constraints and returns structured output, usage, and exact runtime
identity. `openai-compatible` is the single primary implementation; local Ollama
uses `http://localhost:11434/v1`. Tests inject a deterministic fake explicitly.

## Deterministic lifecycle

Compiler stages load definitions, normalize dbt metadata, resolve precedence and
references, validate semantics/policies, canonicalize JSON, and hash it. Secrets
and volatile timestamps never enter artifacts. State stores the applied digest,
artifact, matching eval receipts, actor, environment, and time. Plans compare
semantic canonical forms and use stable ordering.

Runtime stages are explicit: interpretation, semantic planning, structured SQL
generation, AST validation/scope enforcement, read-only execution, answer
construction from result data, claim verification, and evidence packaging. Each
stage returns a typed result or typed failure; it cannot invoke a hidden alternate
model or local answer generator.

## Schemas

The compiled artifact contains format/version, agent identity, normalized
resources/columns/relationships/metrics/time semantics, policies, prompt contract,
required eval suites, and digest. Interpretation contains intent, metrics,
dimensions, filters, exact time range/timezone, ambiguity, and assumptions. Plans
contain ordered semantic operations and approved joins. Answers contain status,
direct answer, interpretation, evidence, SQL, data, technical details, and receipt
identity. Evidence contains source cells/rows, calculations, and claim mappings.

Migration uses new versioned artifacts beside legacy text manifests, read-only
adapters during checkpoints, and explicit upgrade errors. Compatibility code is
removed after examples, CLI, web, and state use the new domain model.
