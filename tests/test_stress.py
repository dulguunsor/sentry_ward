"""Stress test checked on synthetic markets with a known answer.

300 stocks with betas between 0.5 and 1.5 follow SPY plus noise. During the event
window (2026-02-27 to 2026-03-31) SPY falls about 0.8% a day.
"""

from __future__ import annotations

from datetime import date

import duckdb
import numpy as np
import pandas as pd
import pytest

from sentryward import stress

EVENT = stress.Event("Test sell-off", date(2026, 2, 27), date(2026, 3, 31))
PERIODS = [date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31)]


def make_market(effect: float = 0.0, crowding_follows_beta: bool = False, n: int = 300, seed: int = 1):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2024-09-02", "2026-04-10").date
    in_event = np.array([EVENT.start < d <= EVENT.end for d in days])
    m = rng.normal(0.0004, 0.01, len(days))
    m[in_event] = -0.008

    beta = rng.uniform(0.5, 1.5, n)
    if crowding_follows_beta:
        score = 100 * (beta - 0.5) + rng.normal(0, 2, n)     # crowded = high beta
    else:
        score = rng.uniform(0, 100, n)
    crowded = score >= np.quantile(score, 0.9)
    dte = rng.uniform(0, 30, n)

    con = duckdb.connect()
    price_rows = []
    for i in range(n):
        r = beta[i] * m + rng.normal(0, 0.004, len(days))
        if crowded[i]:
            r[in_event] += np.log1p(effect) / in_event.sum()
        adj = 100 * np.exp(np.cumsum(r))
        price_rows.append(pd.DataFrame({"symbol": f"S{i:03d}", "date": days, "close": adj,
                                        "adj_close": adj, "volume": 1e6, "split": 0.0}))
    spy = 100 * np.exp(np.cumsum(m))
    price_rows.append(pd.DataFrame({"symbol": "SPY", "date": days, "close": spy, "adj_close": spy,
                                    "volume": 1e6, "split": 0.0}))
    con.register("p", pd.concat(price_rows))
    con.execute("CREATE TABLE prices AS SELECT symbol, CAST(date AS DATE) AS date, close, adj_close, volume, split FROM p")

    rows = []
    for p in PERIODS:
        for i in range(n):
            rows.append((p, f"I{i:03d}", f"C{i:08d}", float(score[i]), float(dte[i]), f"S{i:03d}",
                         float(np.exp(rng.normal(0, 1)))))
    df = pd.DataFrame(rows, columns=["period", "issuer_id", "cusip", "crowding_score", "days_to_exit",
                                     "symbol", "market_weight"])
    con.register("d", df)
    con.execute("CREATE TABLE stock_crowding AS SELECT period, issuer_id, cusip, crowding_score, days_to_exit FROM d")
    con.execute("CREATE TABLE market AS SELECT cusip, period, symbol, true AS price_match FROM d")
    con.execute("CREATE TABLE market_weights AS SELECT period, issuer_id, market_weight FROM d")
    return con, beta, crowded


def test_recovers_a_planted_effect():
    con, _, _ = make_market(effect=-0.05)
    res = stress.run(con, events=[EVENT], reps=300)
    ev = res["H1 crowded"]["per_event"].iloc[0]
    assert ev["n_treated"] == 30
    assert ev["diff"] == pytest.approx(-0.05, abs=0.012)
    assert ev["ci_high"] < 0                        # interval excludes zero
    assert ev["regression_coef"] == pytest.approx(-0.05, abs=0.012)
    s = res["H1 crowded"]["summary"]
    assert s["events_negative"] == 1 and s["share_of_boot_below_zero"] > 0.99


def test_no_effect_gives_an_interval_around_zero():
    con, _, _ = make_market(effect=0.0)
    ev = stress.run(con, events=[EVENT], reps=300)["H1 crowded"]["per_event"].iloc[0]
    assert ev["ci_low"] < 0 < ev["ci_high"]
    assert abs(ev["diff"]) < 0.012


def test_matching_removes_beta_confounding():
    # Crowded stocks are the high-beta ones and there is NO crowding effect. A plain
    # comparison blames crowding for the extra fall; matching on beta should not.
    con, _, _ = make_market(effect=0.0, crowding_follows_beta=True)
    ev = stress.run(con, events=[EVENT], reps=200)["H1 crowded"]["per_event"].iloc[0]
    assert ev["raw_diff"] < -0.06                   # naive answer: "crowded fell 6%+ more"
    assert abs(ev["diff"]) < abs(ev["raw_diff"]) / 2
    assert abs(ev["regression_coef"]) < 0.015      # controlling for beta: about zero


def test_fragile_group_and_reproducibility():
    con, _, _ = make_market(effect=-0.05)
    a = stress.run(con, events=[EVENT], reps=200)
    b = stress.run(con, events=[EVENT], reps=200)
    h2 = a["H2 crowded and hard to exit"]["per_event"].iloc[0]
    # top 25% by score AND top 25% by days to exit: about 300 x .25 x .25 stocks
    assert 8 <= h2["n_treated"] <= 32
    assert a["H1 crowded"]["summary"] == b["H1 crowded"]["summary"]   # fixed seed


def test_formation_dates():
    con, _, _ = make_market()
    assert stress.formation_period(con, date(2026, 1, 10)) == date(2025, 12, 31)
    # 45 days before Jan 10 is Nov 26: the Dec 31 holdings were not public yet.
    assert stress.formation_period(con, date(2026, 1, 10), public=True) == date(2025, 9, 30)
    assert stress.formation_period(con, date(2025, 1, 1)) is None


def test_ordinary_quarters():
    con, _, _ = make_market(effect=-0.05)
    oq = stress.ordinary_quarters(con)
    assert list(oq["from"]) == [date(2025, 9, 30), date(2025, 12, 31)]
    # The quarter containing the sell-off shows the planted effect; the calm one does not.
    assert oq.iloc[1]["diff"] == pytest.approx(-0.05, abs=0.015)
    assert abs(oq.iloc[0]["diff"]) < 0.015


def test_ensure_benchmark_downloads_once():
    con = duckdb.connect()
    calls = []

    def dl(symbols, start):
        calls.append(symbols)
        return pd.DataFrame({"symbol": ["SPY"], "date": [date(2026, 1, 2)], "close": [1.0],
                             "adj_close": [1.0], "volume": [1.0], "split": [0.0]})

    assert stress.ensure_benchmark(con, downloader=dl, log=lambda m: None)
    assert stress.ensure_benchmark(con, downloader=dl, log=lambda m: None)
    assert calls == [["SPY"]]


def test_cli_stress(tmp_path, monkeypatch, capsys):
    import json

    from sentryward import cli

    con, _, _ = make_market(effect=-0.05)
    con.execute(f"ATTACH '{tmp_path / 'ward.duckdb'}' AS f").fetchall()
    for name in ("prices", "stock_crowding", "market", "market_weights"):
        con.execute(f"CREATE TABLE f.{name} AS SELECT * FROM {name}").fetchall()
    con.execute("DETACH f").fetchall()
    con.close()
    monkeypatch.setenv("WARD_DATA", str(tmp_path))
    monkeypatch.setattr(stress, "EVENTS", [EVENT])
    cli.main(["stress", "--reps", "100"])
    out = capsys.readouterr().out
    assert "Test sell-off" in out and "Average over 1 events" in out
    data = json.loads((tmp_path / "out" / "stress.json").read_text())
    assert data["h1"]["summary"]["avg_diff"] == pytest.approx(-0.05, abs=0.012)
