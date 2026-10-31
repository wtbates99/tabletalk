"""tabletalk mcp: only agents that match tabletalk.lock.yaml answer, over real MCP."""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from mcp import Client

from tabletalk.agent_service import AgentService, AgentUnavailableError
from tabletalk.inference import CachePolicy
from tabletalk.mcp_server import build_server
from tabletalk.project import Project
from tabletalk.runtime import Runtime
from tabletalk.state import LOCK_FILENAME, snapshot, write_lock
from tabletalk.tests.test_dbt_native import EXAMPLE, make_runtime
from tabletalk.tests.test_plan import _add_customer_model, _mutate

# Not an approved eval question, so the receipt is "grounded" rather than eval-checked.
QUESTION = "How much revenue was recognized in July 2026?"


def _stubbed(root: Path) -> Project:
    """Load the project with an offline stub runtime; also used as the reload loader."""
    loaded = Project.load(root)
    reject = loaded.agent("revenue").reject_if_contains

    def runtime(name: str, *, cache_policy: CachePolicy | None = None) -> Runtime:
        stub = make_runtime(loaded.manifest)
        stub.agent = replace(
            stub.agent, source=replace(stub.agent.source, reject_if_contains=reject)
        )
        return stub

    loaded.runtime = runtime  # type: ignore[method-assign]
    return loaded


@pytest.fixture
def project(tmp_path: Path) -> Project:
    root = tmp_path / "dbt-analytics"
    shutil.copytree(
        EXAMPLE, root, ignore=shutil.ignore_patterns(".tabletalk", "*.duckdb*", "run_results.json")
    )
    return _stubbed(root)


def _drift(project: Project) -> None:
    path = project.root / LOCK_FILENAME
    lock = yaml.safe_load(path.read_text())
    lock["agents"]["revenue"]["resources"].popitem()
    path.write_text(yaml.safe_dump(lock))


def test_an_agent_matching_the_lock_is_served_and_answers_with_a_receipt(
    project: Project,
) -> None:
    service = AgentService(project)

    [summary] = service.agents()
    result = service.ask("revenue", QUESTION)

    assert (summary.name, summary.status) == ("revenue", "served")
    assert result.outcome == "answered" and result.receipt is not None
    assert result.receipt.trust == "grounded"
    assert service.receipt(result.receipt.run_id[:10]) == result.receipt


def test_a_drifted_agent_is_listed_but_refused(project: Project) -> None:
    _drift(project)
    service = AgentService(project)

    [summary] = service.agents()

    assert summary.status == "drifted"
    assert "tabletalk plan" in (summary.reason or "")
    with pytest.raises(AgentUnavailableError, match="not served"):
        service.ask("revenue", QUESTION)
    with pytest.raises(AgentUnavailableError, match="not served"):
        service.scope("revenue")


def test_without_a_lock_no_agent_is_served(project: Project) -> None:
    (project.root / LOCK_FILENAME).unlink()

    [summary] = AgentService(project).agents()

    assert summary.status == "unlocked"


def test_scope_lists_locked_columns_and_flags_sensitive_ones(project: Project) -> None:
    scope = AgentService(project).scope("revenue")

    columns = {column.name: column for resource in scope.resources for column in resource.columns}
    assert "model.analytics.fct_orders" in {resource.unique_id for resource in scope.resources}
    assert columns["customer_id"].sensitive
    assert not columns["recognized_revenue"].sensitive


def test_policy_refusals_are_results_not_errors(project: Project) -> None:
    result = AgentService(project).ask("revenue", "What is forecast revenue for 2027?")

    assert result.outcome == "rejected"
    assert result.receipt is None
    assert "forecast revenue" in (result.message or "")


def test_a_dbt_rebuild_while_serving_is_caught_on_the_next_call(project: Project) -> None:
    service = AgentService(project, loader=_stubbed)
    assert service.ask("revenue", QUESTION).outcome == "answered"

    _mutate(project.root, _add_customer_model)  # dbt rebuilt: group:finance gained a model.

    [summary] = service.agents()
    assert summary.status == "drifted"
    with pytest.raises(AgentUnavailableError, match="not served"):
        service.ask("revenue", QUESTION)

    reviewed = Project.load(project.root)  # Someone reviews the plan and re-locks.
    write_lock(project.root / LOCK_FILENAME, snapshot(reviewed.agents(), reviewed.manifest))

    assert service.ask("revenue", QUESTION).outcome == "answered"
    assert "model.analytics.dim_customers" in {
        resource.unique_id for resource in service.scope("revenue").resources
    }


def test_a_half_written_manifest_is_refused_then_retried(project: Project) -> None:
    service = AgentService(project, loader=_stubbed)
    manifest = project.root / "target" / "manifest.json"
    complete = manifest.read_text()

    manifest.write_text(complete[: len(complete) // 2])
    with pytest.raises(AgentUnavailableError, match="could not be reloaded"):
        service.ask("revenue", QUESTION)

    manifest.write_text(complete + "\n")  # The dbt run finishes.
    assert service.ask("revenue", QUESTION).outcome == "answered"


@pytest.mark.anyio
async def test_mcp_clients_see_read_only_tools_and_structured_receipts(
    project: Project,
) -> None:
    server = build_server(AgentService(project))

    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        answered = await client.call_tool("ask", {"agent": "revenue", "question": QUESTION})
        unknown = await client.call_tool("ask", {"agent": "finance", "question": QUESTION})

    assert set(tools) == {"list_agents", "describe_agent", "ask", "get_receipt"}
    assert all(tool.annotations and tool.annotations.read_only_hint for tool in tools.values())
    assert not answered.is_error
    assert answered.structured_content is not None
    receipt = answered.structured_content["receipt"]
    assert receipt["schema_version"] == "tabletalk.receipt/v1"
    assert receipt["trust"] == "grounded"
    assert unknown.is_error
    assert "Unknown agent 'finance'" in str(unknown.content)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
