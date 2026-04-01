# REST API Reference

The tabletalk web server (`tabletalk serve`) exposes a REST API consumed by the web UI. All endpoints are also available for programmatic use.

**Base URL:** `http://localhost:5000` (default)

---

## Health

### `GET /health`

Enhanced readiness probe. Checks manifest folder, database connectivity, and LLM configuration.

**Response 200 — ready:**
```json
{
  "status": "ok",
  "project": "/path/to/project",
  "details": {
    "manifests": 4,
    "database": "ok",
    "llm_provider": "openai",
    "llm_model": "gpt-4o"
  }
}
```

**Response 503 — degraded:**
```json
{
  "status": "degraded",
  "issues": [
    "database unreachable: connection refused",
    "no manifests found — run 'tabletalk apply'"
  ],
  "details": {
    "database": "error",
    "llm_provider": "openai"
  }
}
```

Suitable for Docker `HEALTHCHECK` and Kubernetes liveness/readiness probes.

---

## Configuration

### `GET /config`

Return the active LLM provider, model, and runtime limits.

**Response:**
```json
{
  "provider": "openai",
  "model": "gpt-4o",
  "safe_mode": true,
  "max_rows": 500,
  "query_timeout": 30,
  "max_conv_messages": 20
}
```

---

## Stats

### `GET /stats`

Return aggregate token usage and latency statistics from recent history.

**Query parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `limit` | `100` | Number of recent history entries to aggregate |

**Response:**
```json
{
  "query_count": 47,
  "queries_with_metrics": 47,
  "total_prompt_tokens": 91234,
  "total_completion_tokens": 8456,
  "avg_generation_ms": 1423.2,
  "avg_execution_ms": 87.4
}
```

---

## Manifests

### `GET /manifests`

List all compiled manifests in the project's `manifest/` directory.

**Response:**
```json
{
  "manifests": ["customers.txt", "inventory.txt", "marketing.txt", "sales.txt"]
}
```

### `POST /select_manifest`

Load a manifest and reset the conversation context.

**Request:**
```json
{
  "manifest": "sales.txt"
}
```

**Response:**
```json
{
  "message": "Manifest 'sales.txt' selected",
  "details": "DATA_SOURCE: duckdb - ...\nCONTEXT: sales ...\n..."
}
```

---

## Chat (streaming)

### `POST /chat/stream`

Main query endpoint. Streams SQL generation, execution results, and explanation as Server-Sent Events.

**Request:**
```json
{
  "question": "What is total revenue by month?",
  "manifest": "sales.txt",
  "auto_execute": true,
  "explain": true,
  "suggest": true
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `question` | string | Yes | — | Natural language question |
| `manifest` | string | No | session manifest | Manifest filename (e.g. `sales.txt`) |
| `auto_execute` | boolean | No | `true` | Execute generated SQL |
| `explain` | boolean | No | `true` | Stream plain-English explanation after execution |
| `suggest` | boolean | No | `true` | Return 3 suggested follow-up questions |

**Rate limiting:** 30 requests per 60 seconds per session (configurable via `TABLETALK_RATE_LIMIT` / `TABLETALK_RATE_WINDOW` env vars). Returns 429 when exceeded.

**Response:** `text/event-stream`

Each line is a Server-Sent Event in the format `data: {...}\n\n`.

**Event types:**

| Type | Payload | Description |
|------|---------|-------------|
| `sql_chunk` | `{"type":"sql_chunk","content":"SELECT"}` | Incremental SQL token |
| `sql_done` | `{"type":"sql_done","sql":"SELECT ...","generation_ms":1234.5,"prompt_tokens":450,"completion_tokens":82}` | Full generated SQL with timing and token counts |
| `results` | `{"type":"results","columns":[...],"rows":[[...]],"count":12,"execution_ms":87.3}` | Query results with execution time |
| `execute_error` | `{"type":"execute_error","error":"...","sql":"..."}` | SQL execution error |
| `explain_chunk` | `{"type":"explain_chunk","content":"Revenue grew..."}` | Incremental explanation token |
| `explain_done` | `{"type":"explain_done"}` | Explanation complete |
| `suggestions` | `{"type":"suggestions","questions":["...", "...", "..."]}` | Follow-up suggestions |
| `error` | `{"type":"error","error":"..."}` | Fatal error |
| `done` | `{"type":"done"}` | Stream complete |

**Example stream:**

```
data: {"type": "sql_chunk", "content": "SELECT"}
data: {"type": "sql_chunk", "content": " DATE_TRUNC"}
...
data: {"type": "sql_done", "sql": "SELECT DATE_TRUNC('month', created_at) AS month, SUM(total_amount) AS revenue FROM orders GROUP BY 1 ORDER BY 1", "generation_ms": 1243.1, "prompt_tokens": 412, "completion_tokens": 38}
data: {"type": "results", "columns": ["month", "revenue"], "rows": [["2024-01-01", "15234.50"], ...], "count": 6, "execution_ms": 44.2}
data: {"type": "explain_chunk", "content": "Revenue peaked in March"}
...
data: {"type": "explain_done"}
data: {"type": "suggestions", "questions": ["Break that down by product", "Show month-over-month growth", "Which customers drove January revenue?"]}
data: {"type": "done"}
```

### `POST /fix/stream`

Fix a failing SQL query. Same SSE format as `/chat/stream`.

**Request:**
```json
{
  "sql": "SELECT * FROM order WHERE id = 1",
  "error": "relation \"order\" does not exist",
  "manifest": "sales.txt"
}
```

---

## Execution

### `POST /execute`

Execute SQL and return results (non-streaming).

**Request:**
```json
{
  "sql": "SELECT COUNT(*) AS total FROM orders"
}
```

**Response:**
```json
{
  "columns": ["total"],
  "rows": [["42"]],
  "count": 1
}
```

---

## Export

### `POST /export`

Execute SQL and return results as a downloadable file.

**Request:**
```json
{
  "sql": "SELECT * FROM orders WHERE status = 'completed'",
  "format": "csv",
  "filename": "completed_orders"
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `sql` | string | required | SQL to execute |
| `format` | string | `"csv"` | Output format: `"csv"` or `"json"` |
| `filename` | string | `"results"` | Base filename (without extension) |

**Response:** file download with `Content-Disposition: attachment`

- CSV: `text/csv; charset=utf-8`
- JSON: `application/json` — pretty-printed array of row objects

**Example (curl):**
```bash
curl -X POST http://localhost:5000/export \
  -H "Content-Type: application/json" \
  -d '{"sql":"SELECT * FROM orders LIMIT 1000","format":"csv","filename":"orders"}' \
  -o orders.csv
```

---

## REST API Query

### `POST /api/query`

Synchronous REST endpoint for programmatic integration. No streaming — generates SQL and optionally executes it, returning a single JSON response. Use this to integrate tabletalk with other tools, dashboards, or scripts.

**Request:**
```json
{
  "question": "Show top 10 customers by total revenue",
  "manifest": "sales.txt",
  "execute": true
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `question` | string | Yes | — | Natural language question |
| `manifest` | string | Yes* | — | Manifest filename (*or select via `/select_manifest` first) |
| `execute` | boolean | No | `true` | Execute the generated SQL |

**Response:**
```json
{
  "sql": "SELECT c.name, SUM(o.total_amount) AS revenue FROM customers c JOIN orders o ON c.id = o.customer_id GROUP BY c.name ORDER BY revenue DESC LIMIT 10",
  "columns": ["name", "revenue"],
  "rows": [
    ["Acme Corp", "142500.00"],
    ["Globex Inc", "98300.00"]
  ],
  "count": 10
}
```

**Example integrations:**

```python
# Python
import requests

resp = requests.post("http://localhost:5000/api/query", json={
    "question": "Total revenue by month",
    "manifest": "sales.txt"
})
data = resp.json()
print(data["sql"])
print(f"{data['count']} rows returned")
```

```bash
# curl
curl -s -X POST http://localhost:5000/api/query \
  -H "Content-Type: application/json" \
  -d '{"question": "top 5 products by sales", "manifest": "sales.txt"}' | jq .
```

---

## Suggestions

### `POST /suggest`

Generate 3 follow-up question suggestions for the current schema and conversation context.

**Request:**
```json
{ "manifest": "sales.txt" }
```

**Response:**
```json
{
  "questions": [
    "What is total revenue by month?",
    "Which products drive the most revenue?",
    "Show average order value by customer city"
  ]
}
```

---

## Session

### `POST /reset`

Clear the current conversation context.

**Response:**
```json
{ "ok": true }
```

---

## History

### `GET /history`

Retrieve recent query history, including performance metrics when available.

**Query parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `limit` | `30` | Maximum number of entries to return |

**Response:**
```json
{
  "history": [
    {
      "question": "What is total revenue this month?",
      "sql": "SELECT SUM(total_amount) FROM orders WHERE ...",
      "manifest": "sales.txt",
      "timestamp": "2026-03-15T14:32:01.123456+00:00",
      "metrics": {
        "generation_ms": 1243.1,
        "execution_ms": 44.2,
        "row_count": 1,
        "prompt_tokens": 412,
        "completion_tokens": 38
      }
    }
  ]
}
```

---

## Favorites

### `GET /favorites`

List all saved queries.

**Response:**
```json
{
  "favorites": [
    {
      "name": "Monthly revenue",
      "question": "What is total revenue by month?",
      "sql": "SELECT DATE_TRUNC('month', ...) ...",
      "manifest": "sales.txt",
      "created_at": "2026-03-15T14:32:01.123456+00:00"
    }
  ]
}
```

### `POST /favorites`

Save a query as a favorite.

**Request:**
```json
{
  "name": "Monthly revenue",
  "question": "What is total revenue by month?",
  "sql": "SELECT DATE_TRUNC('month', created_at) ...",
  "manifest": "sales.txt"
}
```

### `DELETE /favorites/<name>`

Delete a saved favorite by name.

---

## Consuming the streaming API

### Python (httpx)

```python
import httpx
import json

with httpx.stream("POST", "http://localhost:5000/chat/stream", json={
    "question": "What is total revenue by month?",
    "manifest": "sales.txt",
    "auto_execute": True,
    "explain": True,
}) as r:
    for line in r.iter_lines():
        if line.startswith("data: "):
            event = json.loads(line[6:])
            if event["type"] == "sql_chunk":
                print(event["content"], end="", flush=True)
            elif event["type"] == "sql_done":
                print(f"\n\nSQL: {event['sql']}")
                print(f"Generated in {event.get('generation_ms', '?')}ms")
            elif event["type"] == "results":
                print(f"Results: {event['count']} rows in {event.get('execution_ms', '?')}ms")
            elif event["type"] == "done":
                break
```

### curl

```bash
curl -sN -X POST http://localhost:5000/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"question":"total revenue by month","manifest":"sales.txt","auto_execute":true}'
```

### JavaScript (browser)

```javascript
const res = await fetch('/chat/stream', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ question: 'total revenue by month', manifest: 'sales.txt' }),
});

const reader = res.body.getReader();
const dec = new TextDecoder();
let buf = '';

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  buf += dec.decode(value, { stream: true });
  for (const line of buf.split('\n')) {
    if (line.startsWith('data: ')) {
      const event = JSON.parse(line.slice(6));
      if (event.type === 'sql_chunk') process.stdout.write(event.content);
      if (event.type === 'sql_done') console.log(`\nGenerated in ${event.generation_ms}ms`);
    }
  }
  buf = '';
}
```
