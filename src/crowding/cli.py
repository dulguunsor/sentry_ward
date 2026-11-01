"""Command line: crowding download | load | build | report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb

from . import clean, config, metrics, sec13f


def _connect(paths: config.Paths) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(paths.db))


def cmd_download(args, paths):
    files = sec13f.download(paths, since_year=args.since)
    print(f"{len(files)} data set files in {paths.raw}")


def cmd_sample(args, paths):
    from .sample import write_sample

    write_sample(paths.raw)
    print(f"Wrote 2 small sample data sets to {paths.raw}. Next: crowding load")


def cmd_load(args, paths):
    con = _connect(paths)
    zips = list(paths.raw.glob("*_form13f.zip"))
    if not zips:
        raise SystemExit(f"No *_form13f.zip files in {paths.raw}. Run `crowding download` first.")
    new = sec13f.load_all(con, zips, force=args.force)
    print(f"Loaded {len(new)} new files ({len(zips) - len(new)} already loaded).")
    counts = clean.build_all(con)
    for table, n in counts.items():
        print(f"  {table:<17} {n:>12,}")


def cmd_build(args, paths):
    con = _connect(paths)
    matched = metrics.build_all(con, Path(args.universe), min_holders=args.min_holders)
    print(f"Universe: {matched.fund.nunique()} funds from {len(matched)} filer CIKs")
    print(matched[["fund", "cik", "manager_name"]].to_string(index=False))
    missing = metrics.unmatched_patterns(con, Path(args.universe))
    if missing:
        print("\nNo filer matched these names (check the pattern in the universe file):")
        print("  " + ", ".join(missing))


def cmd_report(args, paths):
    con = _connect(paths)
    period = args.period or con.execute("SELECT max(period)::VARCHAR FROM crowding_index").fetchall()[0][0]
    top = con.execute(
        """
        SELECT cusip, issuer, n_holders, n_funds, round(holder_share, 3) AS holder_share,
               round(sum_weight, 4) AS sum_weight, round(total_value_usd / 1e9, 2) AS value_bn,
               round(crowding_score, 1) AS score, round(score_change, 1) AS change
        FROM stock_crowding WHERE period = ?::DATE
        ORDER BY crowding_score DESC LIMIT ?
        """,
        [period, args.top],
    ).df()
    idx = con.execute("SELECT * FROM crowding_index ORDER BY period").df()
    matrix = metrics.overlap_matrix(con, period)

    print(f"\nQuarter ending {period}")
    print(top.to_string(index=False))
    print("\nCrowding index by quarter (average pairwise overlap):")
    print(idx.tail(8).to_string(index=False))

    out = paths.out
    top.to_csv(out / f"top_crowded_{period}.csv", index=False)
    idx.to_csv(out / "crowding_index.csv", index=False)
    matrix.to_csv(out / f"overlap_matrix_{period}.csv")
    with open(out / f"dashboard_{period}.json", "w") as fh:
        json.dump(
            {
                "period": period,
                "top_crowded": top.to_dict(orient="records"),
                "crowding_index": idx.assign(period=idx.period.astype(str)).to_dict(orient="records"),
                "overlap_matrix": {"funds": list(matrix.index), "values": matrix.round(4).values.tolist()},
            },
            fh,
            indent=2,
            default=float,
        )
    print(f"\nWrote CSV and JSON to {out}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="crowding", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("download", help="download SEC 13F data set zips")
    d.add_argument("--since", type=int, default=2019, help="first year to fetch (default 2019)")
    d.set_defaults(fn=cmd_download)

    s = sub.add_parser("sample", help="write small sample data to try the pipeline offline")
    s.set_defaults(fn=cmd_sample)

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
