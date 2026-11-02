"""Stress test: do crowded stocks fall more in sell-offs? Design: PREREGISTRATION.md.

Everything here follows that document. Constants are named after its sections so a
change to the design shows up as a change to this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

import duckdb
import numpy as np
import pandas as pd

BENCHMARK = "SPY"


@dataclass(frozen=True)
class Event:
    name: str
    start: date
    end: date


# ------------------------------------------------------------------ the design

EVENTS = [
    Event("COVID crash", date(2020, 2, 19), date(2020, 3, 23)),
    Event("Degrossing, GameStop week", date(2021, 1, 22), date(2021, 1, 29)),
    Event("2022 rate shock", date(2022, 1, 3), date(2022, 6, 16)),
    Event("Yen carry unwind", date(2024, 7, 16), date(2024, 8, 5)),
    Event("Tariff shock", date(2025, 4, 2), date(2025, 4, 8)),
    Event("Multi-manager drawdown", date(2026, 2, 27), date(2026, 3, 31)),
]
PUBLIC_LAG_DAYS = 45          # 13F filings are due 45 days after quarter end
CROWDED_TOP = 0.10            # H1: top 10% by crowding score
FRAGILE_TOP = 0.25            # H2: top 25% by crowding score AND by days to exit
SIZE_BUCKETS, BETA_BUCKETS = 5, 3
BETA_DAYS, BETA_MIN_DAYS = 252, 120
BOOT_REPS, BOOT_SEED = 2000, 0


# ------------------------------------------------------------------ inputs


def ensure_benchmark(con: duckdb.DuckDBPyConnection, downloader=None, start: str = "2018-09-01",
                     log: Callable[[str], None] = print) -> bool:
    """Make sure SPY prices are present (ETFs are not fetched by `ward market`)."""
    from . import market

    market._ensure_tables(con)
    have = con.execute("SELECT count(*) FROM prices WHERE symbol = ?", [BENCHMARK]).fetchall()[0][0]
    if have:
        return True
    log(f"Downloading {BENCHMARK} prices for beta")
    df = (downloader or market.yahoo_download)([BENCHMARK], start)
    if not len(df):
        return False
    con.register("bench", df.assign(split=df["split"].fillna(0.0), volume=df["volume"].fillna(0.0)))
    con.execute(
        "INSERT OR REPLACE INTO prices SELECT symbol, CAST(date AS DATE), close, adj_close, volume, split FROM bench"
    ).fetchall()
    con.unregister("bench")
    return True


def formation_period(con: duckdb.DuckDBPyConnection, start: date, public: bool = False) -> date | None:
    cutoff = start - timedelta(days=PUBLIC_LAG_DAYS) if public else start
    row = con.execute(
        "SELECT max(period) FROM stock_crowding WHERE period <= ?", [cutoff]
    ).fetchall()[0][0]
    return row


def _sample(con: duckdb.DuckDBPyConnection, formation: date, start: date, end: date) -> pd.DataFrame:
    """One row per stock: crowding, days to exit, size, beta, momentum and the window return."""
    return con.execute(
        f"""
        WITH s AS (
            SELECT sc.issuer_id, sc.cusip, sc.crowding_score, sc.days_to_exit, m.symbol,
                   mw.market_weight
            FROM stock_crowding sc
            JOIN market m ON m.cusip = sc.cusip AND m.period = sc.period AND m.price_match
            LEFT JOIN market_weights mw ON mw.period = sc.period AND mw.issuer_id = sc.issuer_id
            WHERE sc.period = $formation
        ),
        px AS (SELECT symbol, date, adj_close FROM prices WHERE symbol IN (SELECT symbol FROM s) OR symbol = '{BENCHMARK}'),
        at_date AS (
            SELECT symbol,
                   arg_max(adj_close, date) FILTER (WHERE date <= $start AND date > $start - INTERVAL 7 DAY) AS p_start,
                   arg_max(adj_close, date) FILTER (WHERE date <= $end AND date > $end - INTERVAL 7 DAY)     AS p_end,
                   arg_max(adj_close, date) FILTER (WHERE date <= $formation AND date > $formation - INTERVAL 7 DAY) AS p_form,
                   arg_max(adj_close, date) FILTER (WHERE date <= $formation - INTERVAL 365 DAY
                                                    AND date > $formation - INTERVAL 372 DAY)          AS p_year_ago
            FROM px GROUP BY symbol
        ),
        r AS (
            SELECT symbol, date, ln(adj_close / lag(adj_close) OVER (PARTITION BY symbol ORDER BY date)) AS ret
            FROM px WHERE date <= $formation AND date > $formation - INTERVAL 400 DAY
        ),
        rr AS (
            SELECT r.symbol, r.ret, b.ret AS mret,
                   row_number() OVER (PARTITION BY r.symbol ORDER BY r.date DESC) AS k
            FROM r JOIN r b ON b.date = r.date AND b.symbol = '{BENCHMARK}'
            WHERE r.ret IS NOT NULL AND b.ret IS NOT NULL AND r.symbol <> '{BENCHMARK}'
        ),
        beta AS (
            SELECT symbol, regr_slope(ret, mret) AS beta, count(*) AS n_days
            FROM rr WHERE k <= {BETA_DAYS} GROUP BY symbol
        )
        SELECT s.issuer_id, s.symbol, s.crowding_score, s.days_to_exit, s.market_weight,
               b.beta, a.p_end / a.p_start - 1 AS ret, a.p_form / a.p_year_ago - 1 AS mom12
        FROM s
        JOIN at_date a USING (symbol)
        JOIN beta b USING (symbol)
        WHERE a.p_start > 0 AND a.p_end > 0 AND b.n_days >= {BETA_MIN_DAYS}
          AND s.market_weight > 0 AND b.beta IS NOT NULL
        ORDER BY s.symbol   -- fixed order, so the seeded bootstrap repeats exactly
        """,
        {"formation": formation, "start": start, "end": end},
    ).df()


def label_groups(df: pd.DataFrame) -> pd.DataFrame:
    """Add crowded (H1), fragile (H2) and the size x beta bucket."""
    df = df.copy()
    df["crowded"] = df["crowding_score"] >= df["crowding_score"].quantile(1 - CROWDED_TOP)
    has_dte = df["days_to_exit"].notna()
    sub = df[has_dte]
    df["fragile_sample"] = has_dte
    df["fragile"] = False
    if len(sub):
        df.loc[has_dte, "fragile"] = (
            (sub["crowding_score"] >= sub["crowding_score"].quantile(1 - FRAGILE_TOP))
            & (sub["days_to_exit"] >= sub["days_to_exit"].quantile(1 - FRAGILE_TOP))
        )
    df["size_q"] = pd.qcut(df["market_weight"].rank(method="first"), SIZE_BUCKETS, labels=False)
    df["beta_q"] = pd.qcut(df["beta"].rank(method="first"), BETA_BUCKETS, labels=False)
    df["bucket"] = df["size_q"] * BETA_BUCKETS + df["beta_q"]
    return df


# ------------------------------------------------------------------ estimates


def matched_diff(df: pd.DataFrame, treat: str) -> dict:
    """Average crowded-minus-comparison return within size x beta buckets, weighted by
    the number of treated stocks in each bucket."""
    num = den = 0.0
    used = []
    for _, g in df.groupby("bucket"):
        t, c = g[g[treat]], g[~g[treat]]
        if len(t) and len(c):
            num += len(t) * (t["ret"].mean() - c["ret"].mean())
            den += len(t)
            used.append(g)
    diff = num / den if den else float("nan")
    matched = pd.concat(used) if used else df.iloc[0:0]
    # Treated return over matched buckets equals the n-weighted average of bucket means,
    # so the matched comparison return is that minus the difference.
    treated_ret = matched.loc[matched[treat], "ret"].mean() if den else float("nan")
    return {
        "diff": diff,
        "n_treated": int(df[treat].sum()),
        "n_treated_matched": int(den),
        "n_control": int((~df[treat]).sum()),
        "treated_ret": treated_ret,
        "control_ret_matched": treated_ret - diff if den else float("nan"),
        "raw_diff": df.loc[df[treat], "ret"].mean() - df.loc[~df[treat], "ret"].mean(),
    }


def regression_coef(df: pd.DataFrame, treat: str) -> float:
    d = df.dropna(subset=["ret", "beta", "market_weight", "mom12"])
    if d[treat].nunique() < 2 or len(d) < 10:
        return float("nan")
    X = np.column_stack([
        np.ones(len(d)), d[treat].astype(float), d["beta"], np.log(d["market_weight"]), d["mom12"],
    ])
    coef, *_ = np.linalg.lstsq(X, d["ret"].to_numpy(), rcond=None)
    return float(coef[1])


def bootstrap(samples: list[pd.DataFrame], treat: str, reps: int = BOOT_REPS, seed: int = BOOT_SEED) -> np.ndarray:
    """reps x len(samples) matrix of matched differences, resampling stocks within each
    (bucket, group) cell of each sample."""
    rng = np.random.default_rng(seed)
    prepared = []
    for df in samples:
        cells = []
        for _, g in df.groupby("bucket"):
            t, c = g.loc[g[treat], "ret"].to_numpy(), g.loc[~g[treat], "ret"].to_numpy()
            if len(t) and len(c):
                cells.append((t, c))
        prepared.append(cells)
    out = np.full((reps, len(samples)), np.nan)
    for i, cells in enumerate(prepared):
        if not cells:
            continue
        w = np.array([len(t) for t, _ in cells], dtype=float)
        for r in range(reps):
            d = [rng.choice(t, len(t)).mean() - rng.choice(c, len(c)).mean() for t, c in cells]
            out[r, i] = np.dot(w, d) / w.sum()
    return out


def _ci(values: np.ndarray) -> tuple[float, float]:
    v = values[~np.isnan(values)]
    return (float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))) if len(v) else (np.nan, np.nan)


# ------------------------------------------------------------------ run


def run(
    con: duckdb.DuckDBPyConnection,
    events: list[Event] | None = None,
    public: bool = False,
    reps: int = BOOT_REPS,
    seed: int = BOOT_SEED,
) -> dict:
    """Run H1 and H2 over the events and the ordinary-quarter comparison."""
    events = EVENTS if events is None else events
    rows, samples = [], []
    for ev in events:
        form = formation_period(con, ev.start, public=public)
        if form is None:
            rows.append({"event": ev.name, "status": "no holdings before this event"})
            samples.append(None)
            continue
        df = _sample(con, form, ev.start, ev.end)
        if len(df) < 20:
            rows.append({"event": ev.name, "formation": form, "status": f"too few stocks ({len(df)})"})
            samples.append(None)
            continue
        df = label_groups(df)
        samples.append(df)
        rows.append({"event": ev.name, "formation": form, "start": ev.start, "end": ev.end,
                     "n_stocks": len(df), "status": "ok"})

    results = {}
    for test, treat, subset in (("H1 crowded", "crowded", None), ("H2 crowded and hard to exit", "fragile", "fragile_sample")):
        use = [None if s is None else (s[s[subset]] if subset else s) for s in samples]
        ok = [(i, s) for i, s in enumerate(use) if s is not None and s[treat].any() and (~s[treat]).any()]
        boot = bootstrap([s for _, s in ok], treat, reps=reps, seed=seed) if ok else np.empty((0, 0))
        per_event = []
        for j, (i, s) in enumerate(ok):
            est = matched_diff(s, treat)
            lo, hi = _ci(boot[:, j])
            per_event.append({**rows[i], **est, "ci_low": lo, "ci_high": hi,
                              "regression_coef": regression_coef(s, treat)})
        if ok:
            avg_boot = np.nanmean(boot, axis=1)
            lo, hi = _ci(avg_boot)
            summary = {
                "events": len(ok),
                "avg_diff": float(np.mean([p["diff"] for p in per_event])),
                "ci_low": lo, "ci_high": hi,
                "share_of_boot_below_zero": float(np.mean(avg_boot < 0)),
                "events_negative": int(sum(p["diff"] < 0 for p in per_event)),
            }
        else:
            summary = {"events": 0}
        results[test] = {"per_event": pd.DataFrame(per_event), "summary": summary}

    results["ordinary_quarters"] = ordinary_quarters(con)
    results["events"] = pd.DataFrame(rows)
    return results


def ordinary_quarters(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """H1 matched difference from each quarter end to the next: what a normal quarter looks like."""
    periods = [r[0] for r in con.execute(
        "SELECT DISTINCT period FROM stock_crowding ORDER BY period"
    ).fetchall()]
    last_price = con.execute("SELECT max(date) FROM prices").fetchall()[0][0]
    out = []
    for p, nxt in zip(periods, periods[1:]):
        if last_price is None or nxt > last_price:
            break
        df = _sample(con, p, p, nxt)
        if len(df) < 20:
            continue
        df = label_groups(df)
        est = matched_diff(df, "crowded")
        out.append({"from": p, "to": nxt, "n_stocks": len(df), **est})
    return pd.DataFrame(out)
