# Deletion plan

Delete only after replacement checkpoints are tested.

| Current surface | Decision | Replacement / reason |
|---|---|---|
| PostgreSQL, MySQL, BigQuery, Azure SQL providers and tests/docs/extras | delete | Focus on reliable SQLite, DuckDB, Snowflake |
| Anthropic-specific provider and dependency | delete | One OpenAI-compatible model contract |
| `router.py` and fast-model routing | delete | No heuristic or implicit model substitution |
| schedules, agent registry, webhooks | defer/delete | Partial fleet features outside the reliable core |
| S3/GCS state backends and dependencies | defer/delete | Canonical local state first; redesign shared state later |
| cache and cached-query web routes | delete | Avoid stale/ambiguous answer behavior |
| favorites, stats, legacy `/query`, duplicate query routes | simplify/delete | One structured invocation API |
| text `manifest/*.txt` artifacts | replace | Versioned canonical JSON compiled artifacts |
| `.tabletalk_history.jsonl` and ad-hoc audit files | replace | Structured secret-safe invocation receipts |
| `memory.py`, generic tools, cost tables | delete/defer | Not core to trusted database answers |
| `validate`, `diff`, `test`, `lint`, `check`, `lock`, `rollback`, `promote`, `watch`, `openapi` commands | consolidate | Focused compile/plan/eval/apply lifecycle |
| current broad docs and single ecommerce demo | replace | SQLite, DuckDB, Snowflake journeys |
| Flask UI organized around SQL chat | rebuild | Trust-centered answer structure |

Compatibility breaks include provider removal, configuration migration,
versioned artifact/state formats, CLI renames, and API response changes. Risks are
existing users of removed adapters, unrecoverable legacy state, and hidden scripts.
Mitigate with a versioned migration guide, artifact backup, configuration
diagnostics, and one release of explicit legacy-format errors—not silent fallback.
