# REST API Reference

The tabletalk web server (`tabletalk serve`) exposes a REST API consumed by the web UI. All endpoints are also available for programmatic use.

**Base URL:** `http://localhost:5000` (default)

---

## Health

### `GET /health`

Check server readiness.

**Response 200 — ready:**
```json
{
  "status": "ok"
}
```

**Response 503 — degraded:**
```json
{
  "status": "degraded",
  "issues": ["No manifests found — run tabletalk apply"]
}
```

---

## Configuration

### `GET /config`

Return the active LLM provider and model name.

**Response:**
```json
{
  "provider": "ollama",
  "model": "qwen2.5-coder:7b"
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
  "manifest": "sales.txt",
  "details": "DATA_SOURCE: duckdb - ...\nCONTEXT: sales ...\n..."
}
```

The `details` field contains the full manifest text in compact schema notation. Parse it with the web UI's `parseManifest()` function or use it as-is as LLM context.

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
| `manifest` | string | Yes | — | Manifest filename (e.g. `sales.txt`) |
| `auto_execute` | boolean | No | `false` | Execute generated SQL |
| `explain` | boolean | No | `false` | Stream plain-English explanation after execution |
| `suggest` | boolean | No | `false` | Return 3 suggested follow-up questions |

**Response:** `text/event-stream`

Each line is a Server-Sent Event in the format:

```
data: {"type": "...", ...}
```

**Event types:**

| Type | Payload | Description |
|------|---------|-------------|
| `sql_chunk` | `{"type":"sql_chunk","content":"SELECT"}` | Incremental SQL token |
| `sql_done` | `{"type":"sql_done","sql":"SELECT ..."}` | Full generated SQL |
| `results` | `{"type":"results","columns":["month","revenue"],"rows":[["2024-01",...]],"count":12}` | Query results |
| `execute_error` | `{"type":"execute_error","error":"...","sql":"..."}` | SQL execution error |
| `explain_chunk` | `{"type":"explain_chunk","content":"Revenue grew..."}` | Incremental explanation token |
| `explain_done` | `{"type":"explain_done"}` | Explanation complete |
| `suggestions` | `{"type":"suggestions","questions":["...", "...", "..."]}` | Follow-up suggestions |
| `error` | `{"type":"error","error":"Select a manifest first"}` | Non-fatal error |
| `done` | `{"type":"done"}` | Stream complete |

**Example stream:**

```
data: {"type": "sql_chunk", "content": "SELECT"}
data: {"type": "sql_chunk", "content": " DATE_TRUNC"}
data: {"type": "sql_chunk", "content": "('month', created_at)"}
...
data: {"type": "sql_done", "sql": "SELECT DATE_TRUNC('month', created_at) AS month, SUM(total_amount) AS revenue FROM orders GROUP BY 1 ORDER BY 1"}
data: {"type": "results", "columns": ["month", "revenue"], "rows": [["2024-01-01", "15234.50"], ...], "count": 6}
data: {"type": "explain_chunk", "content": "Revenue peaked in March"}
...
data: {"type": "explain_done"}
data: {"type": "suggestions", "questions": ["Break that down by product", "Show month-over-month growth", "Which customers drove January revenue?"]}
data: {"type": "done"}
```

### `POST /fix/stream`

Fix a failing SQL query. Same SSE format as `/chat/stream` but generates corrected SQL given an error.

**Request:**
```json
{
  "sql": "SELECT * FROM order WHERE id = 1",
  "error": "relation \"order\" does not exist",
  "manifest": "sales.txt"
}
```

**Response:** `text/event-stream` — same event types as `/chat/stream`.

---

## Execution

### `POST /execute`

Execute SQL and return results (non-streaming).

**Request:**
```json
{
  "sql": "SELECT COUNT(*) AS total FROM orders",
  "manifest": "sales.txt"
}
```

**Response:**
```json
{
  "columns": ["total"],
  "rows": [[42]],
  "count": 1
}
```

**Error response:**
```json
{
  "error": "column \"total_ammount\" does not exist"
}
```

---

## Suggestions

### `POST /suggest`

Generate 3 follow-up question suggestions for a manifest.

**Request:**
```json
{
  "manifest": "sales.txt"
}
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

**Request:** empty body or `{}`

**Response:**
```json
{
  "status": "ok"
}
```

---

## History

### `GET /history`

Retrieve recent query history.

**Query parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `limit` | `20` | Maximum number of entries to return |

**Response:**
```json
{
  "history": [
    {
      "question": "What is total revenue this month?",
      "sql": "SELECT SUM(total_amount) FROM orders WHERE ...",
      "manifest": "sales.txt",
      "timestamp": "2024-03-15T14:32:01.123456"
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
      "created_at": "2024-03-15T14:32:01.123456"
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

**Response:**
```json
{
  "status": "ok"
}
```

### `DELETE /favorites/<name>`

Delete a saved favorite by name.

```
DELETE /favorites/Monthly%20revenue
```

**Response:**
```json
{
  "status": "ok"
}
```

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
            elif event["type"] == "results":
                print(f"Results: {event['count']} rows")
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
    }
  }
  buf = '';
}
```
