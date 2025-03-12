import os
from typing import Tuple

from flask import Flask, Response, jsonify, request, send_from_directory, session

app = Flask(__name__)
app.secret_key = "local_app_secret_key"

# Don't initialize QuerySession at import time
# We'll initialize it lazily in the routes that need it
query_session = None

project_folder = os.getcwd()


@app.route("/")
def serve_index() -> Response:
    return send_from_directory(app.static_folder or "", "index.html")


@app.route("/manifests", methods=["GET"])
def list_manifests() -> Tuple[Response, int] | Response:
    """Return a list of available manifest files."""
    manifest_folder = os.path.join(project_folder, "manifest")
    if not os.path.exists(manifest_folder):
        return jsonify({"error": "Manifest folder not found"}), 404
    manifest_files = [f for f in os.listdir(manifest_folder) if f.endswith(".txt")]
    return jsonify({"manifests": manifest_files})


@app.route("/select_manifest", methods=["POST"])
def select_manifest() -> Tuple[Response, int] | Response:
    """Select a manifest and return its content."""
    data = request.json
    manifest = data.get("manifest") if data else None
    if not manifest:
        return jsonify({"error": "Manifest not provided"}), 400
    manifest_path = os.path.join(project_folder, "manifest", manifest)
    if not os.path.exists(manifest_path):
        return jsonify({"error": "Manifest not found"}), 404
    with open(manifest_path, "r") as f:
        manifest_content = f.read()
    session["manifest"] = manifest
    return jsonify(
        {"message": f"Manifest '{manifest}' selected", "details": manifest_content}
    )


@app.route("/query", methods=["POST"])
def query() -> Tuple[Response, int] | Response:
    """Generate SQL for a question using the selected manifest."""
    from tabletalk.interfaces import QuerySession

    global query_session

    if "manifest" not in session:
        return (
            jsonify({"error": "No manifest selected. Please select a manifest first."}),
            400,
        )
    data = request.json
    question = data.get("question") if data else None
    if not question:
        return jsonify({"error": "Question not provided"}), 400
    manifest_file = session["manifest"]

    try:
        # Lazy initialization of QuerySession
        if query_session is None:
            query_session = QuerySession(project_folder)

        manifest_data = query_session.load_manifest(manifest_file)
        sql = query_session.generate_sql(manifest_data, question)
        return jsonify({"sql": sql})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
