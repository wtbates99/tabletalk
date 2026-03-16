# CLI Reference

All tabletalk functionality is available through the `tabletalk` command.

```
Usage: tabletalk [OPTIONS] COMMAND [ARGS]...

Options:
  --verbose   Enable debug logging
  --help      Show this message and exit

Commands:
  init      Scaffold a new tabletalk project
  apply     Introspect DB and compile agent manifests
  query     Start an interactive agent session
  serve     Launch the web UI
  connect   Save a database connection profile
  profiles  Manage saved connection profiles
  history   View recent query history
```

---

## `tabletalk init`

Scaffold a new project in the current directory.

```bash
tabletalk init
```

Creates:
- `tabletalk.yaml` — template config with comments
- `contexts/default_context.yaml` — sample context definition
- `manifest/` — empty output directory

Run this once per project. Edit `tabletalk.yaml` to configure your database and LLM, then define your agents in `contexts/`.

---

## `tabletalk apply`

Introspect the database and compile context definitions into manifests.

```bash
tabletalk apply [DIR]
```

**Arguments:**

| Argument | Default | Description |
|----------|---------|-------------|
| `DIR` | `.` (current directory) | Path to the project directory containing `tabletalk.yaml` |

**What it does:**

1. Reads `tabletalk.yaml`
2. Connects to the database
3. For each `contexts/*.yaml`:
   - Introspects each declared table (columns, PKs, FKs, types)
   - Merges your descriptions with the live schema
   - Writes `manifest/<name>.txt` in compact schema notation
4. Reports table counts and warns about stale contexts

**Examples:**

```bash
tabletalk apply                   # compile current directory
tabletalk apply ./my_project      # compile a specific project
tabletalk apply --verbose         # show debug output
```

Manifests are regenerated every time you run `apply`. If `tabletalk.yaml` or a context file has changed since the last apply, tabletalk warns you.

---

## `tabletalk query`

Start an interactive agent session in the terminal.

```bash
tabletalk query [DIR] [OPTIONS]
```

**Arguments:**

| Argument | Default | Description |
|----------|---------|-------------|
| `DIR` | `.` | Path to the project directory |

**Options:**

| Option | Description |
|--------|-------------|
| `--execute` | Execute the generated SQL and display results as a table |
| `--explain` | After execution, stream a plain-English explanation of the results (requires `--execute`) |
| `--output FILE` | Save query results to a CSV file (requires `--execute`) |
| `--no-context` | Disable multi-turn conversation — each question is independent |

**Examples:**

```bash
tabletalk query                              # basic SQL generation
tabletalk query --execute                    # generate + run
tabletalk query --execute --explain          # generate + run + explain
tabletalk query --execute --output data.csv  # save results to CSV
tabletalk query --no-context                 # single-turn mode
tabletalk query ./my_project                 # use a specific project
```

### Session commands

Once inside the query session, these special inputs are available:

| Input | Action |
|-------|--------|
| Any question | Generate SQL (streamed token-by-token) |
| `change` | Switch to a different manifest/agent |
| `history` | Display recent queries for this session |
| `clear` | Clear conversation context (start fresh multi-turn session) |
| `exit` or `quit` | Exit the session |
| `Ctrl+C` | Exit immediately |

### Multi-turn context

By default, the agent maintains conversation context across questions. Follow-up questions work naturally:

```
> What is total revenue this month?
SELECT SUM(total_amount) FROM orders WHERE ...

> Break that down by product category
SELECT c.name, SUM(...) ... JOIN products ... JOIN categories ...

> Only show categories with revenue over $10,000
SELECT ... HAVING SUM(...) > 10000
```

Use `clear` to reset context, or `--no-context` to disable it entirely.

---

## `tabletalk serve`

Launch the web UI.

```bash
tabletalk serve [DIR] [OPTIONS]
```

**Arguments:**

| Argument | Default | Description |
|----------|---------|-------------|
| `DIR` | `.` | Path to the project directory |

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--port INTEGER` | `5000` | Port to listen on |
| `--debug` | `false` | Enable Flask debug mode with auto-reload |

**Examples:**

```bash
tabletalk serve                    # http://localhost:5000
tabletalk serve --port 8080        # http://localhost:8080
tabletalk serve --debug            # auto-reload on file changes
tabletalk serve ./my_project       # serve a specific project
```

The web UI starts a Flask server. All features (streaming, execution, explanation, history, favorites) are accessible from the browser. See [Web UI](web-ui.md).

**Production note:** The built-in Flask server is not suitable for production traffic. For production deployment, use a WSGI server (gunicorn, uWSGI) behind a reverse proxy. Set `TABLETALK_SECRET_KEY` to a random string for session security.

---

## `tabletalk connect`

Save a database connection profile interactively or import from dbt.

```bash
tabletalk connect [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--from-dbt PROJECT` | Import from `~/.dbt/profiles.yml` instead of running the wizard |
| `--target TARGET` | dbt target to import (default: `dev`) |
| `--test-only` | Test a connection without saving it |

### Interactive wizard

```bash
tabletalk connect
```

Prompts for:
1. Database type (postgres, snowflake, duckdb, mysql, sqlite, bigquery, azuresql)
2. Connection details (host, port, credentials, etc.)
3. Profile name

Tests the connection before saving. If the connection fails, it shows install instructions for the required driver.

### Import from dbt

```bash
tabletalk connect --from-dbt my_dbt_project
tabletalk connect --from-dbt my_dbt_project --target prod
```

Reads `~/.dbt/profiles.yml`, finds the named project, and converts the connection to tabletalk format. Supports Postgres, Snowflake, BigQuery, DuckDB, and Azure SQL. See [dbt Integration](dbt-integration.md).

### After saving

The profile is saved to `~/.tabletalk/profiles.yml`. Reference it in `tabletalk.yaml`:

```yaml
profile: my_profile_name
```

---

## `tabletalk profiles`

Manage saved connection profiles.

### `tabletalk profiles list`

```bash
tabletalk profiles list
```

Lists all saved profiles in `~/.tabletalk/profiles.yml`.

### `tabletalk profiles test NAME`

```bash
tabletalk profiles test my_postgres_prod
```

Tests a saved profile by attempting to connect to the database. Reports success or failure with error details.

### `tabletalk profiles delete NAME`

```bash
tabletalk profiles delete my_old_profile
```

Permanently removes a profile from `~/.tabletalk/profiles.yml`.

---

## `tabletalk history`

View recent query history for a project.

```bash
tabletalk history [DIR] [OPTIONS]
```

**Arguments:**

| Argument | Default | Description |
|----------|---------|-------------|
| `DIR` | `.` | Project directory |

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--limit INTEGER` | `20` | Number of recent entries to show |

History is stored in `.tabletalk_history.jsonl` in the project directory. Each entry records the question, generated SQL, manifest name, and timestamp.

```bash
tabletalk history                  # show last 20 queries
tabletalk history --limit 50       # show last 50
tabletalk history ./my_project     # show history for a specific project
```

---

## Global options

These options can be passed before any command:

```bash
tabletalk --verbose apply          # enable debug logging for any command
tabletalk --help                   # show top-level help
tabletalk apply --help             # show help for a specific command
```
