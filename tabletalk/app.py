"""Minimal trust-centered web application for applied TableTalk agents."""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request, send_from_directory

from tabletalk.domain import TableTalkError, to_primitive

logger = logging.getLogger("tabletalk")

static_folder = os.path.join(os.path.dirname(__file__), "static")
app = Flask(__name__, static_folder=static_folder, static_url_path="")

project_folder = os.environ.get("TABLETALK_PROJECT_FOLDER", os.getcwd())
_qs = None
_session_lock = threading.Lock()


def _get_session():
    global _qs
    if _qs is None:
        with _session_lock:
            if _qs is None:
                from tabletalk.interfaces import QuerySession

                _qs = QuerySession(project_folder)
    return _qs


def _state() -> dict[str, Any]:
    path = Path(project_folder, ".tabletalk", "state.json")
    if not path.is_file():
        return {"schema_version": 2, "agents": {}}
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 2, "agents": {}}
    return value if isinstance(value, dict) else {"schema_version": 2, "agents": {}}


def _failure(
    *,
    code: str,
    message: str,
    status: int,
    stage: str = "presentation",
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> tuple[Response, int]:
    return (
        jsonify(
            {
                "failure": {
                    "code": code,
                    "stage": stage,
                    "message": message,
                    "retryable": retryable,
                    "details": details or {},
                }
            }
        ),
        status,
    )


@app.after_request
def _security_headers(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; script-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    )
    return response


@app.get("/")
def index() -> Response:
    return send_from_directory(static_folder, "index.html")


@app.get("/health")
def health() -> Response:
    state = _state()
    applied_agents = state.get("agents")
    count = len(applied_agents) if isinstance(applied_agents, dict) else 0
    return jsonify(
        {
            "status": "ready" if count else "needs_apply",
            "applied_agents": count,
        }
    )


@app.get("/api/config")
def config() -> tuple[Response, int] | Response:
    try:
        session = _get_session()
    except Exception:
        logger.exception("Could not initialize TableTalk web session")
        return _failure(
            code="configuration_failed",
            message="The project runtime configuration could not be loaded.",
            stage="configuration",
            status=503,
        )
    llm = session.config.get("llm", {})
    return jsonify(
        {
            "provider": str(llm.get("provider") or "unknown"),
            "model": str(getattr(session.llm_provider, "model", "unknown")),
            "endpoint": getattr(session.llm_provider, "base_url", None),
            "fallback": "disabled",
        }
    )


@app.get("/api/agents")
def agents() -> Response:
    result = []
    state = _state()
    applied_agents = state.get("agents")
    if isinstance(applied_agents, dict):
        artifacts: list[dict[str, Any]] = []
        artifact_root = Path(project_folder, ".tabletalk", "artifacts").resolve()
        for name, applied in applied_agents.items():
            if not isinstance(name, str) or not isinstance(applied, dict):
                continue
            digest = applied.get("artifact_digest")
            if not isinstance(digest, str):
                continue
            path = (artifact_root / name / f"{digest}.json").resolve()
            try:
                path.relative_to(artifact_root)
                artifact = json.loads(path.read_text())
            except (ValueError, OSError, json.JSONDecodeError):
                continue
            artifacts.append(
                {
                    "name": name,
                    "digest": digest,
                    "eval_receipts": applied.get("eval_receipts") or [],
                    "artifact": artifact,
                }
            )
    else:
        artifacts = []
    for entry in artifacts if isinstance(artifacts, list) else []:
        if not isinstance(entry, dict):
            continue
        artifact = entry.get("artifact")
        agent = artifact.get("agent") if isinstance(artifact, dict) else None
        if not isinstance(agent, dict):
            continue
        relations = agent.get("relations")
        result.append(
            {
                "name": entry.get("name"),
                "agent_name": agent.get("name"),
                "description": agent.get("description") or "",
                "artifact_digest": entry.get("digest"),
                "eval_receipts": entry.get("eval_receipts") or [],
                "relation_count": len(relations) if isinstance(relations, list) else 0,
            }
        )
    return jsonify({"agents": sorted(result, key=lambda item: str(item["name"]))})


@app.post("/api/ask")
def ask() -> tuple[Response, int] | Response:
    if not request.is_json:
        return _failure(
            code="invalid_request",
            message="A JSON request body is required.",
            status=400,
        )
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _failure(
            code="invalid_request",
            message="The request body must be a JSON object.",
            status=400,
        )
    agent = payload.get("agent")
    question = payload.get("question")
    if not isinstance(agent, str) or not agent.strip():
        return _failure(
            code="invalid_request",
            message="agent must be a non-empty string.",
            status=400,
        )
    if (
        not isinstance(question, str)
        or not question.strip()
        or len(question) > 4000
    ):
        return _failure(
            code="invalid_request",
            message="question must contain between 1 and 4000 characters.",
            status=400,
        )
    try:
        answer = _get_session().ask(agent.strip(), question.strip())
    except TableTalkError as error:
        return jsonify({"failure": error.to_dict()}), 422
    except Exception:
        logger.exception("Unexpected trusted-runtime failure")
        return _failure(
            code="unexpected_failure",
            message="The trusted query runtime failed unexpectedly.",
            status=500,
        )
    return jsonify({"answer": to_primitive(answer)})


@app.get("/<path:filename>")
def static_files(filename: str) -> Response:
    return send_from_directory(static_folder, filename)
