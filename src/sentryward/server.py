"""Local web server for `ward live` (Flask).

  /             the React live screen (built into sentryward/web/live by `npm run build`)
  /dashboard    the full dashboard, rendered once at start-up
  /api/meta     baskets, history and alert settings (does not change while running)
  /api/live     the latest snapshot and when it was taken
  /api/stream   the same, pushed to the browser every second (server-sent events)
  /api/search   ?q= stocks and funds matching a ticker or name
  /api/stock/K  one stock (ticker or issuer key): crowding history and holders
  /api/fund/N   one fund: holdings, changes since its previous filing, closest funds
"""

from __future__ import annotations

import json
import re
import time
from importlib import resources
from pathlib import Path

from typing import Callable

import duckdb
from flask import Flask, Response, jsonify, request, send_file, send_from_directory

from . import explore, youtube
from .live import LiveMonitor

NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>Sentry Ward</title>
<body style="background:#080808;color:#e5e5e5;font-family:monospace;padding:24px">
<p>The live screen has not been built. From the repository: <code>cd web/live && npm ci && npm run build</code>.</p>
<p>The API is running: <a style="color:#f59e0b" href="/api/live">/api/live</a> ·
<a style="color:#f59e0b" href="/dashboard">/dashboard</a></p></body>"""


def static_dir() -> Path:
    return Path(str(resources.files("sentryward") / "web" / "live"))


def create_app(monitor: LiveMonitor, meta: dict, dashboard_html: str | None = None,
               connect: Callable[[], duckdb.DuckDBPyConnection] | None = None, yt=None) -> Flask:
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

    @app.get("/favicon")
    @app.get("/favicon.ico")
    def favicon():
        from .dashboard import icon_file
        found = icon_file()
        if found is None:
            return Response(status=204)  # no icon set; the browser shows its default
        return send_file(found[0], mimetype=found[1], max_age=3600)

    @app.get("/dashboard")
    def dashboard():
        if dashboard_html is None:
            return Response("Dashboard not available.", status=404)
        # Served by `ward live`, the dashboard gets a tab back to the live screen. The published
        # copy (GitHub Pages) has no live screen, so the marker stays an empty comment there.
        page = dashboard_html.replace("<!--__LIVE_TAB__-->", '<a href="/" style="color:var(--orange-hi)">&larr; LIVE</a>', 1)
        page = page.replace('href="#dashboard" data-home', 'href="/" data-home', 1)   # the logo goes home
        return Response(page, mimetype="text/html")

    @app.get("/api/meta")
    def api_meta():
        return jsonify(meta)

    @app.get("/api/stream")
    def api_stream():
        every = max(0.5, float(request.args.get("every", 1)))

        def events():
            last = None
            quiet = 0.0
            while True:
                body = json.dumps(monitor.state(), allow_nan=False, separators=(",", ":"))
                if body != last:
                    yield f"data: {body}\n\n"
                    last, quiet = body, 0.0
                elif quiet >= 15:
                    yield ": keep-alive\n\n"  # stops proxies and browsers from closing an idle stream
                    quiet = 0.0
                time.sleep(every)
                quiet += every

        return Response(events(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

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

    # -- live TV (YouTube Data API, optional)

    def tv(fn):
        if yt is None:
            return jsonify({"error": "Set YOUTUBE_API_KEY to use this.", "enabled": False}), 503
        try:
            return jsonify(fn())
        except PermissionError as e:
            return jsonify({"error": str(e)}), 502
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 429
        except Exception as e:  # network trouble and the like
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 502

    @app.get("/api/tv/status")
    def tv_status():
        return jsonify({"enabled": yt is not None, "used": getattr(yt, "used", 0)})

    @app.get("/api/tv/resolve")
    def tv_resolve():
        link = request.args.get("url", "")[:300]
        return tv(lambda: yt.resolve(link) or {"error": "Not a YouTube channel link."})

    @app.get("/api/tv/related")
    def tv_related():
        q = (request.args.get("q", "").strip()
             or youtube.company_query(request.args.get("issuer"), request.args.get("ticker")))[:80]
        chans = [c for c in request.args.get("channels", "").split(",") if re.fullmatch(r"UC[\w-]{22}", c)][:5]
        if not q or not chans:
            return jsonify({"error": "Need a search term and at least one channel."}), 400
        return tv(lambda: {"videos": yt.related(q, chans)})

    @app.errorhandler(404)
    def not_found(_):
        # Unknown paths under /api are errors; anything else is a page of the React app.
        if request.path.startswith("/api/"):
            return jsonify({"error": "Not found."}), 404
        return index()

    return app
