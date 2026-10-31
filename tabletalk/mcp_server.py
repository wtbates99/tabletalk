"""``tabletalk mcp``: expose locked agents to MCP clients such as Claude Desktop."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from tabletalk.agent_service import (
    AgentScope,
    AgentService,
    AgentSummary,
    AgentUnavailableError,
    AskResult,
)
from tabletalk.inference import ReplayMissError
from tabletalk.receipts import Receipt, ReceiptError

INSTRUCTIONS = (
    "TableTalk data agents answer questions from a dbt project. Call list_agents first, "
    "then ask a served agent. Each answer includes a receipt with the executed SQL, "
    "evidence rows, and a trust level. Report the trust level with the number. Do not "
    "restate a number that a receipt marks unverified as fact."
)
_READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


@contextmanager
def _explained() -> Iterator[None]:
    """Show the model why a request was refused. MCP hides other exceptions' text."""
    try:
        yield
    except (AgentUnavailableError, ReceiptError, ReplayMissError) as exc:
        raise ToolError(str(exc)) from exc


def build_server(service: AgentService) -> MCPServer:
    """Return an MCP server whose tools answer only through ``service``."""
    server = MCPServer("tabletalk", instructions=INSTRUCTIONS)

    @server.tool(annotations=_READ_ONLY)
    def list_agents() -> list[AgentSummary]:
        """List the data agents. Only agents with status 'served' can answer."""
        return service.agents()

    @server.tool(annotations=_READ_ONLY)
    def describe_agent(agent: str) -> AgentScope:
        """Show the dbt models and columns a served agent may query."""
        with _explained():
            return service.scope(agent)

    @server.tool(annotations=_READ_ONLY)
    def ask(agent: str, question: str) -> AskResult:
        """Ask a served agent one question and get the answer with its receipt."""
        with _explained():
            return service.ask(agent, question)

    @server.tool(annotations=_READ_ONLY)
    def get_receipt(run_id: str) -> Receipt:
        """Fetch a stored answer receipt by run ID or a unique prefix of it."""
        with _explained():
            return service.receipt(run_id)

    return server
