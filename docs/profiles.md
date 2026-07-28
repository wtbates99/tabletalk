# Connection migration

The legacy global TableTalk profile store has been removed. Connections now
live in the project `tabletalk.yaml`, making the runtime target explicit and
reviewable.

Credentials should remain environment references:

```yaml
connections:
  warehouse:
    type: snowflake
    account: ${SNOWFLAKE_ACCOUNT}
    user: ${SNOWFLAKE_USER}
    password: ${SNOWFLAKE_PASSWORD}
```

Use `tabletalk connect --from-dbt PATH` to import connection structure from a
dbt project. Secret values are preserved as environment references rather than
copied into generated artifacts.
