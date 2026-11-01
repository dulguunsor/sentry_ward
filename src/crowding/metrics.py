"""Crowding metrics on top of the clean ``holdings`` table.

Definitions
-----------
weight          a position's value divided by the fund's total 13F value that quarter
holder_share    funds holding the stock / funds in the universe that quarter
sum_weight      sum of every universe fund's weight in the stock (how much of the
                group's attention it takes, independent of fund size)
hf_ownership    universe shares held / shares outstanding     (needs market data)
days_to_exit    universe shares held / (20% of average daily volume)  (needs market data)
crowding_score  0-100. Average percentile rank of the components above that are
                available, among stocks held by at least ``min_holders`` funds.
overlap(i, j)   sum over stocks of min(weight_i, weight_j): the share of each
                portfolio the two funds have in common (0 = nothing, 1 = identical)
crowding_index  average pairwise overlap across all funds in the universe that quarter
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd


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


def build_fund_weights(con: duckdb.DuckDBPyConnection) -> None:
    # Several CIKs can share one label (e.g. a parent and an adviser). They are
    # combined into one fund so the fund is never compared with itself.
    con.execute(
        """
        CREATE OR REPLACE TABLE fund_positions AS
        WITH pos AS (
            SELECT u.fund, h.period, h.cusip,
                   any_value(h.issuer) AS issuer,
                   sum(h.value_usd)    AS value_usd,
                   sum(h.shares)       AS shares
            FROM holdings h
            JOIN universe u USING (cik)
            GROUP BY u.fund, h.period, h.cusip
        )
        SELECT *, value_usd / sum(value_usd) OVER (PARTITION BY fund, period) AS weight
        FROM pos
        """
    ).fetchall()


def _has_table(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return bool(
        con.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [name]
        ).fetchall()[:1]
    )


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
    market_join = "LEFT JOIN market m USING (cusip, period)" if market else ""

    con.execute(
        f"""
        CREATE OR REPLACE TABLE stock_crowding AS
        WITH n AS (
            SELECT period, count(DISTINCT fund) AS n_funds FROM fund_positions GROUP BY period
        ),
        s AS (
            SELECT period, cusip,
                   any_value(issuer)    AS issuer,
                   count(*)             AS n_holders,
                   sum(value_usd)       AS total_value_usd,
                   sum(shares)          AS total_shares,
                   sum(weight)          AS sum_weight
            FROM fund_positions
            GROUP BY period, cusip
        ),
        base AS (
            SELECT s.*, n.n_funds, s.n_holders / n.n_funds AS holder_share {market_cols}
            FROM s JOIN n USING (period) {market_join}
            WHERE s.n_holders >= {int(min_holders)}
        ),
        ranked AS (
            SELECT *,
                percent_rank() OVER (PARTITION BY period ORDER BY holder_share) AS r_holders,
                percent_rank() OVER (PARTITION BY period ORDER BY sum_weight)   AS r_weight,
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
            -- Only compare with the quarter immediately before; gaps give NULL.
            CASE WHEN prev_period = last_day(period - INTERVAL 3 MONTH)
                 THEN crowding_score - prev_score END AS score_change
        FROM (
            SELECT *,
                lag(period)         OVER (PARTITION BY cusip ORDER BY period) AS prev_period,
                lag(crowding_score) OVER (PARTITION BY cusip ORDER BY period) AS prev_score
            FROM scored
        )
        """
    ).fetchall()


def build_overlap(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE OR REPLACE TABLE fund_overlap AS
        WITH funds AS (SELECT DISTINCT period, fund FROM fund_positions),
        pairs AS (
            SELECT a.period, a.fund AS fund_a, b.fund AS fund_b
            FROM funds a JOIN funds b ON a.period = b.period AND a.fund < b.fund
        ),
        shared AS (
            SELECT a.period, a.fund AS fund_a, b.fund AS fund_b,
                   sum(least(a.weight, b.weight)) AS overlap,
                   count(*)                       AS shared_stocks
            FROM fund_positions a
            JOIN fund_positions b
              ON a.period = b.period AND a.cusip = b.cusip AND a.fund < b.fund
            GROUP BY ALL
        )
        SELECT p.period, p.fund_a, p.fund_b,
               coalesce(s.overlap, 0)       AS overlap,
               coalesce(s.shared_stocks, 0) AS shared_stocks
        FROM pairs p LEFT JOIN shared s USING (period, fund_a, fund_b)
        """
    ).fetchall()
    con.execute(
        """
        CREATE OR REPLACE TABLE crowding_index AS
        SELECT period,
               avg(overlap)      AS crowding_index,
               median(overlap)   AS median_overlap,
               max(overlap)      AS max_overlap,
               count(*)          AS n_pairs
        FROM fund_overlap
        GROUP BY period
        ORDER BY period
        """
    ).fetchall()


def overlap_matrix(con: duckdb.DuckDBPyConnection, period: str) -> pd.DataFrame:
    """Symmetric fund-by-fund overlap for one quarter, diagonal = 1."""
    long = con.execute(
        "SELECT fund_a, fund_b, overlap FROM fund_overlap WHERE period = ?::DATE", [period]
    ).df()
    funds = sorted(set(long.fund_a) | set(long.fund_b))
    mat = pd.DataFrame(1.0, index=funds, columns=funds)
    for a, b, v in long.itertuples(index=False):
        mat.loc[a, b] = mat.loc[b, a] = v
    return mat


def build_all(con: duckdb.DuckDBPyConnection, universe_csv: Path, min_holders: int = 2) -> pd.DataFrame:
    matched = build_universe(con, universe_csv)
    build_fund_weights(con)
    build_stock_crowding(con, min_holders=min_holders)
    build_overlap(con)
    return matched
