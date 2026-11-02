"""The dashboard command on the sample data plus fake market data."""

from __future__ import annotations

import json
import re

import duckdb

from sentryward import clean, dashboard, market, metrics, sec13f
from test_market import FakeFigi, FakeSec, fake_yahoo


def build(raw_dir, universe_csv, monkeypatch, with_market=True):
    monkeypatch.setenv("SEC_USER_AGENT", "Test Person test@example.com")
    monkeypatch.delenv(market.OPENFIGI_KEY_ENV, raising=False)
    con = duckdb.connect()
    sec13f.load_all(con, raw_dir.glob("*.zip"))
    clean.build_all(con)
    metrics.build_all(con, universe_csv)
    if with_market:
        market.map_cusips(con, session=FakeFigi(), log=lambda m: None, sleep=lambda s: None)
        market.fetch_prices(con, downloader=fake_yahoo, log=lambda m: None, sleep=lambda s: None)
        market.fetch_shares(con, session=FakeSec(), log=lambda m: None, sleep=lambda s: None)
        market.build_market(con)
        metrics.build_stock_crowding(con)
    return con


def embedded(html: str) -> dict:
    m = re.search(r"const DATA = (.*?);\n", html)
    return json.loads(m.group(1).replace("<\\/", "</"))


def test_dashboard_file(raw_dir, universe_csv, monkeypatch, tmp_path):
    con = build(raw_dir, universe_csv, monkeypatch)
    stress_json = {"h1": {"summary": {"events": 1, "avg_diff": -0.01, "ci_low": -0.02, "ci_high": 0.001,
                                      "events_negative": 1},
                          "per_event": [{"event": "X", "formation": "2025-12-31", "diff": -0.01,
                                         "ci_low": -0.02, "ci_high": 0.001}]}}
    (tmp_path / "stress.json").write_text(json.dumps(stress_json))
    path = dashboard.write(con, tmp_path)
    assert path.name == "sentry-ward-2026-06-30.html"
    html = path.read_text()
    assert "/*__DATA__*/" not in html
    data = embedded(html)
    assert data["period"] == "2026-06-30"
    assert [s["ticker"] for s in data["stocks"]][:1] == ["AAA"]
    assert data["active_overlap"]["funds"] == ["Citadel", "Millennium", "Point72"]
    assert data["stress"]["primary"]["h1"]["summary"]["avg_diff"] == -0.01
    assert "public" not in data["stress"]
    assert data["coverage"]["value_priced_share"] > 0


def test_dashboard_without_market_or_stress(raw_dir, universe_csv, monkeypatch, tmp_path):
    con = build(raw_dir, universe_csv, monkeypatch, with_market=False)
    data = embedded(dashboard.write(con, tmp_path).read_text())
    assert data["coverage"] is None and data["stress"] == {}
    assert data["stocks"][0]["own"] is None


def test_script_tags_in_names_cannot_break_out(raw_dir, universe_csv, monkeypatch, tmp_path):
    con = build(raw_dir, universe_csv, monkeypatch, with_market=False)
    data = dashboard.gather(con, tmp_path)
    data["stocks"][0]["issuer"] = "</script><script>alert(1)</script>"
    html = dashboard.render(data)
    assert html.count("</script>") == 1          # only the page's own closing tag
