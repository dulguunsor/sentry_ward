"""Crowding metrics on top of the clean ``holdings`` table.

Steps (all run by ``build_all``)
--------------------------------
1. holdings_ok     holdings with likely unit errors removed: a row is dropped when its
                   implied price (value / shares) is more than 10x away from the median
                   price every filer reported for that stock that quarter.
2. universe        the funds in universe.csv matched to SEC filer CIKs.
3. fund_positions  each fund's positions grouped by company. ETFs and other fund
                   products are flagged and kept out of ``weight``.
4. market_weights  the "13F market": every filer's stock holdings added up, as weights.
                   Used as the baseline for what a fund would hold with no view.
5. stock_crowding  per stock and quarter: holders, combined weight, score, rank.
6. fund_overlap    per fund pair: raw overlap and active overlap.
7. crowding_index  per quarter: averages, plus the like-for-like change.

Definitions
-----------
weight          a position's value / the fund's total stock value (ETFs excluded)
market_weight   the stock's share of all 13F filers' combined stock holdings
overweight      max(weight - market_weight, 0): how much more the fund holds than the market
active_share    sum of a fund's overweights (0 = index-like, 1 = nothing like the market)
overlap(A, B)   sum over stocks of min(weight_A, weight_B). 0 = nothing shared, 1 = identical.
                Large diversified funds score high partly because both resemble the market.
active_overlap  weighted Jaccard of the two funds' overweights:
                sum min(over_A, over_B) / sum max(over_A, over_B).
                "Of the bets these funds make beyond the market, how much is the same bet?"
crowding_score  0-100, among stocks held by at least ``min_holders`` funds. holder_share and
                sum_weight are divided by the quarter's top value; hf_ownership and
                days_to_exit (when market data exists) use percentile ranks.
Stocks are grouped by company (first 6 CUSIP characters), so share classes count once.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

# Issuer names of fund trusts. They put many products under one CUSIP issuer code,
# so they are never merged by issuer code.
FUND_TRUST_PATTERN = r"(\bTR\b|TRUST|\bETF|\bFD\b|\bFDS\b|FUND|PORTFOLIO|SELECT SECTOR|\bINDEX\b)"

# ETF and ETP sponsors and wording, matched on the issuer name ...
ETF_ISSUER_PATTERN = (
    r"(\bETF\b|ETF TR|ISHARES|SPDR|SELECT SECTOR|INVESCO QQQ|POWERSHARES|PROSHARES|DIREXION|"
    r"WISDOMTREE|VANECK|MARKET VECTORS|GLOBAL X|SCHWAB STRATEGIC|FIRST TR|XTRACKERS|"
    r"ROUNDHILL|GRAYSCALE|SPROTT PHYSICAL|UNITED STATES OIL|VANGUARD (INDEX|WORLD|SPECIALIZED|"
    r"SCOTTSDALE|WHITEHALL|ADMIRAL|INTL|TAX|BD)|\bINDEX FDS?\b|EXCHANGE TRADED)"
)
# ... and on the title of class.
ETF_TITLE_PATTERN = r"(\bETF\b|\bETN\b|\bINDEX\b|TR UNIT|UNIT SER)"

PRICE_TOLERANCE = 10  # keep rows whose implied price is within 10x of the median


# ------------------------------------------------------------------ helpers


def create_macros(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        f"""
        CREATE OR REPLACE MACRO clean_name(x) AS regexp_replace(trim(x), '\\s+', ' ', 'g');
        CREATE OR REPLACE MACRO is_fund_product(issuer, title) AS
            regexp_matches(upper(coalesce(issuer, '')), '{ETF_ISSUER_PATTERN}')
            OR regexp_matches(upper(coalesce(title, '')), '{ETF_TITLE_PATTERN}');
        CREATE OR REPLACE MACRO issuer_key(cusip, issuer) AS
            CASE WHEN regexp_matches(upper(coalesce(issuer, '')), '{FUND_TRUST_PATTERN}')
                   OR regexp_matches(upper(coalesce(issuer, '')), '{ETF_ISSUER_PATTERN}')
                 THEN cusip ELSE left(cusip, 6) END;
        """
    ).fetchall()


# Kept for callers and tests that only need the issuer key.
create_issuer_key = create_macros


def _has_table(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return bool(
        con.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [name]
        ).fetchall()
    )


# ------------------------------------------------------------------ 1. data checks


def build_holdings_ok(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    con.execute(
        f"""
        CREATE OR REPLACE TABLE holdings_ok AS
        WITH p AS (
            SELECT *, value_usd / nullif(shares, 0) AS px FROM holdings
        ),
        med AS (
            SELECT period, cusip, median(px) AS med_px, count(*) AS n
            FROM p WHERE px > 0 GROUP BY ALL
        )
        SELECT p.* EXCLUDE (px)
        FROM p LEFT JOIN med USING (period, cusip)
        WHERE coalesce(med.n, 0) < 3      -- too few filers to judge: keep
           OR p.px BETWEEN med.med_px / {PRICE_TOLERANCE} AND med.med_px * {PRICE_TOLERANCE}
        """
    ).fetchall()
    total = con.execute("SELECT count(*) FROM holdings").fetchall()[0][0]
    kept = con.execute("SELECT count(*) FROM holdings_ok").fetchall()[0][0]
    return {"rows": total, "kept": kept, "dropped": total - kept}


# ------------------------------------------------------------------ 2. universe


def build_universe(con: duckdb.DuckDBPyConnection, universe_csv: Path) -> pd.DataFrame:
    """Match the universe name patterns to filer CIKs. Returns the matches for review."""
    patterns = pd.read_csv(universe_csv)
    con.register("universe_patterns_df", patterns)
    con.execute(
        """
        CREATE OR REPLACE TABLE universe AS
        SELECT p.label AS fund, p.style, m.cik, m.manager_name, m.first_period, m.last_period
        FROM universe_patterns_df p
        JOIN managers m ON upper(m.manager_name) LIKE upper(p.name_pattern)
        """
    ).fetchall()
    con.unregister("universe_patterns_df")
    return con.execute("SELECT * FROM universe ORDER BY fund, cik").df()


def unmatched_patterns(con: duckdb.DuckDBPyConnection, universe_csv: Path) -> list[str]:
    labels = set(pd.read_csv(universe_csv)["label"])
    found = {r[0] for r in con.execute("SELECT DISTINCT fund FROM universe").fetchall()}
    return sorted(labels - found)


# ------------------------------------------------------------------ 3-4. positions and market


def build_fund_weights(con: duckdb.DuckDBPyConnection) -> None:
    # Several CIKs can share one label (a parent and an adviser). They are combined
    # into one fund so a fund is never compared with itself. Each company's largest
    # line gives the label (CUSIP, name, class).
    con.execute(
        """
        CREATE OR REPLACE TABLE fund_positions AS
        WITH pos AS (
            SELECT u.fund, h.period,
                   issuer_key(h.cusip, h.issuer)              AS issuer_id,
                   arg_max(h.cusip, h.value_usd)              AS cusip,
                   arg_max(clean_name(h.issuer), h.value_usd) AS issuer,
                   arg_max(h.title_of_class, h.value_usd)     AS title_of_class,
                   sum(h.value_usd)                           AS value_usd,
                   sum(h.shares)                              AS shares
            FROM holdings_ok h
            JOIN universe u USING (cik)
            GROUP BY ALL
        ),
        flagged AS (
            SELECT *, is_fund_product(issuer, title_of_class) AS is_etf FROM pos
        )
        SELECT *,
            CASE WHEN NOT is_etf THEN value_usd / sum(CASE WHEN NOT is_etf THEN value_usd END)
                 OVER (PARTITION BY fund, period) END AS weight
        FROM flagged
        """
    ).fetchall()


def build_market_weights(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE OR REPLACE TABLE market_weights AS
        WITH allpos AS (
            SELECT period, issuer_key(cusip, issuer) AS issuer_id,
                   arg_max(issuer, value_usd)         AS issuer,
                   arg_max(title_of_class, value_usd) AS title_of_class,
                   sum(value_usd)                     AS value_usd
            FROM holdings_ok
            GROUP BY ALL
        )
        SELECT period, issuer_id, value_usd / sum(value_usd) OVER (PARTITION BY period) AS market_weight
        FROM allpos
        WHERE NOT is_fund_product(issuer, title_of_class)
        """
    ).fetchall()
    con.execute(
        """
        CREATE OR REPLACE TABLE fund_active AS
        SELECT f.fund, f.period, f.issuer_id, f.weight,
               coalesce(m.market_weight, 0)                         AS market_weight,
               greatest(f.weight - coalesce(m.market_weight, 0), 0) AS overweight
        FROM fund_positions f
        LEFT JOIN market_weights m USING (period, issuer_id)
        WHERE NOT f.is_etf
        """
    ).fetchall()
    con.execute(
        """
        CREATE OR REPLACE TABLE fund_stats AS
        SELECT p.fund, p.period,
               count(*) FILTER (WHERE NOT p.is_etf)                                    AS n_stocks,
               sum(p.value_usd)                                                        AS total_value_usd,
               coalesce(sum(p.value_usd) FILTER (WHERE p.is_etf), 0) / sum(p.value_usd) AS etf_share,
               a.active_share
        FROM fund_positions p
        LEFT JOIN (SELECT fund, period, sum(overweight) AS active_share FROM fund_active GROUP BY ALL) a
            USING (fund, period)
        GROUP BY p.fund, p.period, a.active_share
        """
    ).fetchall()


def build_etf_holdings(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE OR REPLACE TABLE etf_holdings AS
        SELECT period, cusip, any_value(issuer) AS issuer, any_value(title_of_class) AS title_of_class,
               count(*) AS n_holders, sum(value_usd) AS total_value_usd
        FROM fund_positions WHERE is_etf
        GROUP BY period, cusip
        """
    ).fetchall()


# ------------------------------------------------------------------ 5. stocks


def build_stock_crowding(con: duckdb.DuckDBPyConnection, min_holders: int = 2) -> None:
    market = _has_table(con, "market")
    market_cols = (
        """, m.shares_outstanding, m.adv_shares,
           s.total_shares / nullif(m.shares_outstanding, 0)        AS hf_ownership,
           s.total_shares / nullif(0.2 * m.adv_shares, 0)          AS days_to_exit"""
        if market
        else """, NULL::DOUBLE AS shares_outstanding, NULL::DOUBLE AS adv_shares,
           NULL::DOUBLE AS hf_ownership, NULL::DOUBLE AS days_to_exit"""
    )
    market_join = "LEFT JOIN market m ON m.cusip = s.cusip AND m.period = s.period" if market else ""

    con.execute(
        f"""
        CREATE OR REPLACE TABLE stock_crowding AS
        WITH n AS (
            SELECT period, count(DISTINCT fund) AS n_funds FROM fund_positions GROUP BY period
        ),
        s AS (
            SELECT period, issuer_id,
                   arg_max(cusip, value_usd)  AS cusip,
                   arg_max(issuer, value_usd) AS issuer,
                   count(*)             AS n_holders,
                   sum(value_usd)       AS total_value_usd,
                   sum(shares)          AS total_shares,
                   sum(weight)          AS sum_weight
            FROM fund_positions
            WHERE NOT is_etf
            GROUP BY period, issuer_id
        ),
        base AS (
            SELECT s.*, n.n_funds, s.n_holders / n.n_funds AS holder_share {market_cols}
            FROM s JOIN n USING (period) {market_join}
            WHERE s.n_holders >= {int(min_holders)}
        ),
        ranked AS (
            SELECT *,
                holder_share / max(holder_share) OVER (PARTITION BY period) AS r_holders,
                sum_weight   / max(sum_weight)   OVER (PARTITION BY period) AS r_weight,
                CASE WHEN hf_ownership IS NULL THEN NULL ELSE
                    percent_rank() OVER (PARTITION BY period, hf_ownership IS NULL ORDER BY hf_ownership) END AS r_own,
                CASE WHEN days_to_exit IS NULL THEN NULL ELSE
                    percent_rank() OVER (PARTITION BY period, days_to_exit IS NULL ORDER BY days_to_exit) END AS r_dte
            FROM base
        ),
        scored AS (
            SELECT *,
                100 * (r_holders + r_weight + coalesce(r_own, 0) + coalesce(r_dte, 0))
                    / (2 + (r_own IS NOT NULL)::INT + (r_dte IS NOT NULL)::INT) AS crowding_score
            FROM ranked
        )
        SELECT * EXCLUDE (prev_period, prev_score),
            rank() OVER (PARTITION BY period ORDER BY crowding_score DESC) AS crowding_rank,
            -- Only compare with the quarter immediately before; gaps give NULL.
            CASE WHEN prev_period = last_day(period - INTERVAL 3 MONTH)
                 THEN crowding_score - prev_score END AS score_change
        FROM (
            SELECT *,
                lag(period)         OVER (PARTITION BY issuer_id ORDER BY period) AS prev_period,
                lag(crowding_score) OVER (PARTITION BY issuer_id ORDER BY period) AS prev_score
            FROM scored
        )
        """
    ).fetchall()


# ------------------------------------------------------------------ 6-7. overlap and index


def build_overlap(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE OR REPLACE TABLE fund_overlap AS
        WITH funds AS (SELECT DISTINCT period, fund FROM fund_active),
        pairs AS (
            SELECT a.period, a.fund AS fund_a, b.fund AS fund_b
            FROM funds a JOIN funds b ON a.period = b.period AND a.fund < b.fund
        ),
        shared AS (
            SELECT a.period, a.fund AS fund_a, b.fund AS fund_b,
                   sum(least(a.weight, b.weight))         AS overlap,
                   sum(least(a.overweight, b.overweight)) AS active_min,
                   count(*)                               AS shared_stocks
            FROM fund_active a
            JOIN fund_active b
              ON a.period = b.period AND a.issuer_id = b.issuer_id AND a.fund < b.fund
            GROUP BY ALL
        ),
        act AS (SELECT fund, period, sum(overweight) AS active_share FROM fund_active GROUP BY ALL)
        SELECT p.period, p.fund_a, p.fund_b,
               coalesce(s.overlap, 0)       AS overlap,
               coalesce(s.active_min, 0)
                 / nullif(aa.active_share + ab.active_share - coalesce(s.active_min, 0), 0) AS active_overlap,
               coalesce(s.shared_stocks, 0) AS shared_stocks
        FROM pairs p
        LEFT JOIN shared s USING (period, fund_a, fund_b)
        JOIN act aa ON aa.fund = p.fund_a AND aa.period = p.period
        JOIN act ab ON ab.fund = p.fund_b AND ab.period = p.period
        """
    ).fetchall()
    build_crowding_index(con)


def build_crowding_index(con: duckdb.DuckDBPyConnection) -> None:
    """Quarterly averages, plus the change measured only on fund pairs present in both
    quarters, so a fund joining or leaving does not move the index by itself."""
    con.execute(
        """
        CREATE OR REPLACE TABLE crowding_index AS
        WITH lvl AS (
            SELECT period,
                   avg(overlap)        AS crowding_index,
                   avg(active_overlap) AS active_index,
                   median(overlap)     AS median_overlap,
                   max(overlap)        AS max_overlap,
                   count(*)            AS n_pairs
            FROM fund_overlap GROUP BY period
        ),
        chg AS (
            SELECT c.period,
                   avg(c.overlap - p.overlap)               AS change_same_funds,
                   avg(c.active_overlap - p.active_overlap) AS active_change_same_funds,
                   count(*)                                 AS n_pairs_compared
            FROM fund_overlap c
            JOIN fund_overlap p
              ON p.fund_a = c.fund_a AND p.fund_b = c.fund_b
             AND p.period = last_day(c.period - INTERVAL 3 MONTH)
            GROUP BY c.period
        )
        SELECT * FROM lvl LEFT JOIN chg USING (period) ORDER BY period
        """
    ).fetchall()


# ------------------------------------------------------------------ report helpers


def missing_funds(con: duckdb.DuckDBPyConnection, period: str) -> list[str]:
    """Universe funds with no holdings report for this quarter."""
    rows = con.execute(
        """
        SELECT DISTINCT fund FROM universe
        WHERE fund NOT IN (SELECT fund FROM fund_positions WHERE period = ?::DATE)
        ORDER BY fund
        """,
        [period],
    ).fetchall()
    return [r[0] for r in rows]


def top_pairs(con: duckdb.DuckDBPyConnection, period: str, n: int = 10) -> pd.DataFrame:
    """Pairs ranked by active overlap: shared bets beyond what the market explains."""
    return con.execute(
        """
        SELECT fund_a, fund_b, round(active_overlap, 3) AS active_overlap,
               round(overlap, 3) AS raw_overlap, shared_stocks
        FROM fund_overlap WHERE period = ?::DATE
        ORDER BY active_overlap DESC NULLS LAST, overlap DESC LIMIT ?
        """,
        [period, n],
    ).df()


def overlap_matrix(con: duckdb.DuckDBPyConnection, period: str, column: str = "overlap") -> pd.DataFrame:
    """Symmetric fund-by-fund matrix for one quarter, diagonal = 1."""
    if column not in ("overlap", "active_overlap"):
        raise ValueError(column)
    long = con.execute(
        f"SELECT fund_a, fund_b, {column} FROM fund_overlap WHERE period = ?::DATE", [period]
    ).df()
    funds = sorted(set(long.fund_a) | set(long.fund_b))
    mat = pd.DataFrame(1.0, index=funds, columns=funds)
    for a, b, v in long.itertuples(index=False):
        mat.loc[a, b] = mat.loc[b, a] = v
    return mat


def build_all(con: duckdb.DuckDBPyConnection, universe_csv: Path, min_holders: int = 2) -> pd.DataFrame:
    create_macros(con)
    checks = build_holdings_ok(con)
    matched = build_universe(con, universe_csv)
    build_fund_weights(con)
    build_market_weights(con)
    build_etf_holdings(con)
    build_stock_crowding(con, min_holders=min_holders)
    build_overlap(con)
    matched.attrs["checks"] = checks
    return matched
