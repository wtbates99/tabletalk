# Profile Management

Profiles store database connection credentials separately from your project files. This lets you:

- Keep credentials out of `tabletalk.yaml` (and out of version control)
- Switch between dev/staging/prod by changing one line
- Share project config without sharing credentials
- Import connections from existing dbt projects

Profiles are stored in `~/.tabletalk/profiles.yml` — the same pattern as `~/.dbt/profiles.yml`.

---

## Creating a profile

### Interactive wizard

```bash
tabletalk connect
```

Prompts for database type, connection details, and a profile name. Tests the connection before saving.

Example session:

```
Database type: postgres
Host: prod-db.company.com
Port [5432]:
Database: analytics
User: analyst
Password: ••••••••
Profile name [analytics]: analytics_prod

✓ Connection successful — profile saved as 'analytics_prod'
```

### Importing from dbt

```bash
tabletalk connect --from-dbt my_dbt_project
tabletalk connect --from-dbt my_dbt_project --target prod
```

Reads `~/.dbt/profiles.yml` and converts the named project's connection to tabletalk format. See [dbt Integration](dbt-integration.md) for full details.

---

## Using a profile

Reference a saved profile in `tabletalk.yaml`:

```yaml
profile: analytics_prod
```

When `profile` is set, the `provider` block is ignored. tabletalk loads the credentials from `~/.tabletalk/profiles.yml` at runtime.

---

## Managing profiles

### List all profiles

```bash
tabletalk profiles list
```

```
analytics_dev
analytics_prod
snowflake_staging
```

### Test a profile

```bash
tabletalk profiles test analytics_prod
```

Attempts to connect to the database and reports success or failure:

```
✓ analytics_prod — connected (postgres 16.1 on prod-db.company.com)
```

### Delete a profile

```bash
tabletalk profiles delete analytics_old
```

Permanently removes the profile from `~/.tabletalk/profiles.yml`.

---

## profiles.yml format

`~/.tabletalk/profiles.yml` is a YAML file you can edit directly. Each profile is a named dictionary:

```yaml
analytics_prod:
  type: postgres
  host: prod-db.company.com
  port: 5432
  database: analytics
  user: analyst
  password: mysecretpassword

snowflake_warehouse:
  type: snowflake
  account: myorg-myaccount
  user: analyst
  password: snowflakepassword
  database: ANALYTICS
  warehouse: COMPUTE_WH
  schema: PUBLIC

local_duckdb:
  type: duckdb
  database_path: /Users/will/data/analytics.duckdb
```

The profile name is the top-level key (e.g., `analytics_prod`).

---

## Profile fields by database type

### PostgreSQL

```yaml
my_postgres:
  type: postgres
  host: localhost
  port: 5432
  database: analytics
  user: analyst
  password: secret
```

### Snowflake

```yaml
my_snowflake:
  type: snowflake
  account: myorg-myaccount
  user: analyst
  password: secret
  database: ANALYTICS
  warehouse: COMPUTE_WH
  schema: PUBLIC
  role: ANALYST_ROLE          # optional
```

### DuckDB

```yaml
my_duckdb:
  type: duckdb
  database_path: /absolute/path/to/analytics.duckdb
```

### MySQL

```yaml
my_mysql:
  type: mysql
  host: localhost
  port: 3306
  database: analytics
  user: analyst
  password: secret
```

### BigQuery

```yaml
my_bigquery:
  type: bigquery
  project_id: my-gcp-project
  credentials: /path/to/service-account.json
  # or: use_default_credentials: true
```

### Azure SQL

```yaml
my_azuresql:
  type: azuresql
  server: myserver.database.windows.net
  database: analytics
  user: analyst
  password: secret
  port: 1433
```

### SQLite

```yaml
my_sqlite:
  type: sqlite
  database_path: /absolute/path/to/database.db
```

---

## Security notes

- `~/.tabletalk/profiles.yml` contains plaintext passwords. Restrict file permissions:

  ```bash
  chmod 600 ~/.tabletalk/profiles.yml
  ```

- For CI/CD environments, prefer inline `provider` config with `${ENV_VAR}` substitution rather than committing profiles.

- Never commit `~/.tabletalk/profiles.yml` to version control — it's in your home directory specifically to keep it out of project repos.
