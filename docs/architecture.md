# Architecture

TableTalk is a reliability-first lifecycle for trusted data agents as code.

```text
define → compile → plan → evaluate → apply → ask
                                      │
                                      ▼
interpret → semantic plan → generate → validate → execute → verify → evidence
```

## Contracts

- `tabletalk.domain` defines verification states, interpretations, semantic
  plans, sources, evidence, claims, receipts, and typed stage errors.
- Database implementations are limited to SQLite, DuckDB, and Snowflake.
- All model calls use one OpenAI-compatible implementation. `ollama` and
  `openai` are configuration aliases, not separate fallback engines.
- Canonical JSON uses stable field and mapping order; SHA-256 digests identify
  immutable semantic values.
- Default tests inject deterministic fake models. Live Ollama and Snowflake
  tests are opt-in.

## No-fallback invariant

A model or database stage returns either its configured result or a typed
failure. TableTalk never substitutes a different provider/model, local keyword
parser, handcrafted SQL, cached prose, model-memory answer, or demo response.
An empty model response is malformed output. A database failure cannot reach
answer construction.

## Verification

`verified` requires executed SQL, a runtime/artifact receipt, evidence, and no
unsupported material claims. Other states are `partially_verified`,
`insufficient_evidence`, `clarification_required`, and `failed`. The web and API
must present answer, verification, interpretation, evidence, SQL, data, and
technical details as distinct fields.

The complete target architecture and migration checkpoints are documented in
`docs/refactor/target-architecture.md` and `docs/refactor/migration-plan.md`.
