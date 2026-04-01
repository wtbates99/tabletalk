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

### Test a profile

```bash
tabletalk profiles test analytics_prod
```

Attempts to connect to the database and reports success or failure.

### Delete a profile

```bash
tabletalk profiles delete analytics_old
```

Permanently removes the profile from `~/.tabletalk/profiles.yml` and deletes any secrets stored in the OS keychain.

---

## Credential security (keyring)

By default, passwords are stored in plaintext in `~/.tabletalk/profiles.yml`. tabletalk prints a warning when saving sensitive fields to plaintext.

**To store passwords in the OS keychain instead**, install `keyring`:

```bash
pip install keyring
# or
uv add 'tabletalk[keyring]'
```

When keyring is available, `tabletalk connect` automatically stores `password` and `credentials` fields in the OS credential store (macOS Keychain, Windows Credential Manager, or GNOME Keyring / KWallet on Linux). The YAML file stores a `__keyring__` sentinel instead of the actual secret.

```yaml
# profiles.yml with keyring enabled
analytics_prod:
  type: postgres
  host: prod-db.company.com
  database: analytics
  user: analyst
  password: __keyring__    # actual password stored in OS keychain
```

`get_profile()` transparently merges the keyring secret back at read time — no changes required in `tabletalk.yaml`.

**Fallback:** If keyring is unavailable or the OS keychain operation fails, tabletalk falls back to plaintext with a warning logged. Your workflow is never blocked.

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

- Install `keyring` to store passwords in the OS keychain rather than plaintext YAML.
- If not using keyring, restrict file permissions:

  ```bash
  chmod 600 ~/.tabletalk/profiles.yml
  ```

- For CI/CD environments, prefer inline `provider` config with `${ENV_VAR}` substitution rather than using profiles at all.

- Never commit `~/.tabletalk/profiles.yml` to version control — it lives in your home directory specifically to keep it out of project repos.
