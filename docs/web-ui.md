# Web UI

TableTalk's web app is an evidence-first data workspace served by Flask:

```bash
tabletalk serve
tabletalk serve --port 8080
```

It has three workspaces—**Ask**, **Eval Studio**, and **Library**—plus a live
Evidence rail. Keyboard shortcuts `1`, `2`, and `3` switch workspaces.

## Ask

Choose a compiled data agent in the dark left rail. Agents enriched from a dbt
`manifest.json` carry a `DBT` badge; ordinary TableTalk contexts carry `CTX`.
The Evidence rail shows the exact tables and columns inside that agent's hard
schema boundary.

Selecting an agent asks Ollama to read the compiled context and propose three
relevant questions. These are model-generated—there is no local suggestion
fallback. Click a field in the schema boundary to insert its fully qualified
name in the composer.

For each question, the interface builds a four-stage receipt:

1. **Scope** — the compiled context supplied to Ollama;
2. **Compose** — SQL streamed from the configured Ollama model;
3. **Execute** — the real database result, row count, and latency;
4. **Ground** — an Ollama finding based on the returned rows.

The SQL, result table, optional chart, timing, token usage, and execution status
remain attached to the answer. An execution receipt proves that the query ran;
it does not claim the answer is correct. Eval Studio provides that release
confidence.

### Controls

- **Execute query** runs generated SQL automatically.
- **Grounded finding** sends returned rows back through Ollama for explanation.
- **Copy** copies the generated SQL.
- **Save** stores the question and SQL in Library.
- **CSV** and **JSON** export the full query result through the server.
- **Fix with Ollama** sends rejected SQL, the database error, and the compiled
  context back to the same model. The revised SQL is shown before execution.
- **New thread** clears conversational context without changing the agent.

Press `Enter` to run or `Shift+Enter` for a new line.

## No-fallback AI behavior

Natural-language parsing, SQL repair, suggested questions, and grounded findings
always use the configured LLM. If Ollama returns a model, authentication, or
Free-tier session-limit error, the failed stage turns red and the exact error
is preserved in the receipt. TableTalk does not insert canned SQL, use a local
heuristic parser, or switch to a paid provider.

Database execution and eval verification are ordinary code by design: they
observe and verify the AI's work rather than replacing it.

## Eval Studio

Eval Studio discovers YAML suites under `evals/`. Select a suite and run it to
watch real case events stream into the case board. Each row exposes:

- case status and aggregate score;
- SQL execution, result accuracy, safety, structure, answer, and performance
  metric results when configured;
- final release score and pass/fail gate.

The UI runs the same versioned suites as:

```bash
tabletalk eval run evals/sales.yaml
```

No synthetic progress is used. If Ollama stops the run, Eval Studio stops and
shows the model error instead of continuing with a substitute.

## Library

Library separates saved questions from recent runs. Reopen an item to restore
its agent and question in the Ask composer. Saved items can be removed in place.

## Evidence rail and responsive layout

On wide screens the Evidence rail stays visible beside the workspace. Below
1180px it becomes a slide-over opened by the **Evidence** button. On mobile,
agents move into the header selector and the navigation becomes compact.

Use **Appearance** to switch themes. The preference is stored in the browser.

## Health and session security

`GET /health` returns `200` when manifests, database configuration, and LLM
configuration are ready, or `503` with concrete issues.

Set a stable signing secret outside local development:

```bash
export TABLETALK_SECRET_KEY=$(openssl rand -hex 32)
tabletalk serve
```
