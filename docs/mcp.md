# MCP server

`tabletalk mcp` serves your agents to MCP clients such as Claude Desktop, Claude
Code, and Cursor. It serves only agents whose dbt scope matches
`tabletalk.lock.yaml`. Every answer comes with its
[receipt](spec/receipt-v1.md).

```console
pip install "tabletalk[mcp,duckdb]"   # or tabletalk[mcp,snowflake]
tabletalk lock
tabletalk mcp --project-folder path/to/project
```

## Tools

| Tool | Result |
|---|---|
| `list_agents` | Every agent and its status: `served`, `drifted`, or `unlocked`. |
| `describe_agent(agent)` | The dbt resources and columns a served agent may query, with sensitive columns flagged. |
| `ask(agent, question)` | `answered` with a receipt, or `rejected` or `needs_clarification` with the reason. |
| `get_receipt(run_id)` | A stored receipt by run ID or a unique prefix. |

All tools are marked read-only. Execution goes through the same SQL validator,
sensitive-data rules, row limits, and [Snowflake scan budget](configuration.md)
as `tabletalk ask`.

## Locked agents only

On every call, the server checks whether the dbt artifacts, `tabletalk.lock.yaml`,
`tabletalk.yaml`, or the agent files changed, for example because dbt rebuilt
while the server ran. If they did, it reloads the project and compares each
agent's scope with the lock again:

- `served`: the scope matches the lock. The agent answers.
- `drifted`: a dbt change altered the scope. The agent refuses until someone
  reviews `tabletalk plan` and runs `tabletalk lock`. No restart is needed.
- `unlocked`: the agent is not in the lock.

If the reload fails, for example while dbt is still writing `manifest.json`, the
call is refused and the next call retries.

A dbt change never reaches a chat client without review.

## Claude Desktop

Add the server to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "tabletalk": {
      "command": "tabletalk",
      "args": ["mcp", "--project-folder", "/absolute/path/to/project"]
    }
  }
}
```

Use an absolute path to the `tabletalk` executable if it is not on the PATH that
Claude Desktop sees. In Claude Code, run
`claude mcp add tabletalk -- tabletalk mcp --project-folder /absolute/path/to/project`.

`--cache-policy replay` answers only from recorded inference, with no model call.
This is useful for demos and for checking that a client is wired correctly.
