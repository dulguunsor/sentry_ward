"""Real-time prices and news, with fake Alpaca responses (no network)."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from sentryward import live, realtime, server
from test_live import fake_quotes, market  # noqa: F401  (fixture)

NY = ZoneInfo("America/New_York")
T_1000 = datetime(2026, 4, 10, 10, 0, 5, tzinfo=NY).timestamp()    # a Friday, market open
T_SAT = datetime(2026, 4, 11, 12, 0, tzinfo=NY).timestamp()


def test_parse_snapshots_and_symbols():
    js = {"BRK.B": {"latestTrade": {"t": "2026-04-10T14:00:01.123456789Z", "p": 480.5},
                    "prevDailyBar": {"c": 470.0}},
          "XYZ": {"latestTrade": None}, "bad": "x"}
    out = realtime.parse_snapshots(js)
    assert list(out) == ["BRK-B"] and out["BRK-B"]["price"] == 480.5 and out["BRK-B"]["prev_close"] == 470.0
    assert out["BRK-B"]["ts"] == pytest.approx(datetime(2026, 4, 10, 14, 0, 1, 123456, tzinfo=ZoneInfo("UTC")).timestamp())
    assert realtime.to_alpaca("BRK-B") == "BRK.B" and realtime.from_alpaca("BRK.B") == "BRK-B"
    assert realtime.regular_hours(T_1000) and not realtime.regular_hours(T_SAT)


def _session(market):
    _, b, h = market
    f = fake_quotes(-0.03, -0.005)
    f.crowded = live.basket_symbols(b)[0]
    cs, ps = live.basket_symbols(b)
    return b, h, live.snapshot(f(cs + ps + ["SPY"]), b, h["sigma"]), cs, ps


def test_tick_moves_every_range(market):
    b, h, snap, cs, ps = _session(market)
    # Close was 97 for crowded, 99.5 for peers. Now crowded recover to 99, peers to 100.
    t = T_1000 + 86400 * 0  # same calendar day as the session in the fake quotes
    rt = {s: {"price": 99.0, "ts": t} for s in cs} | {s: {"price": 100.0, "ts": t} for s in ps} \
        | {"SPY": {"price": 100.0, "ts": t}}
    m = realtime.merge_tick(snap, rt, b, h["sigma"], t, live.ALERT_Z)
    assert m["crowded"] == pytest.approx(-0.01) and m["peers"] == pytest.approx(0.0)
    assert m["spread"] == pytest.approx(-0.01) and m["z"] == pytest.approx(-0.01 / h["sigma"])
    day = m["ranges"]["1D"]
    assert day["path"][-1]["live"] and day["path"][-1]["t"] == "10:00"
    assert len(day["path"]) == len(snap["ranges"]["1D"]["path"]) + 1      # one live point added
    assert all(s["rel"] == pytest.approx(-0.01) for s in day["stocks"]) and day["stocks"][0]["age"] == 0
    # 1H is measured from its own start price, not yesterday's close.
    base = snap["ranges"]["1H"]["_base"][cs[0]]
    assert m["ranges"]["1H"]["crowded"] == pytest.approx(99.0 / base - 1)
    # A second tick in the same minute replaces the live point instead of adding one.
    m2 = realtime.merge_tick(snap, rt, b, h["sigma"], t + 20, live.ALERT_Z)
    assert len(m2["ranges"]["1D"]["path"]) == len(day["path"])
    # Prices older than the session are ignored.
    old = {s: {"price": 1.0, "ts": t - 5 * 86400} for s in cs + ps}
    assert realtime.merge_tick(snap, old, b, h["sigma"], t, live.ALERT_Z) is snap


def test_new_day_before_delayed_bars_arrive(market):
    b, h, snap, cs, ps = _session(market)
    t = datetime(2026, 4, 13, 9, 35, tzinfo=NY).timestamp()   # Monday 09:35; delayed bars still end Friday
    rt = {s: {"price": 97.0 * 0.99, "ts": t} for s in cs} | {s: {"price": 99.5, "ts": t} for s in ps}
    m = realtime.merge_tick(snap, rt, b, h["sigma"], t, live.ALERT_Z)
    assert m["session"] == "2026-04-13"
    assert m["crowded"] == pytest.approx(-0.01) and m["peers"] == pytest.approx(0.0)   # vs Friday's close
    assert m["ranges"]["1D"]["path"] == [m["ranges"]["1D"]["path"][0]] and m["ranges"]["1D"]["since"] == "since yesterday's close"


class FakePrices(realtime.PriceFeed):
    def __init__(self, data, t):
        super().__init__(list(data), lambda s: data)
        self.poll_once()
        self.last_ok = t


def test_monitor_serves_realtime_only_while_open(market):
    b, h, _, cs, ps = _session(market)
    f = fake_quotes(-0.03, -0.005)
    f.crowded = cs
    rt = {s: {"price": 99.0, "ts": T_1000} for s in cs} | {s: {"price": 100.0, "ts": T_1000} for s in ps}
    clock = {"t": T_1000}
    m = live.LiveMonitor(b, h, fetcher=f, prices=FakePrices(rt, T_1000), clock=lambda: clock["t"])
    m.refresh_once()
    s = m.state()
    assert s["source"] == "iex" and s["interval"] == 1 and s["snapshot"]["spread"] == pytest.approx(-0.01)
    assert not any(k.startswith("_") for r in s["snapshot"]["ranges"].values() for k in r)   # internals stay inside
    json.dumps(s, allow_nan=False)
    clock["t"] = T_SAT
    s = m.state()
    assert s["source"] == "yahoo" and s["snapshot"]["spread"] == pytest.approx(-0.025)
    assert s["realtime"]["market_open"] is False


def test_realtime_minutes_fill_the_delay(market):
    b, h, _, cs, ps = _session(market)
    f = fake_quotes(-0.03, -0.005)
    f.crowded = cs
    late = datetime(2026, 4, 10, 15, 59, 30, tzinfo=NY).timestamp()   # after the last delayed bar (15:55)
    rt = {s: {"price": 96.0, "ts": late} for s in cs} | {s: {"price": 99.5, "ts": late} for s in ps}
    m = live.LiveMonitor(b, h, fetcher=f, prices=FakePrices(rt, late), clock=lambda: T_SAT)
    m.prices.on_update()
    m.refresh_once()
    path = m.state()["snapshot"]["ranges"]["1D"]["path"]
    assert path[-1]["t"] == "15:55" and path[-1]["crowded"] == pytest.approx(-0.04)   # 15:59 folds into the 15:55 bar


def test_news_feed_handles_stream_and_dedupes():
    feed = realtime.NewsFeed(["AAPL", "BRK-B"])
    msg = [{"T": "success", "msg": "authenticated"},
           {"T": "n", "id": 1, "headline": "Apple &amp; partner <b>sign</b> deal", "summary": "",
            "content": "<p>Long article text</p>", "url": "https://example.com/a", "symbols": ["AAPL", "MSFT"],
            "source": "benzinga", "created_at": "2026-04-10T14:00:00Z"}]
    feed.handle(json.dumps(msg))
    feed.handle(json.dumps(msg[1:]))                      # same id again
    feed.handle(json.dumps([{"T": "n", "id": 2, "headline": "Berkshire update", "summary": "Short note.",
                             "symbols": ["BRK.B"], "source": "x", "created_at": "2026-04-10T14:05:00Z"}]))
    st = feed.state()
    assert st["status"] == "live" and [n["id"] for n in st["items"]] == [2, 1]
    a = st["items"][1]
    assert a["headline"] == "Apple & partner sign deal" and a["tracked"] == ["AAPL"] and a["summary"] is None
    assert "_text" not in a and "content" not in a        # article text never reaches the browser
    assert st["items"][0]["tracked"] == ["BRK-B"] and st["items"][0]["summary_by"] == "source"
    feed.handle(json.dumps([{"T": "error", "code": 406, "msg": "connection limit exceeded"}]))
    assert feed.state()["status"] == "error" and "406" in feed.state()["error"]


def test_news_summaries_are_queued_for_tracked_items_without_one():
    calls = []
    feed = realtime.NewsFeed(["AAPL"], anthropic_key="k", summarizer=lambda item: calls.append(item["id"]) or "Apple signed a deal.")
    feed.add(realtime.norm_news({"id": 1, "headline": "Apple deal", "symbols": ["AAPL"]}))
    feed.add(realtime.norm_news({"id": 2, "headline": "Other", "symbols": ["ZZZ"]}))
    feed.add(realtime.norm_news({"id": 3, "headline": "Apple again", "summary": "Has one.", "symbols": ["AAPL"]}))
    assert [i["id"] for i in feed._queue] == [1]
    item = feed._queue.popleft()
    item["summary"], item["summary_by"] = feed._summarize(item), "ai"
    assert calls == [1] and feed.state()["items"][-1]["summary_by"] == "ai"


def test_summarize_request(monkeypatch):
    sent = {}

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"content": [{"type": "text", "text": "Apple signed a supply deal."}]}

    class S:
        def post(self, url, **kw):
            sent.update(kw, url=url)
            return R()

    item = realtime.norm_news({"id": 1, "headline": "Apple deal", "content": "<p>Text</p>"})
    assert realtime.summarize(item, "key", S()) == "Apple signed a supply deal."
    assert sent["url"] == realtime.ANTHROPIC_URL and sent["headers"]["x-api-key"] == "key"
    assert "Text" in sent["json"]["messages"][0]["content"] and sent["json"]["model"] == realtime.SUMMARY_MODEL


def test_stream_endpoint(market):
    b, h, _, cs, _ = _session(market)
    f = fake_quotes(-0.03, -0.005)
    f.crowded = cs
    m = live.LiveMonitor(b, h, fetcher=f)
    m.refresh_once()
    c = server.create_app(m, live.meta(b, h)).test_client()
    r = c.get("/api/stream?every=0.5", buffered=False)
    assert r.mimetype == "text/event-stream"
    first = next(r.response).decode()
    assert first.startswith("data: ") and json.loads(first[6:])["snapshot"]["status"] == "ok"
    r.close()
