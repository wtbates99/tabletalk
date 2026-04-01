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
  validate  Dry-run health check — config, contexts, DB, LLM
  diff      Show stale context files and table-level changes
  test      Smoke-test SQL generation against every manifest
  query     Start an interactive agent session
  serve     Launch the web UI
  connect   Save a database connection profile
  profiles  Manage saved connection profiles
  history   View recent query history
  schedule  Manage and run scheduled queries
```

---

## `tabletalk init`

Scaffold a new project in the current directory.

```bash
tabletalk init
```

Creates:
- `tabletalk.yaml` — template config with all available options documented inline
- `contexts/default_context.yaml` — sample context definition
- `manifest/` — empty output directory

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
   - Introspects each declared table (columns, PKs, FKs, types) — results cached in-process (300s TTL)
   - Merges your descriptions with the live schema
   - Writes `manifest/<name>.txt` in compact schema notation

**Examples:**

```bash
tabletalk apply                   # compile current directory
tabletalk apply ./my_project      # compile a specific project
```

---

## `tabletalk validate`

Dry-run validation — checks everything before you run a query. Exits with code 1 if any check fails so it works in CI pipelines.

```bash
tabletalk validate [DIR] [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--skip-db` | Skip database connectivity test (useful in CI without a live DB) |

**What it checks:**

1. `tabletalk.yaml` exists and has required keys (`llm`, `contexts`, `output`)
2. All `contexts/*.yaml` files are valid YAML and have a `name` field
3. Manifests are up-to-date (warns if context files are newer than manifests)
4. Database is reachable (unless `--skip-db`)
5. LLM config is complete (`provider` + `api_key`)

**Examples:**

```bash
tabletalk validate                # full check
tabletalk validate --skip-db      # skip DB connectivity (CI mode)
tabletalk validate ./my_project
```

**CI usage:**

```yaml
# GitHub Actions
- run: tabletalk validate --skip-db
```

---

## `tabletalk diff`

Show which context files are stale and what would change on the next `apply`.

```bash
tabletalk diff [DIR]
```

**Output:**

- `OK` — manifest is up to date
- `STALE` — context file is newer than manifest, with a table-level diff showing added/removed tables
- `NEW` — context file has no manifest yet

**Example:**

```
OK    customers.yaml
STALE sales.yaml
  ┌─────────────┬────────────────────┐
  │ Change      │ Table              │
  ├─────────────┼────────────────────┤
  │ + added     │ public.campaigns   │
  │ - removed   │ public.legacy_data │
  └─────────────┴────────────────────┘
NEW   marketing.yaml — no manifest yet (run 'tabletalk apply')
```

Think of this like `terraform plan` — see what would change before committing.

---

## `tabletalk test`

Smoke-test SQL generation against every manifest in the project.

```bash
tabletalk test [DIR] [OPTIONS]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--question TEXT` | `"What tables are available and how many rows does each have?"` | Test question to run against each manifest |
| `--execute` | off | Also execute the generated SQL and report row count |

**Examples:**

```bash
tabletalk test                        # generate SQL for all manifests
tabletalk test --execute              # generate + run
tabletalk test --question "show top 5 rows from any table"
```

**Output:**

```
Testing: sales.txt
  ✓ SQL generated
  ✓ Executed — 5 row(s) returned

Testing: inventory.txt
  ✓ SQL generated
  ✗ no database provider configured

Results: 1 passed  1 failed
```

Exits with code 1 if any manifest fails — usable in CI.

---

## `tabletalk query`

Start an interactive agent session in the terminal.

```bash
tabletalk query [DIR] [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--execute` | Execute the generated SQL and display results as a table |
| `--explain` | Stream a plain-English explanation of the results (requires `--execute`) |
| `--output FILE` | Save query results to a CSV file (requires `--execute`) |
| `--no-context` | Disable multi-turn conversation — each question is independent |

**Examples:**

```bash
tabletalk query                              # basic SQL generation
tabletalk query --execute                    # generate + run
tabletalk query --execute --explain          # generate + run + explain
tabletalk query --execute --output data.csv  # save results to CSV
tabletalk query --no-context                 # single-turn mode
```

### Session commands

Once inside the query session, these special inputs are available:

| Input | Action |
|-------|--------|
| Any question | Generate SQL (streamed token-by-token) |
| `change` | Switch to a different manifest/agent |
| `history` | Display recent queries for this session |
| `stats` | Show token usage and latency stats for recent queries |
| `clear` | Clear conversation context (start fresh multi-turn session) |
| `exit` | Exit the session |
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

Use `clear` to reset context, or `--no-context` to disable it entirely. Configure the context window with `max_conv_messages` in `tabletalk.yaml`.

---

## `tabletalk serve`

Launch the web UI.

```bash
tabletalk serve [OPTIONS]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--port INTEGER` | `5000` | Port to listen on |
| `--debug` | off | Enable Flask debug mode |
| `--workers INTEGER` | `4` | Number of threads for concurrent request handling |

**Examples:**

```bash
tabletalk serve                    # http://localhost:5000
tabletalk serve --port 8080
tabletalk serve --debug
tabletalk serve --workers 8        # more threads for concurrent users
```

The built-in server uses Flask's threaded mode so multiple users can stream SQL generation concurrently. For production, use gunicorn:

```bash
gunicorn 'tabletalk.app:app' -w 4 -b 0.0.0.0:5000
```

Set `TABLETALK_SECRET_KEY` to a random string for session security. Configure rate limiting with `TABLETALK_RATE_LIMIT` (requests per window, default 30) and `TABLETALK_RATE_WINDOW` (seconds, default 60).

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
| `--test-only PROFILE` | Test a named profile connection without saving |

### Interactive wizard

```bash
tabletalk connect
```

Prompts for database type, connection details, and a profile name. Tests the connection before saving. If the connection fails, shows install instructions for the required driver.

### Import from dbt

```bash
tabletalk connect --from-dbt my_dbt_project
tabletalk connect --from-dbt my_dbt_project --target prod
```

See [dbt Integration](dbt-integration.md).

---

## `tabletalk profiles`

Manage saved connection profiles.

### `tabletalk profiles list`

Lists all saved profiles in `~/.tabletalk/profiles.yml`.

### `tabletalk profiles test NAME`

Tests a saved profile by attempting to connect to the database.

### `tabletalk profiles delete NAME`

Permanently removes a profile. Also removes any secrets stored in the OS keychain.

---

## `tabletalk history`

View recent query history with latency and row-count metrics.

```bash
tabletalk history [DIR] [OPTIONS]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--limit INTEGER` | `20` | Number of recent entries to show |

Each entry shows the question, generated SQL, manifest name, timestamp, and performance metrics (generation time, row count) when available.

---

## `tabletalk schedule`

Manage scheduled queries that run automatically and save results to CSV files.

```bash
tabletalk schedule COMMAND
```

### `tabletalk schedule add NAME`

Add a new scheduled query.

```bash
tabletalk schedule add daily_revenue \
  --question "Total revenue today by product category" \
  --manifest sales.txt \
  --interval 1440 \
  --output-dir ./reports
```

**Options:**

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--question` | Yes | — | Natural-language question to ask on each run |
| `--manifest` | Yes | — | Manifest file to query against (e.g. `sales.txt`) |
| `--interval` | No | `60` | Run interval in minutes |
| `--output-dir` | No | project folder | Directory to write CSV results |

### `tabletalk schedule list`

Show all configured schedules, their intervals, and when they last ran.

### `tabletalk schedule remove NAME`

Remove a scheduled query by name.

### `tabletalk schedule run`

Execute all due schedules. A schedule is "due" when its interval has elapsed since the last run (or it has never run).

```bash
tabletalk schedule run               # run due schedules
tabletalk schedule run --force       # run all schedules regardless of interval
tabletalk schedule run ./my_project  # run schedules for a specific project
```

**Setting up a cron job:**

```bash
# Run every 30 minutes
*/30 * * * * tabletalk schedule run /path/to/project
```

Results are written to `<output_dir>/<name>_<timestamp>.csv`. Schedule state is persisted in `.tabletalk_schedules.json`.
