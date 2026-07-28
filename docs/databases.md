# Databases

TableTalk supports SQLite, DuckDB, and Snowflake.

```yaml
connections:
  sqlite_local:
    type: sqlite
    database_path: ./data.db
    read_only: true

  duckdb_local:
    type: duckdb
    database_path: ./analytics.duckdb
    read_only: true

  snowflake_prod:
    type: snowflake
    account: ${SNOWFLAKE_ACCOUNT}
    user: ${SNOWFLAKE_USER}
    password: ${SNOWFLAKE_PASSWORD}
    database: ANALYTICS
    warehouse: COMPUTE_WH
    schema: PUBLIC
    role: TABLETALK_READER
```

SQLite file connections default to URI read-only mode. DuckDB receives its
native read-only option. Snowflake should use a least-privilege read role.
TableTalk also parses every statement and enforces the applied Agent's scope,
but application validation is not a substitute for database permissions.

Relative database paths resolve from the project directory. Connection secrets
may be environment references and are never embedded in canonical artifacts or
applied state.

Use `tabletalk connections test NAME` to validate access and
`tabletalk discover` to inspect visible relations before authoring an Agent.
