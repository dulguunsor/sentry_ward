"""Search, stock and fund pages, on the hand-checkable sample filings."""

from __future__ import annotations

import json

import duckdb
import pytest

from sentryward import clean, explore, metrics, sec13f, server

AAA, BBB, CCC, DDD = "11111A", "22222B", "33333C", "44444D"


@pytest.fixture
def db(raw_dir, universe_csv, tmp_path):
    path = tmp_path / "ward.duckdb"
    c = duckdb.connect(str(path))
    sec13f.load_all(c, raw_dir.glob("*.zip"))
    clean.build_all(c)
    metrics.build_all(c, universe_csv)
    c.close()
    return path


@pytest.fixture
def con(db):
    c = duckdb.connect(str(db), read_only=True)
    yield c
    c.close()


def test_search_finds_stocks_and_funds(con):
    r = explore.search(con, "aaa")
    assert [s["issuer_id"] for s in r["stocks"]] == [AAA] and r["funds"] == []
    assert r["stocks"][0]["key"] == AAA                     # no tickers without `ward market`
    assert explore.search(con, "MILL")["funds"] == [{"fund": "Millennium", "style": "multi-manager"}]
    assert explore.search(con, "corp")["stocks"][0]["issuer_id"] == AAA   # most crowded first
    assert explore.search(con, "  ") == {"stocks": [], "funds": []}


def test_stock_page(con):
    s = explore.stock(con, AAA)
    assert s["period"] == "2026-06-30" and s["now"]["score"] == 100 and s["now"]["n_holders"] == 3
    assert [h["period"] for h in s["history"]] == ["2022-09-30", "2026-06-30"]
    by = {h["fund"]: h for h in s["holders"]}
    assert list(by) == ["Citadel", "Millennium", "Point72"]   # largest position first
    # Citadel went from 100 shares to 5; Point72 had no earlier filing.
    assert by["Citadel"]["change"] == "trimmed" and by["Citadel"]["prev_shares"] == 100
    assert by["Point72"]["change"] == "first filing"
    assert s["sold_out"] == []
    # A stock held by one fund has no crowding score but still has a page.
    d = explore.stock(con, DDD)
    assert d["now"] is None and [h["fund"] for h in d["holders"]] == ["Millennium"]
    assert d["holders"][0]["change"] == "new"
    assert explore.stock(con, "NOPE") is None
    assert explore.stock(con, "11111a101")["issuer_id"] == AAA   # full CUSIP works too


def test_fund_page(con):
    f = explore.fund(con, "citadel")                        # names match without case
    assert f["fund"] == "Citadel" and f["period"] == "2026-06-30" and f["prev_period"] == "2022-09-30"
    assert f["filers"] == ["CITADEL ADVISORS LLC"]
    assert [h["issuer_id"] for h in f["holdings"]] == [AAA, BBB]
    assert f["changes"] == {"new": 1, "added": 0, "trimmed": 1, "unchanged": 0, "closed": 0}
    assert sum(h["weight"] for h in f["holdings"]) == pytest.approx(1.0)
    # Closest funds by raw overlap: Millennium 0.80, then Point72 0.25 (see sample.py).
    assert [(o["fund"], o["overlap"]) for o in f["overlap"]] == [("Millennium", 0.8), ("Point72", 0.25)]
    assert [h["period"] for h in f["history"]] == ["2022-09-30", "2026-06-30"]
    json.dumps(f, allow_nan=False)
    assert explore.fund(con, "Ghost Fund") is None          # in the universe file, never matched a filer


def test_api_routes(db):
    from sentryward import live

    class Idle:
        def state(self):
            return {"snapshot": None}

    app = server.create_app(Idle(), {}, connect=lambda: duckdb.connect(str(db), read_only=True))
    c = app.test_client()
    assert c.get("/api/search?q=point").get_json()["funds"][0]["fund"] == "Point72"
    assert c.get(f"/api/stock/{AAA}").get_json()["now"]["n_holders"] == 3
    assert c.get("/api/fund/Millennium").get_json()["changes"]["new"] == 2
    assert c.get("/api/stock/NOPE").status_code == 404
    assert c.get("/api/nothing").status_code == 404
    # Deep links to React pages are served the app, not a 404.
    assert c.get("/stock/AAA").status_code == 200
    # Without a database the pages say so instead of failing.
    bare = server.create_app(Idle(), {}).test_client()
    assert bare.get("/api/search?q=a").status_code == 503
    assert live.clean({"a": __import__("numpy").array(["x"])}) == {"a": ["x"]}
