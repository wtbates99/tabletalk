# SQL safety

TableTalk execution is always read-only. The legacy `safe_mode` field is accepted
during migration but cannot disable SQL validation.

---

Before execution, TableTalk parses exactly one dialect-aware statement with
sqlglot. It must be a query AST, not merely text beginning with a read keyword.
When a compiled artifact is active, referenced relations and practical column
references must be declared by that artifact.

---

## What safe mode blocks

Validation blocks DDL, DML, commands, multiple statements, malformed SQL,
undeclared relations, and qualified undeclared columns:

| Blocked | Examples |
|---------|---------|
| `DELETE` | `DELETE FROM orders WHERE ...` |
| `UPDATE` | `UPDATE customers SET ...` |
| `INSERT` | `INSERT INTO ... VALUES ...` |
| `DROP` | `DROP TABLE orders` |
| `TRUNCATE` | `TRUNCATE orders` |
| `CREATE` | `CREATE TABLE ...` |
| `ALTER` | `ALTER TABLE ...` |
| `REPLACE` | `REPLACE INTO ...` |

## What validation allows

| Allowed | Examples |
|---------|---------|
| `SELECT` | `SELECT * FROM orders` |
| `WITH` | CTEs: `WITH cte AS (SELECT ...) SELECT ...` |

---

## Error behaviour

When validation blocks a query, a typed failure is raised before the SQL is sent
to the database driver:

```
code: sql_not_read_only
stage: validation
```

Any model-based repair is disclosed and must pass the complete validation stage
again before execution.

---

## Production deployment checklist

Beyond application validation, use database-level controls:

### Database-level permissions

For defence in depth, also restrict the database user to `SELECT` only:

**Snowflake:**
```sql
CREATE ROLE tabletalk_reader;
GRANT USAGE ON DATABASE analytics TO ROLE tabletalk_reader;
GRANT USAGE ON SCHEMA analytics.public TO ROLE tabletalk_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics.public TO ROLE tabletalk_reader;
GRANT ROLE tabletalk_reader TO USER tabletalk_agent;
```

For SQLite and DuckDB deployments, open file-backed databases read-only and
enforce read-only filesystem permissions in addition to TableTalk validation.

### Session security

Set a stable `TABLETALK_SECRET_KEY` for Flask session signing:

```bash
export TABLETALK_SECRET_KEY=$(openssl rand -hex 32)
```

Without this, Flask generates a new random key on each restart — all existing sessions are invalidated.

### Network isolation

- Run `tabletalk serve` behind a reverse proxy (nginx, Caddy)
- Do not expose port 5000 directly to the internet
- Use HTTPS in front of the web UI

**nginx example:**
```nginx
server {
    listen 443 ssl;
    server_name tabletalk.company.com;

    ssl_certificate /etc/ssl/certs/tabletalk.crt;
    ssl_certificate_key /etc/ssl/private/tabletalk.key;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        # SSE requires these headers for streaming
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }
}
```

### Running with a WSGI server

The built-in Flask dev server is not suitable for production:

```bash
pip install gunicorn
gunicorn "tabletalk.app:create_app('/path/to/project')" \
  --bind 127.0.0.1:5000 \
  --workers 1 \
  --timeout 120
```

Use 1 worker — tabletalk's QuerySession is a per-process singleton. Multiple workers would create separate (unshared) sessions.

### Health checks

Use the `/health` endpoint for readiness probes:

**Docker:**
```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD curl -f http://localhost:5000/health || exit 1
```

**Kubernetes:**
```yaml
readinessProbe:
  httpGet:
    path: /health
    port: 5000
  initialDelaySeconds: 10
  periodSeconds: 30
```

The endpoint returns `200 {"status": "ok"}` when manifests are compiled and ready, or `503 {"status": "degraded", "issues": [...]}` if something is wrong.
