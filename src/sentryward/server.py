"""Local web server for `ward live` (Flask).

  /             the React live screen (built into sentryward/web/live by `npm run build`)
  /dashboard    the full dashboard, rendered once at start-up
  /api/meta     baskets, history and alert settings (does not change while running)
  /api/live     the latest snapshot and when it was taken
  /api/search   ?q= stocks and funds matching a ticker or name
  /api/stock/K  one stock (ticker or issuer key): crowding history and holders
  /api/fund/N   one fund: holdings, changes since its previous filing, closest funds
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from typing import Callable

import duckdb
from flask import Flask, Response, jsonify, request, send_from_directory

from . import explore
from .live import LiveMonitor

NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>Sentry Ward</title>
<body style="background:#080808;color:#e5e5e5;font-family:monospace;padding:24px">
<p>The live screen has not been built. From the repository: <code>cd web/live && npm ci && npm run build</code>.</p>
<p>The API is running: <a style="color:#f59e0b" href="/api/live">/api/live</a> ·
<a style="color:#f59e0b" href="/dashboard">/dashboard</a></p></body>"""


def static_dir() -> Path:
    return Path(str(resources.files("sentryward") / "web" / "live"))


def create_app(monitor: LiveMonitor, meta: dict, dashboard_html: str | None = None,
               connect: Callable[[], duckdb.DuckDBPyConnection] | None = None) -> Flask:
    """`connect` opens a read-only database connection for the stock, fund and search pages.
    Each request opens and closes its own, so other `ward` commands can still write between requests."""
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

    def with_db(fn, *args):
        if connect is None:
            return jsonify({"error": "No database connected."}), 503
        try:
            con = connect()
        except duckdb.Error as e:  # usually another ward command is writing
            return jsonify({"error": f"Database busy or unavailable: {e}"}), 503
        try:
            out = fn(con, *args)
        finally:
            con.close()
        if out is None:
            return jsonify({"error": "Not found."}), 404
        return jsonify(out)

    @app.get("/api/search")
    def api_search():
        return with_db(explore.search, request.args.get("q", "")[:80])

    @app.get("/api/stock/<path:key>")
    def api_stock(key):
        return with_db(explore.stock, key)

    @app.get("/api/fund/<path:name>")
    def api_fund(name):
        return with_db(explore.fund, name)

    @app.errorhandler(404)
    def not_found(_):
        # Unknown paths under /api are errors; anything else is a page of the React app.
        if request.path.startswith("/api/"):
            return jsonify({"error": "Not found."}), 404
        return index()

    return app
