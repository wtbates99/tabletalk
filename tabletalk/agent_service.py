"""Serve only locked agents: the policy behind ``tabletalk mcp``, free of any transport."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from tabletalk.inference import CachePolicy
from tabletalk.project import Project
from tabletalk.receipts import RECEIPTS_DIRECTORY, Receipt, load_receipt
from tabletalk.runtime import AmbiguityError, RejectionError
from tabletalk.semantic_guard import is_sensitive
from tabletalk.state import LOCK_FILENAME, load_lock, snapshot

AgentStatus = Literal["served", "drifted", "unlocked"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AgentSummary(_Model):
    """One agent, and whether this server will answer with it."""

    name: str
    description: str
    status: AgentStatus
    reason: str | None = None
    sample_questions: list[str] = []


class ColumnScope(_Model):
    name: str
    data_type: str | None
    description: str
    sensitive: bool


class ResourceScope(_Model):
    unique_id: str
    relation: str
    description: str
    columns: list[ColumnScope]


class AgentScope(_Model):
    """The dbt resources and columns a served agent may query."""

    name: str
    description: str
    resources: list[ResourceScope]


class AskResult(_Model):
    """An answer with its receipt, or the agent's deliberate refusal."""

    outcome: Literal["answered", "rejected", "needs_clarification"]
    message: str | None = None
    receipt: Receipt | None = None


class AgentUnavailableError(ValueError):
    """The agent is unknown, or its scope no longer matches tabletalk.lock.yaml."""


class AgentService:
    """Answer questions only with agents whose dbt scope matches the lock.

    Every call first checks the dbt artifacts, the lock, and the agent files. If any
    changed, for example because dbt rebuilt while the server ran, the project is
    reloaded and drift is decided again, so a scope change can never be served from
    a stale manifest. If the reload fails, for example on a half-written manifest,
    the call is refused and the next call retries.
    """

    def __init__(
        self,
        project: Project,
        *,
        cache_policy: CachePolicy | None = None,
        loader: Callable[[Path], Project] = Project.load,
    ) -> None:
        self.cache_policy = cache_policy
        self._loader = loader
        self._root = project.root
        self._stamp = self._watched(project)
        self._load(project)

    @staticmethod
    def _watched(project: Project) -> tuple[tuple[str, int, int], ...]:
        """Fingerprint every file that can change an agent's scope or status."""
        paths = [
            *project.manifest.path.parent.glob("*.json"),
            project.root / LOCK_FILENAME,
            project.root / "tabletalk.yaml",
            *project.agents_directory.glob("*.yaml"),
            *project.agents_directory.glob("*.yml"),
        ]
        stamps = []
        for path in paths:
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            stamps.append((str(path), stat.st_mtime_ns, stat.st_size))
        return tuple(sorted(stamps))

    def _refresh(self) -> None:
        if self._watched(self.project) == self._stamp:
            return
        try:
            project = self._loader(self._root)
            stamp = self._watched(project)
            self._load(project)
        except Exception as exc:
            raise AgentUnavailableError(
                f"The dbt project changed and could not be reloaded: {exc}"
            ) from exc
        self._stamp = stamp

    def _load(self, project: Project) -> None:
        self.project = project
        locked = load_lock(project.root / LOCK_FILENAME)
        current = snapshot(project.agents(), project.manifest)
        self._summaries: dict[str, AgentSummary] = {}
        for agent in project.agents():
            status: AgentStatus = "served"
            reason = None
            if locked is None or agent.name not in locked.agents:
                status, reason = "unlocked", "Not in tabletalk.lock.yaml. Run 'tabletalk lock'."
            elif locked.agents[agent.name] != current.agents[agent.name]:
                status = "drifted"
                reason = "Scope changed since tabletalk.lock.yaml. Review 'tabletalk plan'."
            self._summaries[agent.name] = AgentSummary(
                name=agent.name,
                description=agent.description,
                status=status,
                reason=reason,
                sample_questions=list(agent.sample_questions),
            )

    def agents(self) -> list[AgentSummary]:
        """List every agent with its serving status, served agents first."""
        self._refresh()
        return sorted(
            self._summaries.values(), key=lambda item: (item.status != "served", item.name)
        )

    def _served(self, name: str) -> None:
        self._refresh()
        summary = self._summaries.get(name)
        if summary is None:
            raise AgentUnavailableError(f"Unknown agent '{name}'. Call list_agents.")
        if summary.status != "served":
            raise AgentUnavailableError(f"Agent '{name}' is not served: {summary.reason}")

    def scope(self, name: str) -> AgentScope:
        """Describe the resources and columns a served agent may query."""
        self._served(name)
        resolved = self.project.resolve_agent(name)
        return AgentScope(
            name=name,
            description=resolved.source.description,
            resources=[
                ResourceScope(
                    unique_id=node.unique_id,
                    relation=node.relation_name,
                    description=node.description,
                    columns=[
                        ColumnScope(
                            name=column.name,
                            data_type=column.data_type,
                            description=column.description,
                            sensitive=is_sensitive(node, column.name),
                        )
                        for column in node.columns.values()
                    ],
                )
                for node in sorted(resolved.nodes, key=lambda item: item.unique_id)
            ],
        )

    def ask(self, name: str, question: str) -> AskResult:
        """Answer with a served agent and return the receipt, or its refusal."""
        self._served(name)
        try:
            trace = self.project.answer(name, question, cache_policy=self.cache_policy)
        except AmbiguityError as exc:
            return AskResult(outcome="needs_clarification", message=str(exc))
        except RejectionError as exc:
            return AskResult(outcome="rejected", message=str(exc))
        return AskResult(outcome="answered", receipt=self.project.receipt(trace))

    def receipt(self, run_id: str) -> Receipt:
        """Return a stored receipt by run ID or unique prefix."""
        self._refresh()
        return load_receipt(self.project.root / RECEIPTS_DIRECTORY, run_id)
