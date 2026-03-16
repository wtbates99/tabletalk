# Web UI

The tabletalk web UI is a single-page app served by Flask at `http://localhost:5000`.

```bash
tabletalk serve            # default port 5000
tabletalk serve --port 8080
```

---

## Layout

```
┌─────────────────────────────────────────────────────────────┐
│ Sidebar (268px)          │  Main area                       │
│                          │                                   │
│  tabletalk               │  [sales agent]    ↺ New chat    │
│  ollama · qwen2.5-coder  │                                   │
│                          │  ┌─────────────────────────────┐  │
│  [Context][Saved][History│  │ SQL                    Copy ⭐│  │
│                          │  │ SELECT ...                   │  │
│  MANIFESTS               │  └─────────────────────────────┘  │
│  📋 customers            │                                   │
│  📋 inventory            │  Results: 12 rows     ↓CSV ↓JSON │
│  📋 marketing            │  ┌──────┬──────┬───────────────┐ │
│  📋 sales  ←─ active     │  │ name │ rev  │ [bar chart]   │ │
│                          │  └──────┴──────┴───────────────┘ │
│  SCHEMA                  │                                   │
│  ▶ orders (7)            │  💡 Insight                       │
│  ▶ order_items (6)       │  The top 5 products account for...│
│  ▶ products (8)          │                                   │
│                          │  [What is revenue by city?]       │
│                          │  [Show top customers]             │
│                          │                                   │
│                          │  ┌───────────────────────────┐    │
│                          │  │ Ask a question…        Send│    │
│                          │  └───────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

---

## Sidebar

### Model badge

Displays the active LLM provider and model name, loaded from the `/config` endpoint:

```
ollama · qwen2.5-coder:7b
```

### Manifests

Lists all compiled manifests (agents). Click one to load it — the schema tree updates to show the tables that agent can see.

Click **↻** to refresh the manifest list without reloading the page.

### Schema tree

Shows every table and column in the active agent's scope. Click a column to insert `table.column` into the input box. Double-click a table header to insert the table name.

Use the search box to filter by table or column name.

### Saved tab

Saved queries (favorites) — questions you've starred. Click a saved query to load it into the input. Click ✕ to delete.

### History tab

The 40 most recent queries across all agents, newest first. Click a history entry to load the question into the input box.

---

## Chat area

### Sending a question

Type a question and press **Enter** or click **Send**. SQL streams token-by-token as it's generated. When generation completes:
- Syntax highlighting is applied
- **Copy** button copies the SQL to clipboard
- **⭐ Save** button opens the save dialog

### Run toggle

When **Run** is checked, the generated SQL is automatically executed after generation completes. Results appear in a table below the SQL block.

### Explain toggle

When both **Run** and **Explain** are checked, the agent streams a plain-English explanation of the results after execution.

### Auto-suggested follow-ups

After each response, three suggested follow-up questions appear as chips below the SQL. Click one to send it immediately.

### Charts

When results have exactly two columns (a label column and a numeric column), a horizontal bar chart is automatically rendered alongside the results table. Supports up to 40 rows.

### Export

Every results block has **↓ CSV** and **↓ JSON** export buttons. Downloads happen client-side — no server round-trip.

### Fix with AI

If SQL execution fails, an error block appears with a **Fix with AI** button. Clicking it sends the failed SQL and the error message back to the LLM, which generates a corrected version.

### Saving queries

Click **⭐ Save** on any SQL block to save it as a favorite. Enter a name in the dialog. Saved queries appear in the **Saved** tab and can be reused across sessions.

---

## Keyboard shortcuts

| Shortcut | Action |
|----------|--------|
| `Enter` | Send question |
| `Shift+Enter` | Insert newline in input |
| `Escape` | Close save dialog |

---

## Switching agents

Click any manifest in the sidebar to switch agents mid-session. The context bar at the top shows the active agent. The schema tree updates immediately.

Click **↺ New chat** to clear the conversation context while keeping the same agent.

---

## Theme

Click **☀** in the sidebar header to toggle between dark and light mode.

---

## Health check

The server exposes a health endpoint suitable for load balancer probes and Docker `HEALTHCHECK`:

```
GET /health
```

Returns:
- `200 {"status": "ok"}` — manifests are compiled and the project is ready
- `503 {"status": "degraded", "issues": [...]}` — manifests missing or config invalid

---

## Session security

The web UI uses Flask sessions for conversation state. Set `TABLETALK_SECRET_KEY` in the environment for a stable signing key:

```bash
export TABLETALK_SECRET_KEY=$(openssl rand -hex 32)
tabletalk serve
```

Without this variable, Flask generates a random key at startup — sessions are invalidated on restart.
