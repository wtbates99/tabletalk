"""
app.py — Flask web application.

Improvements in this file:
  item  8 — Auto-reload: manifest cache is invalidated when context files change
  item 10 — /export endpoint: download results as CSV or JSON
  item 13 — explain toggle: honour explain/suggest flags from the client
  item 15 — /api/query REST endpoint for programmatic integration
  item 20 — Rate limiting: per-session sliding-window cap on /chat/stream
  item 27 — Enhanced /health: checks DB connectivity and LLM config
"""
import csv
import io
import json
import logging
import os
import time
from collections import defaultdict
from typing import Any, Dict, List, Tuple, Union

from flask import Flask, Response, jsonify, request, send_from_directory, session
from flask import stream_with_context

logger = logging.getLogger("tabletalk")

static_folder = os.path.join(os.path.dirname(__file__), "static")
app = Flask(__name__, static_folder=static_folder, static_url_path="")
app.secret_key = os.environ.get("TABLETALK_SECRET_KEY", "local_dev_secret_key")

project_folder = os.getcwd()

_qs = None  # lazy QuerySession singleton

# ── Rate limiting (item 20) ────────────────────────────────────────────────────
# Simple in-memory sliding-window limiter. Configurable via env vars so it
# survives config reloads without a restart.

_rate_limit_store: Dict[str, List[float]] = defaultdict(list)
_RATE_LIMIT_MAX = int(os.environ.get("TABLETALK_RATE_LIMIT", "30"))   # requests
_RATE_LIMIT_WINDOW = int(os.environ.get("TABLETALK_RATE_WINDOW", "60"))  # seconds


def _check_rate_limit(key: str) -> bool:
    """Sliding-window check — returns True if the request is allowed."""
    now = time.time()
    cutoff = now - _RATE_LIMIT_WINDOW
    bucket = _rate_limit_store[key]
    _rate_limit_store[key] = [t for t in bucket if t > cutoff]
    if len(_rate_limit_store[key]) >= _RATE_LIMIT_MAX:
        return False
    _rate_limit_store[key].append(now)
    return True


# ── Session singleton with auto-reload (item 8) ────────────────────────────────

_STALENESS_CHECK_INTERVAL = 30  # seconds between filesystem staleness checks


def _get_session():
    global _qs
    if _qs is None:
        from tabletalk.interfaces import QuerySession

        _qs = QuerySession(project_folder)
        _qs._last_staleness_check: float = time.time()
    else:
        # Periodically check if context files are newer than cached manifests
        now = time.time()
        if now - getattr(_qs, "_last_staleness_check", 0) > _STALENESS_CHECK_INTERVAL:
            _qs._last_staleness_check = now
            from tabletalk.utils import check_manifest_staleness

            if check_manifest_staleness(project_folder):
                logger.info("Context files changed — invalidating manifest cache.")
                _qs.invalidate_manifest_cache()
    return _qs


# ── Health check (item 27) ─────────────────────────────────────────────────────


@app.route("/health")
def health() -> Union[Tuple[Response, int], Response]:
    """
    Enhanced liveness/readiness probe.

    Checks:
      1. Manifest folder exists and contains at least one manifest
      2. DB provider is reachable (if configured)
      3. LLM config is present and has required fields

    Returns 200 / 503 — suitable for Docker HEALTHCHECK and k8s probes.
    """
    issues: List[str] = []
    details: Dict[str, Any] = {}

    # 1. Manifests
    manifest_folder = os.path.join(project_folder, "manifest")
    if not os.path.isdir(manifest_folder):
        issues.append("manifest folder missing — run 'tabletalk apply'")
    else:
        manifests = [f for f in os.listdir(manifest_folder) if f.endswith(".txt")]
        if not manifests:
            issues.append("no manifests found — run 'tabletalk apply'")
        else:
            details["manifests"] = len(manifests)

    # 2. DB connectivity
    try:
        qs = _get_session()
        db = qs.get_db_provider()
        if db is not None:
            db.get_client()  # will raise if connection is dead
            details["database"] = "ok"
        else:
            details["database"] = "not configured"
    except Exception as exc:
        issues.append(f"database unreachable: {exc}")
        details["database"] = "error"

    # 3. LLM config (config validation only — no live API call)
    try:
        qs = _get_session()
        llm_cfg = qs.config.get("llm", {})
        if not llm_cfg.get("provider"):
            issues.append("llm.provider not set in tabletalk.yaml")
        details["llm_provider"] = llm_cfg.get("provider", "unknown")
        details["llm_model"] = llm_cfg.get("model", "unknown")
    except Exception as exc:
        issues.append(f"LLM config error: {exc}")

    if issues:
        return jsonify({"status": "degraded", "issues": issues, "details": details}), 503
    return jsonify({"status": "ok", "project": project_folder, "details": details})


# ── Static ─────────────────────────────────────────────────────────────────────


@app.route("/")
def serve_index() -> Union[Response, Tuple[Response, int]]:
    try:
        if not app.static_folder:
            return jsonify({"error": "Static folder not configured"}), 404
        return send_from_directory(app.static_folder, "index.html")
    except Exception as e:
        return jsonify({"error": str(e)}), 404


# ── Manifests ──────────────────────────────────────────────────────────────────


@app.route("/manifests")
def list_manifests() -> Union[Tuple[Response, int], Response]:
    manifest_folder = os.path.join(project_folder, "manifest")
    if not os.path.exists(manifest_folder):
        return jsonify({"error": "Manifest folder not found. Run 'tabletalk apply'."}), 404
    files = sorted(f for f in os.listdir(manifest_folder) if f.endswith(".txt"))
    return jsonify({"manifests": files})


@app.route("/select_manifest", methods=["POST"])
def select_manifest() -> Union[Tuple[Response, int], Response]:
    data = request.json or {}
    manifest = data.get("manifest")
    if not manifest:
        return jsonify({"error": "Manifest not provided"}), 400
    path = os.path.join(project_folder, "manifest", manifest)
    if not os.path.exists(path):
        return jsonify({"error": "Manifest not found"}), 404
    with open(path) as f:
        content = f.read()
    session["manifest"] = manifest
    session["conversation"] = []  # reset conversation on manifest switch
    return jsonify({"message": f"Manifest '{manifest}' selected", "details": content})


# ── Main chat endpoint (streaming) ─────────────────────────────────────────────


@app.route("/chat/stream", methods=["POST"])
def chat_stream() -> Union[Tuple[Response, int], Response]:
    """
    Primary endpoint: streams SQL generation → execution → explanation in one SSE stream.

    SSE event types:
      {"type": "sql_chunk",     "content": "..."}
      {"type": "sql_done",      "sql": "...", "generation_ms": N}
      {"type": "results",       "columns": [...], "rows": [...], "count": N, "execution_ms": N}
      {"type": "execute_error", "error": "...", "sql": "..."}
      {"type": "explain_chunk", "content": "..."}
      {"type": "explain_done"}
      {"type": "suggestions",   "questions": [...]}
      {"type": "error",         "error": "..."}
      {"type": "done"}
    """
    # Rate limiting (item 20)
    rate_key = session.get("_id") or request.remote_addr or "anon"
    if not _check_rate_limit(str(rate_key)):
        return (
            jsonify(
                {
                    "error": (
                        f"Rate limit exceeded: {_RATE_LIMIT_MAX} requests "
                        f"per {_RATE_LIMIT_WINDOW}s. Try again shortly."
                    )
                }
            ),
            429,
        )

    data = request.json or {}
    question = data.get("question", "").strip()
    manifest_file = data.get("manifest") or session.get("manifest")
    auto_execute: bool = data.get("auto_execute", True)
    do_explain: bool = data.get("explain", True)      # item 13 — explanation toggle
    do_suggest: bool = data.get("suggest", True)

    if not question:
        return jsonify({"error": "Question not provided"}), 400
    if not manifest_file:
        return jsonify({"error": "No manifest selected"}), 400

    try:
        qs = _get_session()
        manifest_data = qs.load_manifest(manifest_file)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    max_conv = qs.max_conv_messages
    conv: List[Dict[str, str]] = list(session.get("conversation", []))

    def _evt(payload: Dict[str, Any]) -> str:
        return f"data: {json.dumps(payload)}\n\n"

    def generate():
        nonlocal conv
        sql_parts: List[str] = []
        gen_start = time.monotonic()

        # 1 ── Stream SQL generation
        try:
            for chunk in qs.generate_sql_conversational(manifest_data, question, conv):
                sql_parts.append(chunk)
                yield _evt({"type": "sql_chunk", "content": chunk})
        except Exception as e:
            yield _evt({"type": "error", "error": str(e)})
            yield _evt({"type": "done"})
            return

        generation_ms = (time.monotonic() - gen_start) * 1000
        sql = qs._clean_sql("".join(sql_parts))

        # Surface token usage when available (item 25)
        usage = getattr(qs.llm_provider, "last_usage", {})
        yield _evt(
            {
                "type": "sql_done",
                "sql": sql,
                "generation_ms": round(generation_ms, 1),
                **({k: v for k, v in usage.items()} if usage else {}),
            }
        )

        # Update conversation history (item 11 — respect max_conv_messages)
        conv = (
            conv
            + [
                {"role": "user", "content": question},
                {"role": "assistant", "content": sql},
            ]
        )[-max_conv:]
        session["conversation"] = conv

        # 2 ── Execute
        results: List[Dict[str, Any]] = []
        execution_ms = 0.0
        if auto_execute:
            exec_start = time.monotonic()
            try:
                results = qs.execute_sql(sql)
                execution_ms = (time.monotonic() - exec_start) * 1000
                columns = list(results[0].keys()) if results else []
                rows = [
                    [("" if v is None else str(v)) for v in row.values()]
                    for row in results
                ]
                yield _evt(
                    {
                        "type": "results",
                        "columns": columns,
                        "rows": rows,
                        "count": len(results),
                        "execution_ms": round(execution_ms, 1),
                    }
                )
            except Exception as e:
                yield _evt({"type": "execute_error", "error": str(e), "sql": sql})
                yield _evt({"type": "done"})
                return

        # Persist history with metrics (items 25, 26)
        from tabletalk.interfaces import QueryMetrics

        metrics = QueryMetrics(
            generation_ms=generation_ms,
            execution_ms=execution_ms,
            row_count=len(results),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )
        qs.save_history(manifest_file, question, sql, metrics=metrics)

        # 3 ── Explain (item 13 — togglable)
        if do_explain and results:
            try:
                for chunk in qs.explain_results_stream(question, sql, results):
                    yield _evt({"type": "explain_chunk", "content": chunk})
                yield _evt({"type": "explain_done"})
            except Exception:
                pass

        # 4 ── Suggestions
        if do_suggest:
            try:
                suggestions = qs.suggest_questions(manifest_data, conv[-6:])
                if suggestions:
                    yield _evt({"type": "suggestions", "questions": suggestions})
            except Exception:
                pass

        yield _evt({"type": "done"})

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Fix SQL ────────────────────────────────────────────────────────────────────


@app.route("/fix/stream", methods=["POST"])
def fix_stream() -> Union[Tuple[Response, int], Response]:
    """Stream a corrected SQL query given a failing query and its error."""
    data = request.json or {}
    sql = data.get("sql", "")
    error = data.get("error", "")
    manifest_file = data.get("manifest") or session.get("manifest")

    if not sql or not error:
        return jsonify({"error": "sql and error are required"}), 400

    try:
        qs = _get_session()
        manifest_data = qs.load_manifest(manifest_file) if manifest_file else ""
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    def _evt(payload: Dict[str, Any]) -> str:
        return f"data: {json.dumps(payload)}\n\n"

    def generate():
        parts: List[str] = []
        try:
            for chunk in qs.fix_sql_stream(sql, error, manifest_data):
                parts.append(chunk)
                yield _evt({"type": "sql_chunk", "content": chunk})
            fixed = qs._clean_sql("".join(parts))
            yield _evt({"type": "sql_done", "sql": fixed})
        except Exception as e:
            yield _evt({"type": "error", "error": str(e)})
        yield _evt({"type": "done"})

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Execute ────────────────────────────────────────────────────────────────────


@app.route("/execute", methods=["POST"])
def execute_query() -> Union[Tuple[Response, int], Response]:
    data = request.json or {}
    sql = data.get("sql", "").strip()
    if not sql:
        return jsonify({"error": "SQL not provided"}), 400
    try:
        qs = _get_session()
        results = qs.execute_sql(sql)
        if not results:
            return jsonify({"columns": [], "rows": [], "count": 0})
        columns = list(results[0].keys())
        rows = [
            [("" if v is None else str(v)) for v in row.values()] for row in results
        ]
        return jsonify({"columns": columns, "rows": rows, "count": len(results)})
    except Exception as e:
        logger.error(f"Execute error: {e}")
        return jsonify({"error": str(e)}), 500


# ── Export (item 10) ───────────────────────────────────────────────────────────


@app.route("/export", methods=["POST"])
def export_results() -> Union[Tuple[Response, int], Response]:
    """
    Execute SQL and stream back a downloadable CSV or JSON file.

    Request body:
        {
            "sql": "SELECT ...",
            "format": "csv" | "json",   // default: "csv"
            "filename": "my_export"     // default: "results"
        }
    """
    data = request.json or {}
    sql = data.get("sql", "").strip()
    fmt = data.get("format", "csv").lower()
    filename = data.get("filename", "results").replace("/", "_")

    if not sql:
        return jsonify({"error": "SQL not provided"}), 400
    if fmt not in ("csv", "json"):
        return jsonify({"error": "format must be 'csv' or 'json'"}), 400

    try:
        qs = _get_session()
        results = qs.execute_sql(sql)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if fmt == "csv":
        buf = io.StringIO()
        if results:
            writer = csv.DictWriter(buf, fieldnames=list(results[0].keys()))
            writer.writeheader()
            writer.writerows(results)
        return Response(
            buf.getvalue().encode("utf-8"),
            mimetype="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}.csv"',
                "Content-Type": "text/csv; charset=utf-8",
            },
        )

    # JSON
    return Response(
        json.dumps(results, default=str, indent=2).encode("utf-8"),
        mimetype="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}.json"',
        },
    )


# ── REST API endpoint (item 15) ────────────────────────────────────────────────


@app.route("/api/query", methods=["POST"])
def api_query() -> Union[Tuple[Response, int], Response]:
    """
    Simple synchronous REST endpoint for programmatic integration with
    other tools, dashboards, or scripts.

    Request body:
        {
            "question": "Show top 10 customers by revenue",
            "manifest": "sales.txt",    // or use a previously selected manifest
            "execute": true             // default: true — run the generated SQL
        }

    Response:
        {
            "sql": "SELECT ...",
            "columns": ["col1", ...],   // present when execute: true
            "rows": [["v1", ...]],      // present when execute: true
            "count": N
        }
    """
    data = request.json or {}
    question = data.get("question", "").strip()
    manifest_file = data.get("manifest") or session.get("manifest")
    do_execute: bool = data.get("execute", True)

    if not question:
        return jsonify({"error": "question is required"}), 400
    if not manifest_file:
        return jsonify({"error": "manifest is required (pass 'manifest' in the body or call /select_manifest first)"}), 400

    try:
        qs = _get_session()
        manifest_data = qs.load_manifest(manifest_file)
        sql = qs.generate_sql(manifest_data, question)
        result: Dict[str, Any] = {"sql": sql}

        if do_execute:
            rows = qs.execute_sql(sql)
            columns = list(rows[0].keys()) if rows else []
            result["columns"] = columns
            result["rows"] = [
                [("" if v is None else str(v)) for v in r.values()] for r in rows
            ]
            result["count"] = len(rows)

        return jsonify(result)
    except Exception as e:
        logger.error(f"API query error: {e}")
        return jsonify({"error": str(e)}), 500


# ── Suggestions ────────────────────────────────────────────────────────────────


@app.route("/suggest", methods=["POST"])
def suggest() -> Union[Tuple[Response, int], Response]:
    data = request.json or {}
    manifest_file = data.get("manifest") or session.get("manifest")
    if not manifest_file:
        return jsonify({"questions": []})
    try:
        qs = _get_session()
        manifest_data = qs.load_manifest(manifest_file)
        conv = session.get("conversation", [])
        questions = qs.suggest_questions(manifest_data, conv[-6:])
        return jsonify({"questions": questions})
    except Exception as e:
        logger.error(f"Suggest error: {e}")
        return jsonify({"questions": []})


# ── Conversation ───────────────────────────────────────────────────────────────


@app.route("/reset", methods=["POST"])
def reset_conversation() -> Union[Tuple[Response, int], Response]:
    session["conversation"] = []
    return jsonify({"ok": True})


# ── Favorites ──────────────────────────────────────────────────────────────────


@app.route("/favorites", methods=["GET"])
def get_favorites() -> Union[Tuple[Response, int], Response]:
    try:
        qs = _get_session()
        return jsonify({"favorites": qs.get_favorites()})
    except Exception as e:
        return jsonify({"favorites": [], "error": str(e)})


@app.route("/favorites", methods=["POST"])
def save_favorite() -> Union[Tuple[Response, int], Response]:
    data = request.json or {}
    name = data.get("name", "").strip()
    manifest = data.get("manifest", "") or session.get("manifest", "")
    question = data.get("question", "")
    sql = data.get("sql", "")
    if not name or not sql:
        return jsonify({"error": "name and sql are required"}), 400
    try:
        qs = _get_session()
        qs.save_favorite(name, manifest, question, sql)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/favorites/<name>", methods=["DELETE"])
def delete_favorite(name: str) -> Union[Tuple[Response, int], Response]:
    try:
        qs = _get_session()
        deleted = qs.delete_favorite(name)
        return jsonify({"ok": deleted})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── History ────────────────────────────────────────────────────────────────────


@app.route("/history")
def get_history() -> Union[Tuple[Response, int], Response]:
    limit = request.args.get("limit", 30, type=int)
    try:
        qs = _get_session()
        entries = list(reversed(qs.get_history(limit=limit)))
        return jsonify({"history": entries})
    except Exception as e:
        return jsonify({"history": [], "error": str(e)})


# ── Stats (item 25 — token/latency aggregates) ─────────────────────────────────


@app.route("/stats")
def get_stats() -> Union[Tuple[Response, int], Response]:
    """Return aggregate token usage and query latency stats."""
    limit = request.args.get("limit", 100, type=int)
    try:
        qs = _get_session()
        return jsonify(qs.get_usage_stats(limit=limit))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Config ─────────────────────────────────────────────────────────────────────


@app.route("/config")
def get_config() -> Union[Tuple[Response, int], Response]:
    """Return LLM provider, model name, and active limits for display in the UI."""
    try:
        qs = _get_session()
        llm = qs.config.get("llm", {})
        return jsonify(
            {
                "provider": llm.get("provider", "unknown"),
                "model": llm.get("model", "unknown"),
                "safe_mode": qs.config.get("safe_mode", False),
                "max_rows": qs.max_rows,
                "query_timeout": qs.query_timeout,
                "max_conv_messages": qs.max_conv_messages,
            }
        )
    except Exception:
        return jsonify({"provider": "unknown", "model": "unknown"})


# ── Legacy non-streaming query (kept for backward compat) ─────────────────────


@app.route("/query", methods=["POST"])
def query_legacy() -> Union[Tuple[Response, int], Response]:
    data = request.json or {}
    question = data.get("question", "").strip()
    manifest_file = session.get("manifest")
    if not manifest_file:
        return jsonify({"error": "No manifest selected"}), 400
    if not question:
        return jsonify({"error": "Question not provided"}), 400
    try:
        qs = _get_session()
        manifest_data = qs.load_manifest(manifest_file)
        conv = session.get("conversation", [])
        from tabletalk.interfaces import _collect_stream

        sql_raw, _ = _collect_stream(
            qs.generate_sql_conversational(manifest_data, question, conv)
        )
        sql = qs._clean_sql(sql_raw)
        qs.save_history(manifest_file, question, sql)
        return jsonify({"sql": sql})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
