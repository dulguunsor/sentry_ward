"""Market data, tested with fake OpenFIGI, SEC and Yahoo responses (no network).

Scenario for the 2026-06-30 quarter of the sample data:
  AAA  OpenFIGI -> "AAA". Yahoo shows a 2-for-1 split on 2026-07-15, AFTER the quarter,
       so Yahoo's pre-split close of 50 is really 100 and volume 2,000 is really 1,000.
       13F filers reported about $100 a share -> price matches.
       SEC: 11,000 shares outstanding.
       Funds hold 11 shares -> ownership 11 / 11,000 = 0.1%
                               days to exit 11 / (20% x 1,000) = 0.055
  BBB  OpenFIGI -> "BBB/A" (Yahoo symbol BBB-A). Yahoo close 500, but 13F filers
       reported about $100 -> price does not match (ticker reuse) -> not used.
  CCC  OpenFIGI finds nothing.
  DDD  OpenFIGI -> "DDD", Yahoo has no data (delisted).
"""

from __future__ import annotations

from datetime import date, timedelta

import duckdb
import pandas as pd
import pytest

from sentryward import clean, market, metrics, sec13f
from sentryward.sample import CUSIP


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeFigi:
    answers = {
        CUSIP["AAA"]: {"data": [{"ticker": "AAA", "figi": "BBG0000AAA", "securityType": "Common Stock", "name": "AAA CORP"}]},
        CUSIP["BBB"]: {"data": [{"ticker": "BBB/A", "figi": "BBG0000BBB", "securityType": "Common Stock", "name": "BBB CORP"}]},
        CUSIP["CCC"]: {"warning": "No identifier found."},
        CUSIP["DDD"]: {"data": [{"ticker": "DDD", "figi": "BBG0000DDD", "securityType": "Common Stock", "name": "DDD CORP"}]},
    }

    def __init__(self):
        self.calls = []

    def post(self, url, json, headers, timeout):
        self.calls.append([j["idValue"] for j in json])
        if len(self.calls) == 1:
            return FakeResponse(None, status=429)  # first try is rate limited
        return FakeResponse([self.answers.get(j["idValue"], {"warning": "x"}) for j in json])


class FakeSec:
    def get(self, url, headers, timeout):
        assert "@" in headers["User-Agent"]
        if url == market.SEC_TICKERS_URL:
            return FakeResponse({"0": {"cik_str": 100, "ticker": "AAA", "title": "AAA Corp"},
                                 "1": {"cik_str": 200, "ticker": "BBB-A", "title": "BBB Corp"}})
        if "CY2026Q2I" in url:
            return FakeResponse({"data": [{"cik": 100, "val": 11000}, {"cik": 200, "val": 5000}]})
        return FakeResponse({}, status=404)


def business_days(end: date, n: int) -> list[date]:
    days, d = [], end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return sorted(days)


def fake_yahoo(symbols, start):
    rows = []
    for d in business_days(date(2026, 6, 30), 70):
        rows.append(("AAA", d, 50.0, 50.0, 2000.0, 0.0))
        rows.append(("BBB-A", d, 500.0, 500.0, 100.0, 0.0))
    rows.append(("AAA", date(2026, 7, 15), 50.0, 50.0, 2000.0, 2.0))  # split after the quarter
    df = pd.DataFrame(rows, columns=["symbol", "date", "close", "adj_close", "volume", "split"])
    return df[df.symbol.isin(symbols)]


@pytest.fixture
def con(raw_dir, universe_csv, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Test Person test@example.com")
    monkeypatch.delenv(market.OPENFIGI_KEY_ENV, raising=False)
    c = duckdb.connect()
    sec13f.load_all(c, raw_dir.glob("*.zip"))
    clean.build_all(c)
    metrics.build_all(c, universe_csv)
    return c


def run_market(con):
    figi = FakeFigi()
    out = {
        "figi": market.map_cusips(con, session=figi, log=lambda m: None, sleep=lambda s: None),
        "prices": market.fetch_prices(con, downloader=fake_yahoo, log=lambda m: None),
        "shares": market.fetch_shares(con, session=FakeSec(), log=lambda m: None, sleep=lambda s: None),
        "market": market.build_market(con),
    }
    return figi, out


def test_yahoo_symbol():
    assert market.yahoo_symbol("BRK/B") == "BRK-B"
    assert market.yahoo_symbol(" brk b ") == "BRK-B"


def test_mapping_retries_and_caches(con):
    figi, out = run_market(con)
    assert len(figi.calls) == 2                       # one 429, then success
    assert out["figi"] == {"requested": 4, "found": 3}
    rows = dict(con.execute("SELECT cusip, symbol FROM cusip_map").fetchall())
    assert rows[CUSIP["BBB"]] == "BBB-A" and rows[CUSIP["CCC"]] is None
    # Second run has nothing left to map or download.
    assert market.map_cusips(con, session=FakeFigi(), log=lambda m: None) == {"requested": 0, "found": 0}
    assert market.fetch_prices(con, downloader=fake_yahoo, log=lambda m: None)["requested"] == 0


def test_split_is_undone_and_windows_are_right(con):
    run_market(con)
    close, close_date, adv, vol = con.execute(
        "SELECT close, close_date, adv_shares, daily_vol FROM market "
        "WHERE cusip = ? AND period = DATE '2026-06-30'", [CUSIP["AAA"]]
    ).fetchall()[0]
    assert close == pytest.approx(100.0)     # 50 x 2 (split after the quarter)
    assert adv == pytest.approx(1000.0)      # 2,000 / 2
    assert close_date == date(2026, 6, 30)
    assert vol == pytest.approx(0.0)


def test_price_check_rejects_reused_tickers(con):
    run_market(con)
    got = dict(con.execute(
        "SELECT cusip, price_match FROM market WHERE period = DATE '2026-06-30'"
    ).fetchall())
    assert got[CUSIP["AAA"]] is True
    assert got[CUSIP["BBB"]] is False        # Yahoo 500 vs 13F ~100
    assert CUSIP["CCC"] not in got           # no ticker
    assert got[CUSIP["DDD"]] is False        # no prices


def test_ownership_and_days_to_exit_flow_into_scores(con):
    run_market(con)
    metrics.build_stock_crowding(con)
    aaa = con.execute(
        "SELECT ticker, hf_ownership, days_to_exit FROM stock_crowding "
        "WHERE period = DATE '2026-06-30' AND cusip = ?", [CUSIP["AAA"]]
    ).fetchall()[0]
    assert aaa[0] == "AAA"
    assert aaa[1] == pytest.approx(11 / 11000)
    assert aaa[2] == pytest.approx(11 / (0.2 * 1000))
    bbb = con.execute(
        "SELECT hf_ownership FROM stock_crowding WHERE period = DATE '2026-06-30' AND cusip = ?",
        [CUSIP["BBB"]],
    ).fetchall()[0][0]
    assert bbb is None


def test_coverage(con):
    run_market(con)
    cov = market.coverage(con)
    # Q2 2026 stock value: AAA 1,150, BBB 900, CCC 750, DDD 200. Only AAA usable.
    assert cov["value_priced_share"] == pytest.approx(1150 / 3000)
    assert cov["value_with_shares_share"] == pytest.approx(1150 / 3000)


def test_yahoo_download_parses_yfinance_output(monkeypatch):
    import sys
    import types

    idx = pd.to_datetime(["2026-06-29", "2026-06-30"])
    cols = pd.MultiIndex.from_product(
        [["AAA", "ZZZ"], ["Open", "High", "Low", "Close", "Adj Close", "Volume", "Dividends", "Stock Splits"]]
    )
    data = pd.DataFrame(1.0, index=idx, columns=cols)
    data[("ZZZ", "Close")] = float("nan")       # a delisted symbol comes back empty
    fake = types.SimpleNamespace(download=lambda *a, **k: data)
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    out = market.yahoo_download(["AAA", "ZZZ", "MISSING"], "2026-01-01")
    assert list(out.symbol.unique()) == ["AAA"]
    assert list(out.columns) == ["symbol", "date", "close", "adj_close", "volume", "split"]
    assert len(out) == 2


def test_market_data_never_changes_crowding_score(con):
    before = dict(con.execute(
        "SELECT cusip, crowding_score FROM stock_crowding WHERE period = DATE '2026-06-30'"
    ).fetchall())
    run_market(con)
    metrics.build_stock_crowding(con)
    after = dict(con.execute(
        "SELECT cusip, crowding_score FROM stock_crowding WHERE period = DATE '2026-06-30'"
    ).fetchall())
    assert after == pytest.approx(before)
    risk = dict(con.execute(
        "SELECT cusip, exit_risk FROM stock_crowding WHERE period = DATE '2026-06-30'"
    ).fetchall())
    assert risk[CUSIP["AAA"]] is not None and risk[CUSIP["BBB"]] is None
