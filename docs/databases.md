# Databases

tabletalk supports seven database backends. Install the extra for your database and configure the connection in `tabletalk.yaml`.

---

## SQLite

No extra installation required — SQLite is built into Python.

```bash
pip install tabletalk
```

**tabletalk.yaml:**

```yaml
provider:
  type: sqlite
  database_path: ./my_database.db    # relative to project root, or absolute
```

**Context `datasets[].name`:** Use `main` (SQLite's default schema name).

```yaml
datasets:
  - name: main
    tables:
      - orders
      - customers
```

**Notes:**
- Read-only databases (`.db` files you don't own) are fully supported
- In-memory databases (`:memory:`) work but are empty unless populated in the same process

---

## DuckDB

```bash
pip install "tabletalk[duckdb]"
```

**tabletalk.yaml:**

```yaml
provider:
  type: duckdb
  database_path: ./analytics.duckdb    # or :memory: for in-memory
```

**Context `datasets[].name`:** Use `main`.

```yaml
datasets:
  - name: main
    tables:
      - orders
      - products
```

**Notes:**
- DuckDB is ideal for local analytics — it reads Parquet, CSV, and JSON files natively
- Perfect for the ecommerce demo (`examples/ecommerce/`)
- Works great with dbt-duckdb projects

---

## PostgreSQL

```bash
pip install "tabletalk[postgres]"
```

**tabletalk.yaml:**

```yaml
provider:
  type: postgres
  host: localhost
  port: 5432                 # default: 5432
  database: analytics
  user: analyst
  password: ${DB_PASSWORD}
```

**Context `datasets[].name`:** Use the PostgreSQL schema name (commonly `public`).

```yaml
datasets:
  - name: public
    tables:
      - orders
      - customers
  - name: analytics
    tables:
      - daily_revenue
```

**Connection string options:**

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `host` | Yes | — | Hostname or IP |
| `port` | No | `5432` | Port |
| `database` | Yes | — | Database name |
| `user` | Yes | — | Username |
| `password` | Yes | — | Password |

**SSL/cloud connections:**

For managed Postgres (RDS, Cloud SQL, Supabase), you may need SSL. Use a profile saved via `tabletalk connect` which handles SSL automatically through the psycopg2 connection string.

---

## MySQL

```bash
pip install "tabletalk[mysql]"
```

**tabletalk.yaml:**

```yaml
provider:
  type: mysql
  host: localhost
  port: 3306                 # default: 3306
  database: my_database
  user: analyst
  password: ${DB_PASSWORD}
```

**Context `datasets[].name`:** Use the MySQL database name (same as `database` in the config above).

```yaml
datasets:
  - name: my_database
    tables:
      - orders
      - customers
```

**Connection string options:**

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `host` | Yes | — | Hostname |
| `port` | No | `3306` | Port |
| `database` | Yes | — | Database name |
| `user` | Yes | — | Username |
| `password` | Yes | — | Password |

---

## Snowflake

```bash
pip install "tabletalk[snowflake]"
```

**tabletalk.yaml:**

```yaml
provider:
  type: snowflake
  account: myorg-myaccount         # Snowflake account identifier
  user: analyst
  password: ${SNOWFLAKE_PASSWORD}
  database: ANALYTICS
  warehouse: COMPUTE_WH
  schema: PUBLIC                   # default schema (optional)
  role: ANALYST_ROLE               # optional
```

**Context `datasets[].name`:** Use the Snowflake schema name.

```yaml
datasets:
  - name: PUBLIC
    tables:
      - ORDERS
      - CUSTOMERS
```

**Connection options:**

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `account` | Yes | — | Account identifier (`org-account` or legacy `account.region`) |
| `user` | Yes | — | Username |
| `password` | Yes | — | Password |
| `database` | Yes | — | Database name (case-insensitive) |
| `warehouse` | Yes | — | Warehouse name |
| `schema` | No | `PUBLIC` | Default schema |
| `role` | No | — | Role to assume |

**Finding your account identifier:**

In Snowsight, go to **Admin → Accounts**. The account identifier is in the format `orgname-accountname`. For legacy accounts, use `accountname.region` (e.g., `xy12345.us-east-1`).

---

## BigQuery

```bash
pip install "tabletalk[bigquery]"
```

**tabletalk.yaml — service account:**

```yaml
provider:
  type: bigquery
  project_id: my-gcp-project
  credentials: /path/to/service-account.json
```

**tabletalk.yaml — application default credentials (ADC):**

```yaml
provider:
  type: bigquery
  project_id: my-gcp-project
  use_default_credentials: true
```

For ADC, authenticate first:

```bash
gcloud auth application-default login
```

**Context `datasets[].name`:** Use the BigQuery dataset ID.

```yaml
datasets:
  - name: analytics
    tables:
      - orders
      - sessions
  - name: ml_features
    tables:
      - customer_embeddings
```

**Connection options:**

| Field | Required | Description |
|-------|----------|-------------|
| `project_id` | Yes | GCP project ID |
| `credentials` | One of | Path to service account JSON key file |
| `use_default_credentials` | One of | Use ADC (set to `true`) |

---

## Azure SQL / SQL Server

```bash
pip install "tabletalk[azuresql]"
```

**tabletalk.yaml:**

```yaml
provider:
  type: azuresql
  server: myserver.database.windows.net
  database: analytics
  user: analyst
  password: ${AZURE_SQL_PASSWORD}
  port: 1433                 # default: 1433
```

**Context `datasets[].name`:** Use the SQL Server schema name (commonly `dbo`).

```yaml
datasets:
  - name: dbo
    tables:
      - orders
      - customers
```

**Connection options:**

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `server` | Yes | — | Server hostname (e.g., `myserver.database.windows.net`) |
| `database` | Yes | — | Database name |
| `user` | Yes | — | Username |
| `password` | Yes | — | Password |
| `port` | No | `1433` | Port |

---

## Type mapping

tabletalk maps database-specific types to single-letter codes used in manifests:

| Code | Meaning | Example DB types |
|------|---------|-----------------|
| `I` | Integer | `int`, `bigint`, `smallint`, `INTEGER` |
| `S` | String | `varchar`, `text`, `nvarchar`, `STRING` |
| `F` | Float | `float`, `double`, `FLOAT64` |
| `N` | Numeric/Decimal | `decimal`, `numeric`, `NUMERIC` |
| `D` | Date | `date`, `DATE` |
| `DT` | DateTime | `datetime`, `DATETIME` |
| `TS` | Timestamp | `timestamp`, `timestamptz`, `TIMESTAMP` |
| `T` | Time | `time`, `TIME` |
| `B` | Boolean | `boolean`, `bool`, `BOOL` |
| `BY` | Binary | `bytea`, `blob`, `BYTES` |
| `J` | JSON | `json`, `jsonb`, `JSON` |
| `U` | UUID | `uuid`, `uniqueidentifier` |
| `A` | Array | `ARRAY`, `[]` |
| `IV` | Interval | `interval` |
| `G` | Geography | `geography`, `GEOGRAPHY` |

---

## Using profiles for credentials

Instead of inlining credentials in `tabletalk.yaml`, save them as a profile:

```bash
tabletalk connect       # interactive wizard — saves to ~/.tabletalk/profiles.yml
```

Then reference the profile:

```yaml
profile: my_prod_snowflake
```

This keeps credentials out of your project files and lets you switch environments by changing one line. See [Profile Management](profiles.md).
