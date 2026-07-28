"""Narrow database, model, and trusted invocation contracts."""

from __future__ import annotations

import json
import logging
import os
import re
from abc import ABC, abstractmethod
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from tabletalk.domain import (
    ErrorCode,
    QueryAnswer,
    RuntimeStage,
    TableTalkError,
    canonical_digest,
    model_request_error,
)

logger = logging.getLogger("tabletalk")


def _artifact_policy(agent: dict[str, Any], name: str, default: Any) -> Any:
    policies = agent.get("policies", {})
    if isinstance(policies, dict):
        return policies.get(name, default)
    if isinstance(policies, list):
        for item in policies:
            if isinstance(item, list) and len(item) == 2 and item[0] == name:
                return item[1]
    return default


class DatabaseProvider(ABC):
    """Database behavior required by compilation and trusted execution."""

    @abstractmethod
    def execute_query(self, sql_query: str) -> list[dict[str, Any]]:
        """Execute one query and return row mappings."""

    @abstractmethod
    def get_client(self) -> Any:
        """Return the native connection for health and identity inspection."""

    @abstractmethod
    def get_database_type_map(self) -> dict[str, str]:
        """Map native database types into stable compact type identifiers."""

    @abstractmethod
    def get_compact_tables(
        self,
        schema_name: str,
        table_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return relation and column metadata for deterministic compilation."""


class LLMProvider(ABC):
    """One explicitly configured model endpoint."""

    def __init__(self) -> None:
        self.last_usage: dict[str, int] = {}

    @abstractmethod
    def generate_response(self, prompt: str) -> str:
        """Generate a complete text response for compatibility smoke checks."""

    def generate_response_stream(self, prompt: str) -> Generator[str, None, None]:
        yield self.generate_response(prompt)

    def generate_chat_stream(
        self,
        messages: list[dict[str, str]],
    ) -> Generator[str, None, None]:
        user_message = next(
            (
                message["content"]
                for message in reversed(messages)
                if message["role"] == "user"
            ),
            "",
        )
        yield from self.generate_response_stream(user_message)

    def generate_structured(
        self,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
    ) -> dict[str, Any]:
        text = "".join(self.generate_chat_stream(messages))
        try:
            value = json.loads(text)
        except json.JSONDecodeError as error:
            raise TableTalkError(
                ErrorCode.MODEL_OUTPUT_MALFORMED,
                RuntimeStage.GENERATION,
                "Configured model returned malformed structured output.",
            ) from error
        from tabletalk.structured_output import validate_structured_output

        return validate_structured_output(value, json_schema)


class QuerySession:
    """Invoke exact applied artifacts through the structured reliability runtime."""

    def __init__(self, project_folder: str = ".") -> None:
        self.project_folder = str(Path(project_folder).resolve())
        self.config = self._load_config()
        self.llm_provider = self._get_llm_provider()
        self._active_connection_name: str | None = None
        self._db_provider: DatabaseProvider | None = None
        self._db_connection_name: str | None = None

    def _load_config(self) -> dict[str, Any]:
        path = Path(self.project_folder) / "tabletalk.yaml"
        if not path.is_file():
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                f"TableTalk project configuration was not found at '{path}'.",
            )
        try:
            value = yaml.safe_load(path.read_text())
        except (OSError, yaml.YAMLError) as error:
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                "tabletalk.yaml could not be loaded.",
            ) from error
        if not isinstance(value, dict):
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                "tabletalk.yaml must be a mapping.",
            )
        return value

    def _get_llm_provider(self) -> LLMProvider:
        from tabletalk.factories import get_llm_provider

        config = self.config.get("llm")
        if not isinstance(config, dict):
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                "tabletalk.yaml requires an llm mapping.",
            )
        try:
            return get_llm_provider(config)
        except Exception as error:
            raise model_request_error(
                error,
                provider=str(config.get("provider") or "unknown"),
                model=str(config.get("model") or "unknown"),
                stage=RuntimeStage.CONFIGURATION,
            ) from error

    def _database_config(self) -> dict[str, Any]:
        if not self._active_connection_name:
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                "The compiled Agent does not identify a database connection.",
            )
        connections = self.config.get("connections")
        value = (
            connections.get(self._active_connection_name)
            if isinstance(connections, dict)
            else None
        )
        if not isinstance(value, dict):
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                f"Connection '{self._active_connection_name}' was not found.",
                details={"connection": self._active_connection_name},
            )
        resolved = dict(value)
        if "path" in resolved and "database_path" not in resolved:
            resolved["database_path"] = resolved.pop("path")
        database_path = resolved.get("database_path")
        if (
            isinstance(database_path, str)
            and database_path != ":memory:"
            and not Path(database_path).is_absolute()
        ):
            resolved["database_path"] = str(
                (Path(self.project_folder) / database_path).resolve()
            )
        return resolved

    def _database(self) -> DatabaseProvider:
        from tabletalk.factories import get_db_provider

        if (
            self._db_provider is not None
            and self._db_connection_name == self._active_connection_name
        ):
            return self._db_provider
        try:
            provider = get_db_provider(self._database_config())
        except TableTalkError:
            raise
        except Exception as error:
            raise TableTalkError(
                ErrorCode.DATABASE_UNAVAILABLE,
                RuntimeStage.EXECUTION,
                f"Connection '{self._active_connection_name}' is unavailable.",
                retryable=True,
                details={"connection": self._active_connection_name},
            ) from error
        self._db_provider = provider
        self._db_connection_name = self._active_connection_name
        return provider

    def get_db_provider(self) -> DatabaseProvider:
        """Return the active database provider for eval fixture injection."""
        return self._database()

    def _dialect(self) -> str:
        database_type = str(self._database_config().get("type") or "")
        dialect = {
            "sqlite": "sqlite",
            "duckdb": "duckdb",
            "snowflake": "snowflake",
        }.get(database_type)
        if dialect is None:
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                f"Connection type '{database_type}' is unsupported.",
            )
        return dialect

    def _database_identity(self) -> tuple[str, str]:
        config = self._database_config()
        database_type = str(config.get("type") or "unknown")
        if database_type in {"sqlite", "duckdb"}:
            identity = str(config.get("database_path") or ":memory:")
        else:
            identity = "/".join(
                str(config.get(field) or "")
                for field in ("account", "database", "schema")
            )
        return database_type, identity

    def execute_sql(
        self,
        sql: str,
        *,
        artifact: dict[str, Any],
        max_rows: int = 500,
        timeout_seconds: float = 30,
    ) -> list[dict[str, Any]]:
        """Validate, bound, and execute one scoped read-only statement."""
        from tabletalk.runtime import SQLScope, validate_sql

        validated = validate_sql(
            sql,
            dialect=self._dialect(),
            scope=SQLScope.from_artifact(artifact),
            max_rows=max_rows,
        )
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self._database().execute_query, validated.sql)
        try:
            rows = future.result(timeout=timeout_seconds)
        except FuturesTimeoutError as error:
            future.cancel()
            raise TableTalkError(
                ErrorCode.DATABASE_QUERY_FAILED,
                RuntimeStage.EXECUTION,
                f"Query timed out after {timeout_seconds:g} seconds.",
                details={"timeout_seconds": timeout_seconds},
            ) from error
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        return rows[:max_rows]

    def _load_applied_artifact(
        self,
        agent_name: str,
    ) -> tuple[dict[str, Any], list[str]]:
        state_path = Path(self.project_folder) / ".tabletalk" / "state.json"
        if not state_path.is_file():
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                "No applied Agent state exists. Run 'tabletalk apply' first.",
            )
        try:
            state = json.loads(state_path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                "Applied Agent state is unreadable.",
            ) from error
        agents = state.get("agents") if isinstance(state, dict) else None
        entry = agents.get(agent_name) if isinstance(agents, dict) else None
        if not isinstance(entry, dict):
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                f"Applied Agent '{agent_name}' was not found.",
                details={"agent": agent_name},
            )
        digest = entry.get("artifact_digest")
        if not isinstance(digest, str) or not digest:
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                f"Applied Agent '{agent_name}' state is malformed.",
            )
        artifact_root = (
            Path(self.project_folder) / ".tabletalk" / "artifacts"
        ).resolve()
        artifact_path = (
            artifact_root / agent_name / f"{digest}.json"
        ).resolve()
        try:
            artifact_path.relative_to(artifact_root)
            artifact = json.loads(artifact_path.read_text())
        except (ValueError, OSError, json.JSONDecodeError) as error:
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                f"Applied Agent '{agent_name}' artifact is unreadable.",
            ) from error
        agent = artifact.get("agent") if isinstance(artifact, dict) else None
        if (
            not isinstance(agent, dict)
            or artifact.get("digest") != digest
            or canonical_digest(agent) != digest
        ):
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                f"Applied Agent '{agent_name}' failed its integrity check.",
            )
        receipt_digests = entry.get("eval_receipts") or []
        if not isinstance(receipt_digests, list) or not all(
            isinstance(value, str) for value in receipt_digests
        ):
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                f"Applied Agent '{agent_name}' eval receipt state is malformed.",
            )
        return artifact, receipt_digests

    def ask_artifact(
        self,
        artifact: dict[str, Any],
        question: str,
        *,
        eval_receipt_digest: str | None = None,
        database_type_override: str | None = None,
        database_identity_override: str | None = None,
        dialect_override: str | None = None,
    ) -> QueryAnswer:
        """Invoke one exact candidate or applied artifact."""
        from tabletalk.domain import RuntimeIdentity
        from tabletalk.runtime import StructuredQueryRuntime

        agent = artifact.get("agent")
        if not isinstance(agent, dict):
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                "Compiled Agent artifact is malformed.",
            )
        connection = agent.get("connection")
        if not isinstance(connection, str) or not connection:
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                "Compiled Agent artifact has no connection.",
            )
        self._active_connection_name = connection
        llm_config = self.config.get("llm", {})
        database_type, database_identity = self._database_identity()
        max_rows = int(_artifact_policy(agent, "max_rows", 500))
        timeout_seconds = float(
            _artifact_policy(agent, "timeout_seconds", 30)
        )
        runtime = StructuredQueryRuntime(
            model=self.llm_provider,
            execute=lambda sql: self.execute_sql(
                sql,
                artifact=artifact,
                max_rows=max_rows,
                timeout_seconds=timeout_seconds,
            ),
            artifact=artifact,
            runtime_identity=RuntimeIdentity(
                provider=str(llm_config.get("provider") or "unknown"),
                model=str(getattr(self.llm_provider, "model", "unknown")),
                base_url=getattr(self.llm_provider, "base_url", None),
            ),
            database_type=database_type_override or database_type,
            database_identity=database_identity_override or database_identity,
            dialect=dialect_override or self._dialect(),
            eval_receipt_digest=eval_receipt_digest,
        )
        return runtime.invoke(question)

    @staticmethod
    def _redact_text(value: str) -> str:
        return re.sub(
            r"(?i)(api[_-]?key|authorization|credential|password|secret|token)"
            r"(\s*[:=]\s*)[^\s,;]+",
            r"\1\2[REDACTED]",
            value,
        )

    def _write_invocation(
        self,
        agent_name: str,
        question: str,
        answer: QueryAnswer,
    ) -> None:
        now = datetime.now(timezone.utc)
        folder = (
            Path(self.project_folder)
            / ".tabletalk"
            / "history"
            / now.date().isoformat()
        )
        folder.mkdir(parents=True, exist_ok=True)
        invocation_id = str(uuid4())
        record = {
            "schema_version": 1,
            "invocation_id": invocation_id,
            "timestamp": now.isoformat(),
            "agent": agent_name,
            "question": self._redact_text(question),
            "status": answer.status.value,
            "artifact_digest": (
                answer.receipt.artifact_digest if answer.receipt else None
            ),
            "model": (
                answer.receipt.runtime.model if answer.receipt else None
            ),
            "database_type": (
                answer.receipt.database_type if answer.receipt else None
            ),
            "sql": answer.sql,
            "sources": [
                {"relation": source.relation, "columns": list(source.columns)}
                for source in answer.sources
            ],
            "row_count": len(answer.data),
            "repair_attempts": len(answer.repairs),
            "verification": [
                {
                    "claim": claim.claim,
                    "supported": claim.supported,
                    "evidence_ids": list(claim.evidence_ids),
                }
                for claim in answer.claims
            ],
        }
        temporary = folder / f".{invocation_id}.tmp"
        destination = folder / f"{invocation_id}.json"
        temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, destination)

    def ask(self, agent_name: str, question: str) -> QueryAnswer:
        """Ask one applied Agent and persist a secret-safe invocation record."""
        artifact, receipt_digests = self._load_applied_artifact(agent_name)
        answer = self.ask_artifact(
            artifact,
            question,
            eval_receipt_digest=receipt_digests[0] if receipt_digests else None,
        )
        try:
            self._write_invocation(agent_name, question, answer)
        except OSError as error:
            logger.warning("Could not write invocation history: %s", type(error).__name__)
        return answer
