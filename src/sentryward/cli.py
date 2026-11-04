"""Sentry Ward command line: ward sample | download | load | build | report."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import duckdb
import pandas as pd

from . import clean, config, dashboard, market, metrics, sec13f, stress


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
    try:
        out = market.run_all(con, start=f"{args.since - 1}-09-01", skip=skip, refresh_prices=args.refresh_prices)
    except RuntimeError as e:
        raise SystemExit(f"Error: {e}")
    m = out["market"]
    print(f"\nMarket table: {m['rows']:,} stock-quarters, {m['priced']:,} with prices, "
          f"{m['matched']:,} matching the 13F price, {m['with_shares']:,} also with shares outstanding.")
    cov = market.coverage(con)
    print(f"Latest quarter: {cov['value_priced_share']:.1%} of the funds' stock value has usable prices, "
          f"{cov['value_with_shares_share']:.1%} also has shares outstanding.")
    metrics.create_macros(con)
    metrics.build_stock_crowding(con, min_holders=args.min_holders)
    print("Crowding scores updated with ownership and days to exit. Next: ward report")


def cmd_live(args, paths):
    import webbrowser

    from . import live, realtime, server

    con = _connect(paths)
    for t in ("stock_crowding", "market", "prices"):
        if not metrics._has_table(con, t):
            raise SystemExit("Run `ward build` and `ward market` first.")
    if not stress.ensure_benchmark(con):
        raise SystemExit(f"Could not download {stress.BENCHMARK} prices, which are needed for beta.")
    try:
        baskets = live.build_baskets(con, top=args.top)
    except RuntimeError as e:
        raise SystemExit(f"Error: {e}")
    hist = live.history(con, baskets)
    cs, ps = live.basket_symbols(baskets)
    daily = live.db_daily(con, cs + ps + [stress.BENCHMARK])
    html = dashboard.render(dashboard.gather(con, paths.out))
    con.close()  # release the database so other commands can run while live mode is on

    interval = max(60, args.interval)
    prices = news = None
    keys = realtime.alpaca_keys() if args.source != "yahoo" else None
    if args.source == "alpaca" and keys is None:
        raise SystemExit("Set APCA_API_KEY_ID and APCA_API_SECRET_KEY for real-time prices (free at alpaca.markets).")
    if keys:
        client = realtime.AlpacaClient(*keys)
        tracked = cs + ps + [stress.BENCHMARK]
        prices = realtime.PriceFeed(tracked, client.snapshots, every=max(1.0, args.tick))
        news = realtime.NewsFeed(tracked, client, anthropic_key=os.environ.get("ANTHROPIC_API_KEY", "").strip() or None)
    monitor = live.LiveMonitor(baskets, hist, interval=interval, daily=daily, daily_fetcher=live.yahoo_daily,
                               prices=prices, news=news)
    for feed in (prices, news):
        if feed is not None:
            feed.start()
    monitor.start()
    db_path = str(paths.db)
    app = server.create_app(monitor, live.meta(baskets, hist), html,
                            connect=lambda: duckdb.connect(db_path, read_only=True))
    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}/"
    sig = hist["sigma"]
    print(f"Live mode: {len(baskets['crowded'])} crowded stocks vs {len(baskets['peers'])} matched peers "
          f"(holdings {baskets['period']}).")
    print(f"Normal daily spread: {100 * sig:.2f}% (std over {hist['days']} days); alert at z <= {live.ALERT_Z}."
          if sig else "Not enough price history to size alerts.")
    if prices is not None:
        print(f"Real-time prices every {prices.every:g} s from Alpaca (IEX exchange), during market hours; "
              f"charts filled in from Yahoo every {interval} s.")
        print("Live news from Alpaca" + (", with summaries from Claude." if news.anthropic_key else
                                          ". Set ANTHROPIC_API_KEY for summaries of items without one."))
    else:
        print(f"Quotes every {interval} s from Yahoo Finance (delayed about 15 minutes). "
              "Set APCA_API_KEY_ID and APCA_API_SECRET_KEY for real-time prices and news.")
    print(f"Open {url}   ·   full dashboard at {url}dashboard   ·   Ctrl+C to stop")
    if not args.no_browser:
        webbrowser.open(url)
    app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)


def cmd_dashboard(args, paths):
    con = _connect(paths)
    try:
        path = dashboard.write(con, paths.out, Path(args.site) if args.site else None)
    except RuntimeError as e:
        raise SystemExit(f"Error: {e}")
    print(f"Wrote {path.resolve()}")
    if args.site:
        print("Commit and push it; GitHub Pages serves it once Pages is set to this folder.")
    else:
        print("Open it in a browser. It is one self-contained file you can share.")


def _pct(x):
    return "" if x is None or pd.isna(x) else f"{100 * x:+.1f}%"


def cmd_stress(args, paths):
    con = _connect(paths)
    for t in ("stock_crowding", "market", "prices"):
        if not metrics._has_table(con, t):
            raise SystemExit("Run `ward build` and `ward market` first.")
    if not stress.ensure_benchmark(con):
        raise SystemExit(f"Could not download {stress.BENCHMARK} prices, which are needed for beta.")
    which = "public holdings (45+ days old)" if args.public else "last quarter-end holdings before each event"
    print(f"Running the pre-registered stress test (PREREGISTRATION.md) with {which}.")
    print(f"Bootstrap: {args.reps:,} resamples, seed {stress.BOOT_SEED}. This can take a few minutes.\n")
    res = stress.run(con, public=args.public, reps=args.reps)

    skipped = res["events"][res["events"]["status"] != "ok"]
    for _, r in skipped.iterrows():
        print(f"Skipped {r['event']}: {r['status']}")

    out = paths.out
    suffix = "_public" if args.public else ""
    payload = {"holdings": which, "reps": args.reps, "seed": stress.BOOT_SEED}
    for test in ("H1 crowded", "H2 crowded and hard to exit"):
        pe, sm = res[test]["per_event"], res[test]["summary"]
        print(f"\n{test}: crowded minus matched comparison (size x beta), event return")
        if not len(pe):
            print("  (no events with both groups)")
            continue
        table = pd.DataFrame({
            "event": pe["event"],
            "holdings": pe["formation"].astype(str),
            "stocks": pe["n_stocks"],
            "crowded": pe["n_treated"],
            "crowded_ret": pe["treated_ret"].map(_pct),
            "matched_ret": pe["control_ret_matched"].map(_pct),
            "difference": pe["diff"].map(_pct),
            "95% interval": [f"{_pct(a)} to {_pct(b)}" for a, b in zip(pe["ci_low"], pe["ci_high"])],
            "unmatched": pe["raw_diff"].map(_pct),
            "regression": pe["regression_coef"].map(_pct),
        })
        print(table.to_string(index=False))
        print(f"  Average over {sm['events']} events: {_pct(sm['avg_diff'])} "
              f"(95% interval {_pct(sm['ci_low'])} to {_pct(sm['ci_high'])}); "
              f"negative in {sm['events_negative']} of {sm['events']} events.")
        key = "h1" if test.startswith("H1") else "h2"
        pe.to_csv(out / f"stress_{key}{suffix}.csv", index=False)
        payload[key] = {"summary": sm, "per_event": _records(pe.astype({"formation": str, "start": str, "end": str}))}

    oq = res["ordinary_quarters"]
    if len(oq):
        d = oq["diff"]
        print(f"\nOrdinary quarters (H1, quarter end to next quarter end, {len(oq)} quarters): "
              f"average {_pct(d.mean())}, typical spread (std) {100 * d.std():.1f} points, "
              f"range {_pct(d.min())} to {_pct(d.max())}.")
        oq.to_csv(out / f"stress_ordinary_quarters{suffix}.csv", index=False)
        payload["ordinary_quarters"] = _records(oq.astype({"from": str, "to": str}))
    with open(out / f"stress{suffix}.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, allow_nan=False, default=float)
    print(f"\nWrote CSV and JSON to {out}")
    print("Unmatched = plain crowded minus everything else. Regression = crowded effect after beta,"
          " size and 12-month momentum.")


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
    with open(out / f"dashboard_{period}.json", "w", encoding="utf-8") as fh:
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

    lv = sub.add_parser("live", help="serve a live view of crowded stocks vs matched peers")
    lv.add_argument("--port", type=int, default=8765)
    lv.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 inside Docker")
    lv.add_argument("--interval", type=int, default=120, help="seconds between quote updates (min 60)")
    lv.add_argument("--top", type=int, default=30, help="size of the crowded basket")
    lv.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    lv.add_argument("--source", choices=["auto", "alpaca", "yahoo"], default="auto",
                    help="auto: Alpaca real time when its keys are set, otherwise Yahoo delayed")
    lv.add_argument("--tick", type=float, default=1.0, help="seconds between real-time price updates (min 1)")
    lv.set_defaults(fn=cmd_live)

    db = sub.add_parser("dashboard", help="write a self-contained HTML dashboard")
    db.add_argument("--site", metavar="DIR",
                    help="write DIR/index.html for a website instead, e.g. --site docs for GitHub Pages")
    db.set_defaults(fn=cmd_dashboard)

    st = sub.add_parser("stress", help="run the pre-registered stress test (see PREREGISTRATION.md)")
    st.add_argument("--public", action="store_true",
                    help="use only holdings public 45+ days before each event")
    st.add_argument("--reps", type=int, default=stress.BOOT_REPS, help="bootstrap resamples (default 2000)")
    st.set_defaults(fn=cmd_stress)

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
