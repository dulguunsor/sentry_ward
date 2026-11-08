"""Look-ups behind the stock and fund pages of `ward live`: search, one stock, one fund.

Everything here only reads the database built by `ward build` (and `ward market` for
tickers). Quarters are compared with the fund's previous filing, which is usually the
quarter before.
"""

from __future__ import annotations

import duckdb

from .live import clean
from .metrics import _has_table


def _rows(con, sql: str, params: list | None = None) -> list[dict]:
    df = con.execute(sql, params or []).df()
    return clean(df.to_dict(orient="records"))


def _tickers(con) -> str:
    """CTE `tk(cusip, ticker)`: tickers from OpenFIGI where `ward market` has run."""
    if _has_table(con, "cusip_map"):
        return "tk AS (SELECT cusip, any_value(ticker) AS ticker FROM cusip_map WHERE ticker IS NOT NULL GROUP BY cusip)"
    return "tk AS (SELECT NULL::VARCHAR AS cusip, NULL::VARCHAR AS ticker WHERE false)"


def _latest(con) -> str | None:
    p = con.execute("SELECT max(period) FROM fund_positions").fetchall()[0][0]
    return str(p) if p is not None else None


# ------------------------------------------------------------------ search


def search(con: duckdb.DuckDBPyConnection, q: str, limit: int = 10) -> dict:
    """Stocks by ticker or company name and funds by name, best matches first."""
    q = (q or "").strip()
    if not q:
        return {"stocks": [], "funds": []}
    period = _latest(con)
    stocks = _rows(con, f"""
        WITH {_tickers(con)},
        s AS (
            SELECT f.issuer_id, first(f.issuer ORDER BY f.value_usd DESC) AS issuer,
                   first(tk.ticker ORDER BY f.value_usd DESC) AS ticker, count(*) AS n_holders
            FROM fund_positions f LEFT JOIN tk USING (cusip)
            WHERE f.period = ? AND NOT f.is_etf
            GROUP BY f.issuer_id
        )
        SELECT s.issuer_id, s.issuer, s.ticker, s.n_holders, c.crowding_score AS score,
               CASE WHEN upper(s.ticker) = upper(?) THEN 0
                    WHEN upper(s.ticker) LIKE upper(?) || '%' THEN 1
                    WHEN upper(s.issuer) LIKE upper(?) || '%' THEN 2 ELSE 3 END AS m
        FROM s LEFT JOIN stock_crowding c ON c.period = ? AND c.issuer_id = s.issuer_id
        WHERE upper(s.ticker) LIKE upper(?) || '%' OR s.issuer ILIKE '%' || ? || '%'
        ORDER BY m, score DESC NULLS LAST, n_holders DESC, issuer
        LIMIT ?""", [period, q, q, q, period, q, q, limit])
    for s in stocks:
        s["key"] = s["ticker"] or s["issuer_id"]
        s.pop("m", None)
    funds = _rows(con, """
        SELECT fund, any_value(style) AS style FROM universe
        WHERE fund ILIKE '%' || ? || '%'
        GROUP BY fund ORDER BY (upper(fund) LIKE upper(?) || '%') DESC, fund LIMIT ?""", [q, q, limit])
    return {"stocks": stocks, "funds": funds}


# ------------------------------------------------------------------ one stock


def _resolve_stock(con, key: str) -> str | None:
    rows = con.execute(f"""
        WITH {_tickers(con)}
        SELECT f.issuer_id FROM fund_positions f LEFT JOIN tk USING (cusip)
        WHERE NOT f.is_etf AND (upper(tk.ticker) = upper(?) OR f.issuer_id = ? OR f.cusip = upper(?))
        ORDER BY f.period DESC, f.value_usd DESC LIMIT 1""", [key, key, key]).fetchall()
    return rows[0][0] if rows else None


def stock(con: duckdb.DuckDBPyConnection, key: str) -> dict | None:
    """One company: crowding over time and every tracked fund holding it."""
    issuer_id = _resolve_stock(con, key)
    if issuer_id is None:
        return None
    period = _latest(con)
    name = _rows(con, f"""
        WITH {_tickers(con)}
        SELECT first(f.issuer ORDER BY f.period DESC, f.value_usd DESC) AS issuer,
               first(tk.ticker ORDER BY f.period DESC, f.value_usd DESC) AS ticker,
               first(f.cusip ORDER BY f.period DESC, f.value_usd DESC) AS cusip
        FROM fund_positions f LEFT JOIN tk USING (cusip) WHERE f.issuer_id = ?""", [issuer_id])[0]
    now = _rows(con, """
        SELECT crowding_score AS score, crowding_rank AS rank, score_change, n_holders, n_funds,
               hf_ownership, days_to_exit, exit_risk, total_value_usd,
               (SELECT count(*) FROM stock_crowding WHERE period = ?) AS n_ranked
        FROM stock_crowding WHERE period = ? AND issuer_id = ?""", [period, period, issuer_id])
    history = _rows(con, """
        SELECT strftime(period, '%Y-%m-%d') AS period, crowding_score AS score, crowding_rank AS rank,
               n_holders, n_funds, hf_ownership, days_to_exit
        FROM stock_crowding WHERE issuer_id = ? ORDER BY period""", [issuer_id])
    # Each fund's position now and in its previous filing.
    holders = _rows(con, """
        WITH filed AS (SELECT DISTINCT fund, period FROM fund_positions),
        prev AS (
            SELECT fund, max(period) AS prev_period FROM filed WHERE period < ? GROUP BY fund
        ),
        styles AS (SELECT fund, any_value(style) AS style FROM universe GROUP BY fund),
        cur AS (SELECT fund, shares, value_usd, weight FROM fund_positions
                WHERE period = ? AND issuer_id = ? AND NOT is_etf),
        old AS (SELECT f.fund, f.shares, f.value_usd FROM fund_positions f JOIN prev p
                  ON f.fund = p.fund AND f.period = p.prev_period
                WHERE f.issuer_id = ? AND NOT f.is_etf)
        SELECT coalesce(c.fund, o.fund) AS fund, st.style, c.shares, c.value_usd, c.weight,
               o.shares AS prev_shares, o.value_usd AS prev_value_usd,
               strftime(p.prev_period, '%Y-%m-%d') AS prev_period,
               CASE WHEN c.fund IS NULL THEN 'sold out'
                    WHEN p.prev_period IS NULL THEN 'first filing'
                    WHEN o.fund IS NULL THEN 'new'
                    WHEN c.shares > o.shares * 1.005 THEN 'added'
                    WHEN c.shares < o.shares * 0.995 THEN 'trimmed'
                    ELSE 'unchanged' END AS change
        FROM cur c
        FULL JOIN old o USING (fund)
        LEFT JOIN prev p ON p.fund = coalesce(c.fund, o.fund)
        LEFT JOIN styles st ON st.fund = coalesce(c.fund, o.fund)
        -- A fund that has not filed for the latest quarter yet is not "sold out".
        WHERE c.fund IS NOT NULL OR coalesce(o.fund, '') IN (SELECT fund FROM filed WHERE period = ?)
        ORDER BY c.value_usd DESC NULLS LAST, fund""", [period, period, issuer_id, issuer_id, period])
    n_funds = con.execute("SELECT count(DISTINCT fund) FROM fund_positions WHERE period = ?", [period]).fetchall()[0][0]
    return clean({
        "issuer_id": issuer_id, **name, "period": period, "n_funds_filed": n_funds,
        "now": now[0] if now else None,
        "history": history,
        "holders": [h for h in holders if h["change"] != "sold out"],
        "sold_out": [h for h in holders if h["change"] == "sold out"],
    })


# ------------------------------------------------------------------ one fund


def _resolve_fund(con, name: str) -> str | None:
    rows = con.execute("SELECT DISTINCT fund FROM universe WHERE upper(fund) = upper(?)", [name]).fetchall()
    return rows[0][0] if rows else None


def fund(con: duckdb.DuckDBPyConnection, name: str, top: int = 200) -> dict | None:
    """One fund: size and active share over time, holdings and changes, closest funds."""
    f = _resolve_fund(con, name)
    if f is None:
        return None
    periods = [r[0] for r in con.execute(
        "SELECT DISTINCT period FROM fund_positions WHERE fund = ? ORDER BY period", [f]).fetchall()]
    if not periods:
        return clean({"fund": f, "period": None, "history": [], "holdings": [], "closed": [], "overlap": []})
    period, prev = periods[-1], (periods[-2] if len(periods) > 1 else None)
    info = _rows(con, """
        SELECT any_value(style) AS style, list(DISTINCT manager_name ORDER BY manager_name) AS filers,
               list(DISTINCT cik ORDER BY cik) AS ciks
        FROM universe WHERE fund = ?""", [f])[0]
    history = _rows(con, """
        SELECT strftime(period, '%Y-%m-%d') AS period, total_value_usd, n_stocks, active_share, etf_share
        FROM fund_stats WHERE fund = ? ORDER BY period""", [f])
    holdings = _rows(con, f"""
        WITH {_tickers(con)},
        cur AS (SELECT * FROM fund_positions WHERE fund = ? AND period = ? AND NOT is_etf),
        old AS (SELECT issuer_id, shares, value_usd, weight FROM fund_positions
                WHERE fund = ? AND period = ? AND NOT is_etf)
        SELECT c.issuer_id, c.issuer, tk.ticker, c.shares, c.value_usd, c.weight,
               a.market_weight, s.crowding_score AS score, s.n_holders,
               o.shares AS prev_shares, o.weight AS prev_weight,
               CASE WHEN ?::DATE IS NULL THEN 'first filing'
                    WHEN o.issuer_id IS NULL THEN 'new'
                    WHEN c.shares > o.shares * 1.005 THEN 'added'
                    WHEN c.shares < o.shares * 0.995 THEN 'trimmed'
                    ELSE 'unchanged' END AS change
        FROM cur c
        LEFT JOIN tk USING (cusip)
        LEFT JOIN old o USING (issuer_id)
        LEFT JOIN fund_active a ON a.fund = c.fund AND a.period = c.period AND a.issuer_id = c.issuer_id
        LEFT JOIN stock_crowding s ON s.period = c.period AND s.issuer_id = c.issuer_id
        ORDER BY c.value_usd DESC, c.issuer_id""", [f, period, f, prev, prev])
    closed = _rows(con, f"""
        WITH {_tickers(con)}
        SELECT o.issuer_id, o.issuer, tk.ticker, o.shares AS prev_shares, o.value_usd AS prev_value_usd,
               o.weight AS prev_weight
        FROM fund_positions o LEFT JOIN tk USING (cusip)
        WHERE o.fund = ? AND o.period = ? AND NOT o.is_etf
          AND o.issuer_id NOT IN (SELECT issuer_id FROM fund_positions WHERE fund = ? AND period = ? AND NOT is_etf)
        ORDER BY o.value_usd DESC LIMIT 50""", [f, prev, f, period]) if prev else []
    for h in holdings + closed:
        h["key"] = h["ticker"] or h["issuer_id"]
    overlap = _rows(con, """
        SELECT CASE WHEN fund_a = ? THEN fund_b ELSE fund_a END AS fund,
               overlap, active_overlap, shared_stocks
        FROM fund_overlap WHERE period = ? AND (fund_a = ? OR fund_b = ?)
        ORDER BY overlap DESC, fund""", [f, period, f, f])
    etf = _rows(con, """
        SELECT count(*) AS n, coalesce(sum(value_usd), 0) AS value_usd
        FROM fund_positions WHERE fund = ? AND period = ? AND is_etf""", [f, period])[0]
    counts = {k: sum(h["change"] == k for h in holdings) for k in ("new", "added", "trimmed", "unchanged")}
    counts["closed"] = len(closed)
    return clean({
        "fund": f, **info, "period": str(period), "prev_period": str(prev) if prev else None,
        "latest_period": _latest(con),
        "now": history[-1] if history else None, "history": history,
        "n_holdings": len(holdings), "holdings": holdings[:top], "closed": closed,
        "changes": counts, "etf": etf, "overlap": overlap,
    })
