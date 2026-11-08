"""Live mode, tested with a synthetic market and fake intraday quotes."""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from sentryward import live, server, stress
from test_stress import make_market

NY = ZoneInfo("America/New_York")


def fake_quotes(crowded_move: float, peer_move: float, spy_move: float = -0.01, drop: set | None = None):
    """Fetcher: yesterday ends at 100 for everyone; today every symbol drifts linearly
    to its target move by the close."""
    def fetch(symbols):
        rows = []
        prev = datetime(2026, 4, 9, 15, 55, tzinfo=NY)
        bars = [datetime.combine(datetime(2026, 4, 10).date(), time(9, 30), NY) + timedelta(minutes=5 * k)
                for k in range(78)]
        crowded = set(fetch.crowded)
        for s in symbols:
            if drop and s in drop:
                continue
            move = spy_move if s == "SPY" else crowded_move if s in crowded else peer_move
            rows.append((s, prev, 100.0))
            for k, t in enumerate(bars):
                rows.append((s, t, 100.0 * (1 + move * (k + 1) / len(bars))))
        return pd.DataFrame(rows, columns=["symbol", "ts", "close"])
    fetch.crowded = []
    return fetch


@pytest.fixture(scope="module")
def market():
    con, beta, crowded = make_market(effect=0.0)
    baskets = live.build_baskets(con)
    hist = live.history(con, baskets)
    return con, baskets, hist


def test_baskets_are_matched(market):
    con, b, _ = market
    assert len(b["crowded"]) == 30
    scores = [c["score"] for c in b["crowded"]]
    assert scores == sorted(scores, reverse=True)
    peers = [p for c in b["crowded"] for p in c["peers"]]
    assert all(len(c["peers"]) == 2 for c in b["crowded"])
    assert len(peers) == len(set(peers)) == 60                 # every peer used once
    assert not set(peers) & {c["symbol"] for c in b["crowded"]}
    # Peers come from the same size x beta bucket, so average betas are close.
    assert abs(b["avg_beta_crowded"] - b["avg_beta_peers"]) < 0.1


def test_history_measures_normal_spread(market):
    _, _, h = market
    assert h["days"] >= 200 and 0 < h["sigma"] < 0.01
    assert len(h["series"]) == live.HIST_SHOWN


def test_crowded_selloff_triggers_alert(market):
    _, b, h = market
    f = fake_quotes(crowded_move=-0.03, peer_move=-0.005)
    f.crowded = [c["symbol"] for c in b["crowded"]]
    snap = live.snapshot(f(live.basket_symbols(b)[0] + live.basket_symbols(b)[1] + ["SPY"]), b, h["sigma"])
    assert snap["status"] == "ok" and snap["session"] == "2026-04-10"
    assert snap["crowded"] == pytest.approx(-0.03)
    assert snap["peers"] == pytest.approx(-0.005)
    assert snap["spread"] == pytest.approx(-0.025)
    assert snap["spy"] == pytest.approx(-0.01)
    assert snap["z"] == pytest.approx(-0.025 / h["sigma"]) and snap["alert"] is True
    assert len(snap["path"]) == 78 and snap["path"][0]["t"] == "09:30"
    assert snap["coverage"] == {"crowded": 30, "crowded_total": 30, "peers": 60, "peers_total": 60}
    assert all(s["rel"] == pytest.approx(-0.025) for s in snap["stocks"])


def test_calm_day_no_alert(market):
    _, b, h = market
    f = fake_quotes(crowded_move=-0.006, peer_move=-0.005)
    f.crowded = [c["symbol"] for c in b["crowded"]]
    cs, ps = live.basket_symbols(b)
    snap = live.snapshot(f(cs + ps + ["SPY"]), b, h["sigma"])
    assert snap["alert"] is False and abs(snap["spread"]) < 0.002


def test_missing_quotes_reduce_coverage(market):
    _, b, h = market
    cs, ps = live.basket_symbols(b)
    f = fake_quotes(-0.02, -0.01, drop={cs[0], ps[0], ps[1]})
    f.crowded = cs
    snap = live.snapshot(f(cs + ps + ["SPY"]), b, h["sigma"])
    assert snap["coverage"]["crowded"] == 29 and snap["coverage"]["peers"] == 58
    missing = next(s for s in snap["stocks"] if s["ticker"] == b["crowded"][0]["ticker"])
    assert missing["ret"] is None and missing["rel"] is None


def test_empty_quotes():
    assert live.snapshot(pd.DataFrame(columns=["symbol", "ts", "close"]), {"crowded": [], "peers": []}, 0.01) == {"status": "no_data"}


def test_monitor_keeps_last_good_snapshot(market):
    _, b, h = market
    good = fake_quotes(-0.03, -0.005)
    good.crowded = live.basket_symbols(b)[0]
    calls = {"n": 0}

    def flaky(symbols):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ConnectionError("rate limited")
        return good(symbols)

    m = live.LiveMonitor(b, h, fetcher=flaky, interval=60)
    m.refresh_once()
    first = m.state()
    assert first["snapshot"]["alert"] is True and first["error"] is None
    m.refresh_once()
    second = m.state()
    assert "rate limited" in second["error"]
    assert second["snapshot"] == first["snapshot"]          # still serving the last good one
    json.dumps(second, allow_nan=False)                     # strict JSON


def test_flask_api(market, monkeypatch, tmp_path):
    _, b, h = market
    f = fake_quotes(-0.03, -0.005)
    f.crowded = live.basket_symbols(b)[0]
    m = live.LiveMonitor(b, h, fetcher=f)
    m.refresh_once()
    app = server.create_app(m, live.meta(b, h), "<html>dash</html>")
    c = app.test_client()
    meta = c.get("/api/meta").get_json()
    assert meta["period"] == "2026-03-31" and len(meta["crowded"]) == 30 and meta["alert_z"] == live.ALERT_Z
    r = c.get("/api/live")
    assert r.headers["Cache-Control"] == "no-store"
    assert r.get_json()["snapshot"]["alert"] is True
    assert c.get("/dashboard").get_data(as_text=True) == "<html>dash</html>"
    # Without a built front end the root page explains how to build it.
    monkeypatch.setattr(server, "static_dir", lambda: tmp_path)
    app2 = server.create_app(m, live.meta(b, h))
    assert "npm run build" in app2.test_client().get("/").get_data(as_text=True)
    assert app2.test_client().get("/dashboard").status_code == 404


def _selloff(b, h, con=None):
    f = fake_quotes(-0.03, -0.005)
    f.crowded = live.basket_symbols(b)[0]
    cs, ps = live.basket_symbols(b)
    syms = cs + ps + ["SPY"]
    daily = live.db_daily(con, syms) if con is not None else None
    return live.snapshot(f(syms), b, h["sigma"], daily)


def test_intraday_ranges(market):
    _, b, h = market
    r = _selloff(b, h)["ranges"]
    assert list(r) == ["1H", "3H", "1D", "5D"]               # no daily data passed
    # 1D matches the headline numbers and keeps 5-minute bars.
    assert r["1D"]["spread"] == pytest.approx(-0.025) and len(r["1D"]["path"]) == 78
    # The last hour starts from the price an hour before the last bar (bar 66 of 78),
    # so the move is the last 12/78 of the day's drift, measured from that price.
    c0, c1 = 1 - 0.03 * 66 / 78, 1 - 0.03
    assert r["1H"]["crowded"] == pytest.approx(c1 / c0 - 1)
    assert len(r["1H"]["path"]) == 12 and r["1H"]["since"] == "since 14:55"
    # Three hours, 36 bars. 5D has only two sessions here, so it starts from the first bar.
    assert len(r["3H"]["path"]) == 36
    assert r["5D"]["path"][0]["t"].startswith("Thu")
    assert r["5D"]["crowded"] == pytest.approx(-0.03)
    assert all(s["rel"] == pytest.approx(r["1H"]["spread"]) for s in r["1H"]["stocks"])


def test_daily_ranges_use_live_price_for_today(market):
    con, b, h = market
    snap = _selloff(b, h, con)
    r = snap["ranges"]
    assert list(r) == ["1H", "3H", "1D", "5D", "1M", "3M", "1Y"]
    assert len(r["1M"]["path"]) == 21 and len(r["3M"]["path"]) == 63 and len(r["1Y"]["path"]) == 252
    # The last point of every daily range is today's live price, labelled with today's date.
    assert r["1M"]["path"][-1]["t"] == "Apr 10"
    assert r["1M"]["since"].startswith("since 2026-03")
    json.dumps(snap, allow_nan=False)


def test_monitor_fetches_daily_closes(market):
    con, b, h = market
    f = fake_quotes(-0.03, -0.005)
    f.crowded = live.basket_symbols(b)[0]
    calls = []

    def daily_fetch(symbols):
        calls.append(len(symbols))
        return live.db_daily(con, symbols)

    m = live.LiveMonitor(b, h, fetcher=f, daily_fetcher=daily_fetch, daily_every=3600)
    m.refresh_once()
    m.refresh_once()
    assert calls == [91]                                   # once per daily_every, not every refresh
    assert "1Y" in m.state()["snapshot"]["ranges"]
