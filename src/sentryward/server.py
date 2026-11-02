"""Local web server for `ward live` (Flask).

  /             the React live screen (built into sentryward/web/live by `npm run build`)
  /dashboard    the full dashboard, rendered once at start-up
  /api/meta     baskets, history and alert settings (does not change while running)
  /api/live     the latest snapshot and when it was taken
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from flask import Flask, Response, jsonify, send_from_directory

from .live import LiveMonitor

NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>Sentry Ward</title>
<body style="background:#080808;color:#e5e5e5;font-family:monospace;padding:24px">
<p>The live screen has not been built. From the repository: <code>cd web/live && npm ci && npm run build</code>.</p>
<p>The API is running: <a style="color:#f59e0b" href="/api/live">/api/live</a> ·
<a style="color:#f59e0b" href="/dashboard">/dashboard</a></p></body>"""


def static_dir() -> Path:
    return Path(str(resources.files("sentryward") / "web" / "live"))


def create_app(monitor: LiveMonitor, meta: dict, dashboard_html: str | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    root = static_dir()

    @app.get("/")
    def index():
        if (root / "index.html").exists():
            return send_from_directory(root, "index.html")
        return Response(NOT_BUILT, mimetype="text/html")

    @app.get("/assets/<path:name>")
    def assets(name):
        return send_from_directory(root / "assets", name)

    @app.get("/dashboard")
    def dashboard():
        if dashboard_html is None:
            return Response("Dashboard not available.", status=404)
        return Response(dashboard_html, mimetype="text/html")

    @app.get("/api/meta")
    def api_meta():
        return jsonify(meta)

    @app.get("/api/live")
    def api_live():
        resp = jsonify(monitor.state())
        resp.headers["Cache-Control"] = "no-store"
        return resp

    return app
