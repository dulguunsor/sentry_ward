"""Sentry Ward command line: ward sample | download | load | build | report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import pandas as pd

from . import clean, config, market, metrics, sec13f


def _connect(paths: config.Paths) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(paths.db))


def cmd_download(args, paths):
    files = sec13f.download(paths, since_year=args.since)
    print(f"{len(files)} data set files in {paths.raw}")


def cmd_sample(args, paths):
    from .sample import write_sample

    write_sample(paths.raw)
    print(f"Wrote 2 small sample data sets to {paths.raw}. Next: ward load")


def cmd_load(args, paths):
    con = _connect(paths)
    zips = list(paths.raw.glob("*_form13f.zip"))
    if not zips:
        raise SystemExit(f"No *_form13f.zip files in {paths.raw}. Run `ward download` first.")
    new = sec13f.load_all(con, zips, force=args.force)
    print(f"Loaded {len(new)} new files ({len(zips) - len(new)} already loaded).")
    counts = clean.build_all(con)
    for table, n in counts.items():
        print(f"  {table:<17} {n:>12,}")


def cmd_build(args, paths):
    con = _connect(paths)
    print("Building metrics (on full 13F data this takes a few minutes)...")
    matched = metrics.build_all(con, Path(args.universe), min_holders=args.min_holders)
    c = matched.attrs["checks"]
    print(f"Unit check: rescaled {c['rescaled_filings']:,} filings ({c['rescaled_rows']:,} rows) "
          f"reported in the wrong units.")
    print(f"            dropped {c['dropped']:,} of {c['rows']:,} rows "
          f"({c['dropped'] / max(c['rows'], 1):.2%}) still over 10x off the median price.")
    print(f"Universe: {matched.fund.nunique()} funds from {len(matched)} filer CIKs")
    print(matched[["fund", "cik", "manager_name"]].to_string(index=False))
    print("\nData checks per fund (quarters filed, kept, rescaled; share of rows dropped):")
    print(metrics.universe_data_checks(con).to_string(index=False))
    missing = metrics.unmatched_patterns(con, Path(args.universe))
    if missing:
        print("\nNo filer matched these names (check the pattern in the universe file):")
        print("  " + ", ".join(missing))


def cmd_market(args, paths):
    con = _connect(paths)
    if not metrics._has_table(con, "fund_positions"):
        raise SystemExit("Run `ward build` first: market data is fetched for the stocks the funds hold.")
    skip = [name for name in ("figi", "prices", "shares") if getattr(args, f"skip_{name}")]
    out = market.run_all(con, start=f"{args.since - 1}-09-01", skip=skip, refresh_prices=args.refresh_prices)
    m = out["market"]
    print(f"\nMarket table: {m['rows']:,} stock-quarters, {m['priced']:,} with prices, "
          f"{m['matched']:,} matching the 13F price, {m['with_shares']:,} also with shares outstanding.")
    cov = market.coverage(con)
    print(f"Latest quarter: {cov['value_priced_share']:.1%} of the funds' stock value has usable prices, "
          f"{cov['value_with_shares_share']:.1%} also has shares outstanding.")
    metrics.create_macros(con)
    metrics.build_stock_crowding(con, min_holders=args.min_holders)
    print("Crowding scores updated with ownership and days to exit. Next: ward report")


def _records(df):
    """Rows as dicts with missing values as None, so the JSON stays valid."""
    return df.astype(object).where(df.notna(), None).to_dict(orient="records")


def _matrix(m):
    rows = [[None if pd.isna(x) else round(float(x), 4) for x in row] for row in m.values]
    return {"funds": list(m.index), "values": rows}


def _fmt(df):
    return df.to_string(index=False) if len(df) else "  (none)"


def cmd_report(args, paths):
    con = _connect(paths)
    period = args.period or con.execute("SELECT max(period)::VARCHAR FROM crowding_index").fetchall()[0][0]
    top = con.execute(
        """
        SELECT crowding_rank AS rank, coalesce(ticker, cusip) AS ticker, left(issuer, 24) AS issuer,
               n_holders AS holders, round(sum_weight, 3) AS sum_weight,
               round(total_value_usd / 1e9, 2) AS value_bn,
               round(100 * hf_ownership, 2) AS own_pct, round(days_to_exit, 1) AS dte,
               round(exit_risk, 0) AS exit_risk,
               round(crowding_score, 1) AS score, round(score_change, 1) AS change
        FROM stock_crowding WHERE period = ?::DATE
        ORDER BY crowding_rank LIMIT ?
        """,
        [period, args.top],
    ).df()
    if top["own_pct"].isna().all():
        top = top.drop(columns=["own_pct", "dte", "exit_risk"])
    etfs = con.execute(
        """
        SELECT cusip, left(issuer, 28) AS issuer, left(coalesce(title_of_class, ''), 24) AS class,
               n_holders AS holders, round(total_value_usd / 1e9, 2) AS value_bn
        FROM etf_holdings WHERE period = ?::DATE
        ORDER BY n_holders DESC, total_value_usd DESC LIMIT 10
        """,
        [period],
    ).df()
    funds = con.execute(
        """
        SELECT fund, n_stocks AS stocks, round(total_value_usd / 1e9, 1) AS value_bn,
               round(etf_share, 3) AS etf_share, round(active_share, 3) AS active_share
        FROM fund_stats WHERE period = ?::DATE ORDER BY active_share
        """,
        [period],
    ).df()
    idx = con.execute(
        """
        SELECT period, round(crowding_index, 4) AS raw_index, round(active_index, 4) AS active_index,
               n_pairs, round(change_same_funds, 4) AS raw_change, round(active_change_same_funds, 4)
               AS active_change, n_pairs_compared AS compared
        FROM crowding_index ORDER BY period
        """
    ).df()
    matrix = metrics.overlap_matrix(con, period)
    active_matrix = metrics.overlap_matrix(con, period, column="active_overlap")
    pairs = metrics.top_pairs(con, period)
    missing = metrics.missing_funds(con, period)

    print(f"\nQuarter ending {period}: {len(matrix)} funds reporting")
    if missing:
        print(f"No 13F holdings report this quarter: {', '.join(missing)}")
    print("\nMost crowded stocks (ETFs excluded). own_pct = share of the company the funds own;"
          "\ndte = days to sell it all at 20% of daily volume; exit_risk = percentile of both:")
    print(_fmt(top))
    print("\nMost similar fund pairs, by active overlap (shared bets beyond the market):")
    print(_fmt(pairs))
    print("\nFunds, from most index-like to least (active share 0 = holds the market):")
    print(_fmt(funds))
    print("\nETFs held most widely (kept out of the scores above):")
    print(_fmt(etfs))
    print("\nCrowding index by quarter. Change columns compare only fund pairs present in both quarters:")
    print(_fmt(idx.tail(8)))

    out = paths.out
    top.to_csv(out / f"top_crowded_{period}.csv", index=False)
    idx.to_csv(out / "crowding_index.csv", index=False)
    matrix.to_csv(out / f"overlap_matrix_{period}.csv")
    active_matrix.to_csv(out / f"active_overlap_matrix_{period}.csv")
    pairs.to_csv(out / f"top_pairs_{period}.csv", index=False)
    funds.to_csv(out / f"funds_{period}.csv", index=False)
    etfs.to_csv(out / f"etfs_{period}.csv", index=False)
    with open(out / f"dashboard_{period}.json", "w") as fh:
        json.dump(
            {
                "period": period,
                "missing_funds": missing,
                "top_crowded": _records(top),
                "top_pairs": _records(pairs),
                "funds": _records(funds),
                "etfs": _records(etfs),
                "crowding_index": _records(idx.assign(period=idx.period.astype(str))),
                "overlap_matrix": _matrix(matrix),
                "active_overlap_matrix": _matrix(active_matrix),
            },
            fh,
            indent=2,
            allow_nan=False,
            default=float,
        )
    print(f"\nWrote CSV and JSON to {out}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="ward", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("download", help="download SEC 13F data set zips")
    d.add_argument("--since", type=int, default=2019, help="first year to fetch (default 2019)")
    d.set_defaults(fn=cmd_download)

    s = sub.add_parser("sample", help="write small sample data to try the pipeline offline")
    s.set_defaults(fn=cmd_sample)

    mk = sub.add_parser("market", help="fetch tickers, prices and shares outstanding")
    mk.add_argument("--since", type=int, default=2019, help="first year of prices (default 2019)")
    mk.add_argument("--min-holders", type=int, default=2)
    mk.add_argument("--skip-figi", action="store_true", help="skip CUSIP to ticker mapping")
    mk.add_argument("--skip-prices", action="store_true", help="skip Yahoo price download")
    mk.add_argument("--skip-shares", action="store_true", help="skip SEC shares outstanding")
    mk.add_argument("--refresh-prices", action="store_true", help="download prices again for every ticker")
    mk.set_defaults(fn=cmd_market)

    l = sub.add_parser("load", help="load zips into DuckDB and build clean holdings")
    l.add_argument("--force", action="store_true", help="reload files already loaded")
    l.set_defaults(fn=cmd_load)

    b = sub.add_parser("build", help="compute crowding metrics for the fund universe")
    b.add_argument("--universe", default="data/universe.csv")
    b.add_argument("--min-holders", type=int, default=2)
    b.set_defaults(fn=cmd_build)

    r = sub.add_parser("report", help="print and export results for one quarter")
    r.add_argument("--period", help="quarter end date, e.g. 2026-06-30 (default: latest)")
    r.add_argument("--top", type=int, default=25)
    r.set_defaults(fn=cmd_report)

    args = p.parse_args(argv)
    args.fn(args, config.default_paths())


if __name__ == "__main__":
    main()
