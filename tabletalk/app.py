import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple, Union

from flask import Flask, Response, jsonify, request, send_from_directory, session
from flask import stream_with_context

logger = logging.getLogger("tabletalk")

static_folder = os.path.join(os.path.dirname(__file__), "static")
app = Flask(__name__, static_folder=static_folder, static_url_path="")
app.secret_key = os.environ.get("TABLETALK_SECRET_KEY", "local_dev_secret_key")

project_folder = os.getcwd()

_qs = None  # lazy QuerySession singleton
_MAX_CONV_MESSAGES = 20  # keep last 20 messages (~10 turns) in session


def _get_session():
    global _qs
    if _qs is None:
        from tabletalk.interfaces import QuerySession
        _qs = QuerySession(project_folder)
    return _qs


# ── Health check ───────────────────────────────────────────────────────────────

@app.route("/health")
def health() -> Union[Tuple[Response, int], Response]:
    """
    Liveness/readiness probe.

    Returns 200 if the project directory is accessible and at least one
    manifest exists; 503 otherwise — suitable for Docker HEALTHCHECK and
    k8s probes.
    """
    issues = []
    manifest_folder = os.path.join(project_folder, "manifest")
    if not os.path.isdir(manifest_folder):
        issues.append("manifest folder missing — run 'tabletalk apply'")
    else:
        manifests = [f for f in os.listdir(manifest_folder) if f.endswith(".txt")]
        if not manifests:
            issues.append("no manifests found — run 'tabletalk apply'")

    if issues:
        return jsonify({"status": "degraded", "issues": issues}), 503
    return jsonify({"status": "ok", "project": project_folder})


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


# ── Main chat endpoint (streaming) ────────────────────────────────────────────

@app.route("/chat/stream", methods=["POST"])
def chat_stream() -> Union[Tuple[Response, int], Response]:
    """
    Primary endpoint: streams SQL generation → execution → explanation in one SSE stream.

    SSE event types:
      {"type": "sql_chunk",     "content": "..."}   — streaming SQL token
      {"type": "sql_done",      "sql": "..."}        — complete SQL
      {"type": "results",       "columns": [...], "rows": [...], "count": N}
      {"type": "execute_error", "error": "...", "sql": "..."}
      {"type": "explain_chunk", "content": "..."}    — streaming explanation token
      {"type": "explain_done"}
      {"type": "suggestions",   "questions": [...]}  — 3 follow-up suggestions
      {"type": "error",         "error": "..."}      — fatal error
      {"type": "done"}
    """
    data = request.json or {}
    question = data.get("question", "").strip()
    manifest_file = data.get("manifest") or session.get("manifest")
    auto_execute: bool = data.get("auto_execute", True)
    do_explain: bool = data.get("explain", True)
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

    conv: List[Dict[str, str]] = list(session.get("conversation", []))

    def _evt(payload: Dict[str, Any]) -> str:
        return f"data: {json.dumps(payload)}\n\n"

    def generate():
        nonlocal conv
        sql_parts: List[str] = []

        # 1 ── Stream SQL generation
        try:
            for chunk in qs.generate_sql_conversational(manifest_data, question, conv):
                sql_parts.append(chunk)
                yield _evt({"type": "sql_chunk", "content": chunk})
        except Exception as e:
            yield _evt({"type": "error", "error": str(e)})
            yield _evt({"type": "done"})
            return

        sql = qs._clean_sql("".join(sql_parts))
        yield _evt({"type": "sql_done", "sql": sql})

        # Update conversation history
        conv = conv + [
            {"role": "user", "content": question},
            {"role": "assistant", "content": sql},
        ]
        conv = conv[-_MAX_CONV_MESSAGES:]
        session["conversation"] = conv

        qs.save_history(manifest_file, question, sql)

        # 2 ── Execute
        results: List[Dict[str, Any]] = []
        if auto_execute:
            try:
                results = qs.execute_sql(sql)
                columns = list(results[0].keys()) if results else []
                rows = [
                    [("" if v is None else str(v)) for v in row.values()]
                    for row in results[:500]
                ]
                yield _evt(
                    {"type": "results", "columns": columns, "rows": rows, "count": len(results)}
                )
            except Exception as e:
                yield _evt({"type": "execute_error", "error": str(e), "sql": sql})
                yield _evt({"type": "done"})
                return

        # 3 ── Explain
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
            [("" if v is None else str(v)) for v in row.values()]
            for row in results[:500]
        ]
        return jsonify({"columns": columns, "rows": rows, "count": len(results)})
    except Exception as e:
        logger.error(f"Execute error: {e}")
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


# ── Config ─────────────────────────────────────────────────────────────────────

@app.route("/config")
def get_config() -> Union[Tuple[Response, int], Response]:
    """Return LLM provider and model name for display in the UI."""
    try:
        qs = _get_session()
        llm = qs.config.get("llm", {})
        return jsonify({
            "provider": llm.get("provider", "unknown"),
            "model": llm.get("model", "unknown"),
        })
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
        chunks = list(qs.generate_sql_conversational(manifest_data, question, conv))
        sql = qs._clean_sql("".join(chunks))
        qs.save_history(manifest_file, question, sql)
        return jsonify({"sql": sql})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
