# Safe Mode

Safe mode restricts the agent to read-only queries. Enable it for any agent connected to a production database.

---

## Enabling safe mode

```yaml
# tabletalk.yaml
safe_mode: true
```

When enabled, tabletalk checks the generated SQL before execution. If the query is not a read operation, it raises an error and **the query never reaches the database**.

---

## What safe mode blocks

Safe mode blocks any SQL that doesn't begin with a read-only keyword:

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

## What safe mode allows

| Allowed | Examples |
|---------|---------|
| `SELECT` | `SELECT * FROM orders` |
| `WITH` | CTEs: `WITH cte AS (SELECT ...) SELECT ...` |
| `EXPLAIN` | `EXPLAIN SELECT ...` |
| `SHOW` | `SHOW TABLES` |
| `DESCRIBE` / `DESC` | `DESCRIBE orders` |

---

## Error behaviour

When safe mode blocks a query, the error is raised before the SQL is sent to the database driver:

```
Error: Safe mode is enabled — only SELECT queries are allowed.
Generated SQL: DELETE FROM orders WHERE status = 'cancelled'
```

In the web UI, blocked queries show the error in the execution block with an option to "Fix with AI" — which will regenerate a SELECT equivalent.

---

## Production deployment checklist

Beyond safe mode, consider these additional measures for production deployments:

### Database-level permissions

Safe mode is a client-side check. For defence in depth, also restrict the database user to `SELECT` only:

**PostgreSQL:**
```sql
CREATE USER tabletalk_agent WITH PASSWORD 'secret';
GRANT CONNECT ON DATABASE analytics TO tabletalk_agent;
GRANT USAGE ON SCHEMA public TO tabletalk_agent;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO tabletalk_agent;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO tabletalk_agent;
```

**Snowflake:**
```sql
CREATE ROLE tabletalk_reader;
GRANT USAGE ON DATABASE analytics TO ROLE tabletalk_reader;
GRANT USAGE ON SCHEMA analytics.public TO ROLE tabletalk_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics.public TO ROLE tabletalk_reader;
GRANT ROLE tabletalk_reader TO USER tabletalk_agent;
```

**MySQL:**
```sql
CREATE USER 'tabletalk_agent'@'%' IDENTIFIED BY 'secret';
GRANT SELECT ON analytics.* TO 'tabletalk_agent'@'%';
FLUSH PRIVILEGES;
```

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
