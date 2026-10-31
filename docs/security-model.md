# Security model

This page says exactly what TableTalk enforces, what it does not, and how to
deploy it so the gaps do not matter. TableTalk is alpha software in maintenance
mode. Read the [known limitations](#known-limitations) before you point it at
real data.

## What TableTalk enforces

TableTalk checks every query the model writes before it runs. It also checks
the column names the warehouse returns before any row reaches the model, a
trace, a receipt, or an MCP client.

| Control | Where | What it does |
|---|---|---|
| SQL AST guard | `validation.py` | Parses the SQL with sqlglot. Allows exactly one read-only query statement. Rejects writes, commands, and a fixed denylist of functions. |
| Scope | `validation.py`, `agents.py` | Every table reference must resolve to a dbt resource in the agent's selector scope. Resolution happens per lexical scope, so a CTE named like an out-of-scope table cannot hide the real table. Table functions (`read_text`, `glob`, `unnest`, ...) in `FROM` are rejected. An empty scope allows nothing. |
| Columns | `validation.py` | Every column must exist on the dbt resource it reads, compared with the dialect's identifier rules. `*` and `t.*` expand to the manifest's columns. Joins need a condition. |
| Sensitive columns | `validation.py`, `semantic_guard.py` | Columns flagged `meta: {sensitive: true}` in dbt, or matched by the keyword heuristic below, are rejected anywhere in the query, including `WHERE`, `HAVING`, `QUALIFY`, `ORDER BY`, and window clauses, unless the agent lists them in `allow_sensitive`. |
| Result-column backstop | `runtime/__init__.py` | After execution, a returned column that is sensitive, or that a star query did not expand to, fails the question before the rows are used. |
| Row limit | `validation.py` | Adds `LIMIT max_rows`, or lowers an integer `LIMIT` above it (see limitation 5). |
| Scan budget | `scan_budget.py` | On Snowflake, `max_scan_bytes` blocks a query whose `EXPLAIN` estimate is over budget, and blocks it when no estimate is returned. Not available on DuckDB. |
| Timeout and tag | `connections.py`, `providers/snowflake_provider.py` | On Snowflake, each query sets `STATEMENT_TIMEOUT_IN_SECONDS` and a `QUERY_TAG`. On DuckDB and SQLite, the timeout is only a client-side wait (see below). |
| Read-only open | `connections.py` | DuckDB and SQLite files from the dbt profile are opened read-only. |

These checks are tested offline. `tabletalk/tests/test_sql_guard.py` holds 87
adversarial cases (42 must-reject, 19 must-accept, and targeted tests). They
run against sqlglot 30.14 (the lock) and were also run by hand against 30.27.

## What TableTalk does not enforce

The SQL guard is a second line of defense, not the boundary. The warehouse
identity's permissions are the boundary. The guard cannot stop a query that the
role is allowed to run and that the guard does not understand.

## Known limitations

None of these are fixed. Each one is described as far as it was checked.

1. **Function denylist, not an allowlist.** The guard rejects a fixed list of
   dangerous functions and any table function in `FROM`. Any scalar function
   not on the list is allowed. For example, the guard accepts
   `select current_setting('home_directory') from fct_orders` (checked by hand).
   Whether any allowed function leaks data on a given warehouse is not verified.
2. **No connection-level hardening.** TableTalk does not set DuckDB's
   `enable_external_access = false` (its default is `true`, checked by hand),
   and it does not create or check a read-only Snowflake role. It uses whatever
   role the dbt profile has. Not fixed.
3. **The timeout may not cancel a DuckDB or SQLite query.** On those adapters
   `ReadOnlyConnection` stops waiting after `timeout_seconds` and raises, but the
   query can keep running in a worker thread. Whether it is cancelled is not
   verified. Snowflake uses a server-side timeout, which is verified only against
   a fake connector.
4. **Sensitivity is also guessed from English keywords.**
   `semantic_guard.is_sensitive` treats a column as sensitive when its name or
   description contains words such as "email", "phone", "ssn", or "customer id".
   It misses sensitive columns named or described any other way, or in another
   language, and it can flag harmless ones. Flag columns explicitly in dbt with
   `meta: {sensitive: true}`. Not fixed.
5. **A computed `LIMIT` is not capped.** The row limit lowers only an integer
   literal. `limit 10 + 999990` runs unchanged with `max_rows: 10` (checked by
   hand), and nothing caps the rows after execution. Not fixed.

## Recommended deployment

- **Snowflake:** give TableTalk a dedicated role with `USAGE` on the warehouse,
  database, and schemas it needs and `SELECT` on only the models in the agents'
  scope. Grant no write privileges, no access to raw schemas, and no
  `IMPORTED PRIVILEGES` on shared databases. Set `max_scan_bytes` and a short
  `timeout_seconds`. Run `scripts/snowflake_check.py` against the account to
  confirm the plan estimate, timeout, and query tag work.
- **DuckDB:** point the dbt profile TableTalk uses at a dedicated DuckDB file
  that holds only the models the agents may read, separate from the file dbt
  builds into. TableTalk opens it read-only. Run TableTalk as an OS user that
  cannot read other sensitive files, because external file access is not
  disabled.
- **Everywhere:** flag sensitive columns in dbt rather than relying on the
  keyword heuristic, and keep `.tabletalk/` (traces, receipts, inference
  artifacts) on storage with the same access controls as the data itself,
  because traces hold evidence rows.
