"""YouTube look-ups for the live TV panel, with a fake API."""

from __future__ import annotations

import pytest

from sentryward import live, server, youtube
from test_live import fake_quotes, market  # noqa: F401

UC = "UC" + "a" * 22


class Resp:
    def __init__(self, js, code=200):
        self.js, self.status_code = js, code

    def json(self):
        return self.js

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeHttp:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url.rsplit("/", 1)[-1], params))
        if url.endswith("/channels"):
            return Resp({"items": [{"id": UC, "snippet": {"title": "Market News TV"}}]})
        return Resp({"items": [
            {"id": {"videoId": "abcDEF12345"}, "snippet": {"title": "Nvidia shares slide", "channelTitle": "Market News TV",
                                                           "publishedAt": "2026-04-10T15:00:00Z", "liveBroadcastContent": "none"}},
            {"id": {"videoId": "zzzDEF12345"}, "snippet": {"title": "Older", "channelTitle": "Market News TV",
                                                           "publishedAt": "2026-04-10T13:00:00Z"}}]})


def test_company_query():
    assert youtube.company_query("NVIDIA CORP", "NVDA") == "Nvidia"
    assert youtube.company_query("ALPHABET INC CL A", "GOOGL") == "Alphabet"
    assert youtube.company_query("", "XYZ") == "XYZ"


def test_resolve_and_related_with_cache_and_budget():
    http = FakeHttp()
    yt = youtube.YouTube("k", http, clock=lambda: 1_775_000_000)
    assert yt.resolve("https://www.youtube.com/@marketnews") == {"channel_id": UC, "name": "Market News TV"}
    assert http.calls[-1][1]["forHandle"] == "@marketnews"
    assert yt.resolve("https://example.com") is None
    v = yt.related("Nvidia", [UC])
    assert [x["video_id"] for x in v] == ["abcDEF12345", "zzzDEF12345"]
    assert http.calls[-1][0] == "search" and http.calls[-1][1]["channelId"] == UC
    n = len(http.calls)
    yt.related("nvidia", [UC])                     # cached: no second search
    assert len(http.calls) == n and yt.used == 1 + 100
    yt.used = youtube.DAILY_BUDGET - 50
    with pytest.raises(RuntimeError):
        yt.related("Apple", [UC])


def test_tv_routes(market):
    _, b, h = market
    f = fake_quotes(-0.03, -0.005)
    f.crowded = live.basket_symbols(b)[0]
    m = live.LiveMonitor(b, h, fetcher=f)
    off = server.create_app(m, live.meta(b, h)).test_client()
    assert off.get("/api/tv/status").get_json()["enabled"] is False
    assert off.get("/api/tv/related?q=x&channels=" + UC).status_code == 503
    on = server.create_app(m, live.meta(b, h), yt=youtube.YouTube("k", FakeHttp())).test_client()
    assert on.get("/api/tv/resolve?url=https://youtube.com/@x").get_json()["name"] == "Market News TV"
    assert on.get(f"/api/tv/related?q=Nvidia&channels={UC},bad").get_json()["videos"][0]["video_id"] == "abcDEF12345"
    assert on.get("/api/tv/related?q=Nvidia&channels=bad").status_code == 400
    assert on.get(f"/api/tv/related?issuer=NVIDIA%20CORP&ticker=NVDA&channels={UC}").status_code == 200
