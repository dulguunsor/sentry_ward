"""`ward dashboard`: one self-contained HTML file built from the database.

The page has no server and no external scripts: the data is embedded as JSON and drawn
with plain JavaScript and SVG, so the file can be opened locally, attached to an email
or published as a static page.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from importlib import resources
from pathlib import Path

import duckdb
import pandas as pd

from . import market, metrics, stress

TOP_STOCKS = 30
TOP_HARD_EXIT = 15


def _clean(v):
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, (date, datetime, pd.Timestamp)):
        return str(v)[:10]
    if hasattr(v, "item"):            # numpy scalars
        return _clean(v.item())
    if v is pd.NA:
        return None
    return v


def _rows(df: pd.DataFrame) -> list[dict]:
    return [{k: _clean(v) for k, v in r.items()} for r in df.to_dict(orient="records")]


def _matrix(m: pd.DataFrame) -> dict:
    return {"funds": list(m.index), "values": [[_clean(round(float(x), 4)) if pd.notna(x) else None
                                                for x in row] for row in m.values]}


def gather(con: duckdb.DuckDBPyConnection, out_dir: Path) -> dict:
    has = lambda t: metrics._has_table(con, t)  # noqa: E731
    if not has("stock_crowding"):
        raise RuntimeError("Run `ward build` first.")
    period = str(con.execute("SELECT max(period) FROM crowding_index").fetchall()[0][0])
    with_market = has("market")

    stocks = con.execute(
        """
        SELECT crowding_rank AS rank, coalesce(ticker, cusip) AS ticker, issuer, n_holders AS holders,
               n_funds, sum_weight, total_value_usd AS value_usd, hf_ownership AS own,
               days_to_exit AS dte, exit_risk, crowding_score AS score, score_change AS change,
               total_shares, adv_shares, daily_vol, close
        FROM stock_crowding WHERE period = ?::DATE
        ORDER BY crowding_rank LIMIT ?
        """,
        [period, TOP_STOCKS],
    ).df()

    # Same definition as H2 in PREREGISTRATION.md: top 25% by crowding score and by days to exit.
    hard_exit = con.execute(
        f"""
        WITH s AS (SELECT * FROM stock_crowding WHERE period = ?::DATE AND days_to_exit IS NOT NULL),
        q AS (SELECT quantile_cont(crowding_score, {1 - stress.FRAGILE_TOP}) AS qs,
                     quantile_cont(days_to_exit, {1 - stress.FRAGILE_TOP}) AS qd FROM s)
        SELECT coalesce(ticker, cusip) AS ticker, issuer, n_holders AS holders, hf_ownership AS own,
               days_to_exit AS dte, exit_risk, crowding_score AS score, crowding_rank AS rank,
               total_value_usd AS value_usd
        FROM s, q WHERE crowding_score >= qs AND days_to_exit >= qd
        ORDER BY exit_risk DESC NULLS LAST, crowding_score DESC LIMIT ?
        """,
        [period, TOP_HARD_EXIT],
    ).df()

    index = con.execute(
        """SELECT period, crowding_index AS raw, active_index AS active, n_pairs,
                  change_same_funds AS raw_change, active_change_same_funds AS active_change
           FROM crowding_index ORDER BY period"""
    ).df()
    funds = con.execute(
        """SELECT fund, n_stocks AS stocks, total_value_usd AS value_usd, etf_share, active_share
           FROM fund_stats WHERE period = ?::DATE ORDER BY active_share""",
        [period],
    ).df()
    styles = dict(con.execute("SELECT DISTINCT fund, style FROM universe").fetchall())
    funds["style"] = funds["fund"].map(styles)
    etfs = con.execute(
        """SELECT cusip, issuer, title_of_class AS class, n_holders AS holders, total_value_usd AS value_usd
           FROM etf_holdings WHERE period = ?::DATE ORDER BY n_holders DESC, total_value_usd DESC LIMIT 8""",
        [period],
    ).df()

    stress_results = {}
    for key, name in (("primary", "stress.json"), ("public", "stress_public.json")):
        f = out_dir / name
        if f.exists():
            stress_results[key] = json.loads(f.read_text(encoding="utf-8"))

    return {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "period": period,
        "n_funds": len(metrics.overlap_matrix(con, period)),
        "missing_funds": metrics.missing_funds(con, period),
        "coverage": market.coverage(con) if with_market else None,
        "stocks": _rows(stocks),
        "hard_exit": _rows(hard_exit),
        "index": _rows(index),
        "pairs": _rows(metrics.top_pairs(con, period, n=8)),
        "overlap": _matrix(metrics.overlap_matrix(con, period)),
        "active_overlap": _matrix(metrics.overlap_matrix(con, period, column="active_overlap")),
        "funds": _rows(funds),
        "etfs": _rows(etfs),
        "stress": stress_results,
        "events": [{"name": e.name, "start": str(e.start), "end": str(e.end)} for e in stress.EVENTS],
    }


def render(data: dict) -> str:
    template = resources.files("sentryward").joinpath("dashboard_template.html").read_text(encoding="utf-8")
    payload = json.dumps(data, allow_nan=False, separators=(",", ":")).replace("</", "<\\/")
    return template.replace("/*__DATA__*/null", payload)


def write(con: duckdb.DuckDBPyConnection, out_dir: Path) -> Path:
    data = gather(con, out_dir)
    path = out_dir / f"sentry-ward-{data['period']}.html"
    path.write_text(render(data), encoding="utf-8")
    return path
