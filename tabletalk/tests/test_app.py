"""
Tests for tabletalk/app.py (Flask web API)

All LLM calls are mocked via a MockLLMProvider injected into the QuerySession.
All database calls use the real SQLite ecommerce fixture so execution tests
hit a real (in-process) database.

Endpoints tested:
  GET  /
  GET  /manifests
  POST /select_manifest
  POST /chat/stream   (SSE)
  POST /fix/stream    (SSE)
  POST /execute
  POST /suggest
  POST /reset
  GET  /favorites
  POST /favorites
  DELETE /favorites/<name>
  GET  /history
  POST /query  (legacy)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Generator, List
from unittest.mock import MagicMock, patch

import pytest

from tabletalk.tests.conftest import MockLLMProvider


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_sse(text: str) -> List[Dict[str, Any]]:
    """Parse SSE text/event-stream body into a list of event payload dicts."""
    events = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            payload = json.loads(line[6:])
            events.append(payload)
    return events


def _event_types(events: List[Dict[str, Any]]) -> List[str]:
    return [e.get("type") for e in events]


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def flask_app(project_with_manifest):
    """
    Return a configured Flask test client.

    - Uses the project_with_manifest fixture (real SQLite DB + manifests).
    - Injects a MockLLMProvider so no real LLM calls are made.
    """
    import tabletalk.app as app_mod

    # Reset module-level singleton so each test gets a fresh QuerySession
    app_mod._qs = None
    app_mod.project_folder = project_with_manifest

    llm = MockLLMProvider(
        default_response="SELECT * FROM customers LIMIT 10",
        responses={
            "revenue": "SELECT SUM(total_amount) AS revenue FROM orders",
            "suggest": '["How many customers?", "Top products?", "Monthly revenue?"]',
            "fix": "SELECT id FROM customers",
        },
    )

    with patch("tabletalk.factories.get_llm_provider", return_value=llm):
        app_mod.app.config["TESTING"] = True
        app_mod.app.config["SECRET_KEY"] = "test"
        with app_mod.app.test_client() as client:
            yield client

    # Clean up singleton
    app_mod._qs = None


# ── GET / ──────────────────────────────────────────────────────────────────────

class TestServeIndex:
    def test_returns_html(self, flask_app):
        r = flask_app.get("/")
        assert r.status_code == 200
        assert b"html" in r.data.lower()


# ── GET /manifests ─────────────────────────────────────────────────────────────

class TestListManifests:
    def test_lists_manifest_files(self, flask_app):
        r = flask_app.get("/manifests")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert "manifests" in data
        names = data["manifests"]
        assert "customers.txt" in names
        assert "orders.txt" in names

    def test_returns_only_txt_files(self, flask_app):
        r = flask_app.get("/manifests")
        data = json.loads(r.data)
        for name in data["manifests"]:
            assert name.endswith(".txt")

    def test_missing_manifest_folder(self, flask_app, project_with_manifest):
        import shutil
        import tabletalk.app as app_mod

        shutil.rmtree(Path(project_with_manifest) / "manifest")
        r = flask_app.get("/manifests")
        assert r.status_code == 404
        assert "error" in json.loads(r.data)


# ── POST /select_manifest ──────────────────────────────────────────────────────

class TestSelectManifest:
    def test_selects_valid_manifest(self, flask_app):
        r = flask_app.post(
            "/select_manifest",
            json={"manifest": "customers.txt"},
            content_type="application/json",
        )
        assert r.status_code == 200
        data = json.loads(r.data)
        assert "message" in data
        assert "customers" in data["message"]

    def test_returns_manifest_details(self, flask_app):
        r = flask_app.post(
            "/select_manifest",
            json={"manifest": "customers.txt"},
            content_type="application/json",
        )
        data = json.loads(r.data)
        assert "details" in data
        assert "DATA_SOURCE" in data["details"]

    def test_missing_manifest_name(self, flask_app):
        r = flask_app.post(
            "/select_manifest", json={}, content_type="application/json"
        )
        assert r.status_code == 400

    def test_nonexistent_manifest(self, flask_app):
        r = flask_app.post(
            "/select_manifest",
            json={"manifest": "ghost.txt"},
            content_type="application/json",
        )
        assert r.status_code == 404


# ── POST /chat/stream ──────────────────────────────────────────────────────────

class TestChatStream:
    def _select(self, client):
        client.post(
            "/select_manifest",
            json={"manifest": "customers.txt"},
            content_type="application/json",
        )

    def test_basic_stream(self, flask_app):
        self._select(flask_app)
        r = flask_app.post(
            "/chat/stream",
            json={"question": "how many customers?", "auto_execute": True},
            content_type="application/json",
        )
        assert r.status_code == 200
        assert "text/event-stream" in r.content_type

    def test_stream_contains_sql_done(self, flask_app):
        self._select(flask_app)
        r = flask_app.post(
            "/chat/stream",
            json={"question": "list all customers", "auto_execute": False},
            content_type="application/json",
        )
        events = _parse_sse(r.data.decode())
        types = _event_types(events)
        assert "sql_done" in types
        assert "done" in types

    def test_stream_sql_chunk_events(self, flask_app):
        self._select(flask_app)
        r = flask_app.post(
            "/chat/stream",
            json={"question": "list all customers", "auto_execute": False},
            content_type="application/json",
        )
        events = _parse_sse(r.data.decode())
        sql_chunks = [e for e in events if e.get("type") == "sql_chunk"]
        assert len(sql_chunks) > 0

    def test_stream_includes_results_on_execute(self, flask_app):
        self._select(flask_app)
        r = flask_app.post(
            "/chat/stream",
            json={"question": "list customers", "auto_execute": True, "explain": False, "suggest": False},
            content_type="application/json",
        )
        events = _parse_sse(r.data.decode())
        types = _event_types(events)
        # The generated SQL is "SELECT * FROM customers LIMIT 10" — executes against real SQLite
        assert "results" in types or "execute_error" in types

    def test_missing_question_returns_400(self, flask_app):
        self._select(flask_app)
        r = flask_app.post(
            "/chat/stream",
            json={"question": ""},
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_missing_manifest_returns_400(self, flask_app):
        # Don't select a manifest first
        import tabletalk.app as app_mod

        app_mod._qs = None
        r = flask_app.post(
            "/chat/stream",
            json={"question": "test"},
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_manifest_passed_inline(self, flask_app):
        """Manifest can be passed in the request body instead of via /select_manifest."""
        r = flask_app.post(
            "/chat/stream",
            json={
                "question": "list customers",
                "manifest": "customers.txt",
                "auto_execute": False,
            },
            content_type="application/json",
        )
        assert r.status_code == 200


# ── POST /fix/stream ───────────────────────────────────────────────────────────

class TestFixStream:
    def test_fix_stream_returns_corrected_sql(self, flask_app):
        flask_app.post("/select_manifest", json={"manifest": "customers.txt"})
        r = flask_app.post(
            "/fix/stream",
            json={
                "sql": "SELECT x FROM nonexistent_table",
                "error": "no such table: nonexistent_table",
            },
            content_type="application/json",
        )
        assert r.status_code == 200
        events = _parse_sse(r.data.decode())
        types = _event_types(events)
        assert "sql_done" in types
        assert "done" in types

    def test_missing_sql_returns_400(self, flask_app):
        r = flask_app.post(
            "/fix/stream",
            json={"error": "some error"},
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_missing_error_returns_400(self, flask_app):
        r = flask_app.post(
            "/fix/stream",
            json={"sql": "SELECT 1"},
            content_type="application/json",
        )
        assert r.status_code == 400


# ── POST /execute ──────────────────────────────────────────────────────────────

class TestExecute:
    def test_executes_valid_sql(self, flask_app):
        flask_app.post("/select_manifest", json={"manifest": "customers.txt"})
        r = flask_app.post(
            "/execute",
            json={"sql": "SELECT * FROM customers LIMIT 3"},
            content_type="application/json",
        )
        assert r.status_code == 200
        data = json.loads(r.data)
        assert "columns" in data
        assert "rows" in data
        assert data["count"] == 3

    def test_empty_results(self, flask_app):
        flask_app.post("/select_manifest", json={"manifest": "customers.txt"})
        r = flask_app.post(
            "/execute",
            json={"sql": "SELECT * FROM customers WHERE id = 99999"},
            content_type="application/json",
        )
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data["count"] == 0
        assert data["rows"] == []

    def test_invalid_sql_returns_500(self, flask_app):
        flask_app.post("/select_manifest", json={"manifest": "customers.txt"})
        r = flask_app.post(
            "/execute",
            json={"sql": "SELECT * FROM totally_fake_table_xyz"},
            content_type="application/json",
        )
        assert r.status_code == 500
        assert "error" in json.loads(r.data)

    def test_missing_sql_returns_400(self, flask_app):
        r = flask_app.post(
            "/execute", json={}, content_type="application/json"
        )
        assert r.status_code == 400


# ── POST /suggest ──────────────────────────────────────────────────────────────

class TestSuggest:
    def test_returns_questions(self, flask_app):
        import tabletalk.app as app_mod

        # Inject a mock LLM that returns JSON suggestions
        qs = app_mod._get_session()
        qs.llm_provider = MockLLMProvider(
            default_response='["Q1?", "Q2?", "Q3?"]'
        )
        flask_app.post("/select_manifest", json={"manifest": "customers.txt"})
        r = flask_app.post(
            "/suggest",
            json={"manifest": "customers.txt"},
            content_type="application/json",
        )
        data = json.loads(r.data)
        assert "questions" in data

    def test_no_manifest_returns_empty(self, flask_app):
        r = flask_app.post(
            "/suggest", json={}, content_type="application/json"
        )
        data = json.loads(r.data)
        assert data["questions"] == []


# ── POST /reset ────────────────────────────────────────────────────────────────

class TestReset:
    def test_reset_clears_conversation(self, flask_app):
        r = flask_app.post("/reset")
        assert r.status_code == 200
        assert json.loads(r.data)["ok"] is True


# ── Favorites endpoints ────────────────────────────────────────────────────────

class TestFavoritesAPI:
    def test_get_empty_favorites(self, flask_app):
        r = flask_app.get("/favorites")
        data = json.loads(r.data)
        assert "favorites" in data
        assert isinstance(data["favorites"], list)

    def test_save_and_retrieve(self, flask_app):
        flask_app.post(
            "/favorites",
            json={
                "name": "top_customers",
                "manifest": "customers.txt",
                "question": "top 5 customers?",
                "sql": "SELECT * FROM customers LIMIT 5",
            },
            content_type="application/json",
        )
        r = flask_app.get("/favorites")
        data = json.loads(r.data)
        names = [f["name"] for f in data["favorites"]]
        assert "top_customers" in names

    def test_save_missing_name_returns_400(self, flask_app):
        r = flask_app.post(
            "/favorites",
            json={"sql": "SELECT 1"},
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_save_missing_sql_returns_400(self, flask_app):
        r = flask_app.post(
            "/favorites",
            json={"name": "my_fav"},
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_delete_favorite(self, flask_app):
        flask_app.post(
            "/favorites",
            json={"name": "to_delete", "sql": "SELECT 1", "question": "?", "manifest": "c.txt"},
        )
        r = flask_app.delete("/favorites/to_delete")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data["ok"] is True

    def test_delete_nonexistent_favorite(self, flask_app):
        r = flask_app.delete("/favorites/does_not_exist")
        data = json.loads(r.data)
        assert data["ok"] is False


# ── GET /history ───────────────────────────────────────────────────────────────

class TestHistoryAPI:
    def test_empty_history(self, flask_app):
        r = flask_app.get("/history")
        data = json.loads(r.data)
        assert "history" in data

    def test_history_appears_after_query(self, flask_app):
        flask_app.post("/select_manifest", json={"manifest": "customers.txt"})
        # Use the legacy /query endpoint which saves history synchronously
        flask_app.post(
            "/query",
            json={"question": "count customers"},
            content_type="application/json",
        )
        r = flask_app.get("/history")
        data = json.loads(r.data)
        assert len(data["history"]) >= 1

    def test_limit_parameter(self, flask_app):
        r = flask_app.get("/history?limit=5")
        data = json.loads(r.data)
        assert isinstance(data["history"], list)
        assert len(data["history"]) <= 5


# ── POST /query (legacy) ───────────────────────────────────────────────────────

class TestLegacyQuery:
    def test_returns_sql(self, flask_app):
        flask_app.post("/select_manifest", json={"manifest": "customers.txt"})
        r = flask_app.post(
            "/query",
            json={"question": "list all customers"},
            content_type="application/json",
        )
        assert r.status_code == 200
        data = json.loads(r.data)
        assert "sql" in data
        assert len(data["sql"]) > 0

    def test_no_manifest_returns_400(self, flask_app):
        import tabletalk.app as app_mod
        app_mod._qs = None
        r = flask_app.post(
            "/query",
            json={"question": "test"},
            content_type="application/json",
        )
        assert r.status_code == 400


# ── GET /health ────────────────────────────────────────────────────────────────

class TestHealth:
    def test_ok_when_manifests_exist(self, flask_app):
        r = flask_app.get("/health")
        data = json.loads(r.data)
        assert r.status_code == 200
        assert data["status"] == "ok"

    def test_degraded_when_no_manifest_folder(self, tmp_path):
        """Returns 503 when the project has no manifest directory."""
        import tabletalk.app as app_mod

        app_mod._qs = None
        app_mod.project_folder = str(tmp_path)  # fresh dir, no manifest/

        with patch("tabletalk.factories.get_llm_provider", return_value=MockLLMProvider()):
            app_mod.app.config["TESTING"] = True
            with app_mod.app.test_client() as client:
                r = client.get("/health")
                data = json.loads(r.data)
                assert r.status_code == 503
                assert data["status"] == "degraded"

        app_mod._qs = None
