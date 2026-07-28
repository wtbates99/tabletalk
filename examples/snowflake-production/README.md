# Snowflake production

This example shows a source-controlled Agent, environment-only credentials,
local DuckDB regression fixtures, eval-gated application, and an explicitly
configured OpenAI-compatible production model endpoint.

Use a dedicated Snowflake role with only the database, schema, warehouse, and
relation privileges the Agent needs. TableTalk AST validation is defense in
depth; Snowflake permissions remain the final security boundary.

```bash
export SNOWFLAKE_ACCOUNT=...
export SNOWFLAKE_USER=...
export SNOWFLAKE_PASSWORD=...
export TABLETALK_LLM_BASE_URL=...
export TABLETALK_LLM_API_KEY=...
export TABLETALK_LLM_MODEL=...

tabletalk compile sales
tabletalk plan sales --detailed-exit-code
tabletalk apply sales --auto-approve
tabletalk ask sales "What drove recognized revenue last week?" --format json
```

The default test suite does not require Snowflake credentials. Run live
Snowflake integration checks only in a protected CI environment with a
read-only role.
