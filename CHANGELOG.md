# Changelog

## 0.7.0 (unreleased)

TableTalk is alpha software in maintenance mode. See the README's Status
section for what is and is not verified.

### Added

- **Lock and plan.** `tabletalk lock` records what each agent is approved to see
  in `tabletalk.lock.yaml`. `tabletalk plan` shows how a dbt change alters each
  agent's scope (added, removed, and renamed columns, logic, descriptions,
  sensitivity, and tests) and which eval cases are at risk. `plan --check` exits
  5 on drift. `tabletalk approve` refreshes the lock entries it approves.
- **Replay.** Committed inference recordings let `eval run --cache-policy replay`
  and `tabletalk receipt replay` run with no model call. `make demo` and
  `make replay` run the example offline.
- **GitHub Action.** `uses: wtbates99/tabletalk@main` runs `plan` and a
  model-free eval replay on a dbt pull request and posts one comment.
- **Answer receipts.** `tabletalk.receipt/v1`, a published JSON Schema, records
  the executed SQL, evidence rows, dbt models, tests, lineage, source freshness,
  and exposures (dashboards) behind each answer, plus a replay command.
- **Snowflake controls.** `max_scan_bytes` blocks a query whose `EXPLAIN`
  estimate is over budget. Each query sets a server-side statement timeout and a
  `QUERY_TAG`. SSO (`authenticator: externalbrowser`) and key-pair auth are
  supported. `scripts/snowflake_check.py` checks these against a real account.
  All of this is tested only against a fake connector.
- **MCP server.** `tabletalk mcp` (extra `mcp`) serves only agents that match
  the lock, with a receipt on every answer. An agent whose dbt scope drifts while
  the server runs stops answering until the change is locked.
- **Score.** `tabletalk score` rates agent readiness against a fixed rubric and
  can write a shields.io badge JSON.
- **Fix.** `tabletalk fix` prints certain dbt YAML fixes (sensitive flags and
  descriptions inherited from upstream) as a patch for `git apply`.
  `fix --report` turns column-level eval failures into fixes.

### Security

- The SQL guard resolves tables per lexical scope, so a CTE named like an
  out-of-scope table no longer hides the real table.
- Identifiers are compared with each dialect's rules, so `CUSTOMER_ID` and
  `"Customer_Id"` no longer bypass the sensitive-column check.
- `*` and `t.*` expand from the manifest's columns, so a star no longer exposes
  sensitive columns.
- Columns in `HAVING`, `QUALIFY`, `ORDER BY`, and window clauses are checked.
- Table functions in `FROM` and an empty agent scope are rejected.
- The returned column names are checked before rows reach the model, a trace,
  or an MCP client.
- Known limitations that remain are listed in `docs/security-model.md`.

### Changed

- **License:** Apache-2.0. Earlier releases keep their earlier grants; see
  `LICENSE_HISTORY.md`.
- **Python 3.11 or newer** is required. CI tests 3.11 to 3.13.
- **sqlglot 30.14 or newer** is required.
- The codebase passes `mypy --strict` and `ruff format`.
- Inference fingerprints use the agent's resolved scope instead of the whole
  manifest, and are portable across checkouts. Recordings made with 0.6.0 are
  not expected to replay (not tested); record them again with
  `--cache-policy write-only`.
- Queries that 0.6.0 accepted may now be rejected: stars over sensitive
  columns, table functions, and sensitive columns in any case or position.
