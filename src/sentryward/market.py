"""Market data for the stocks the universe funds hold.

Sources (all free):
  OpenFIGI          CUSIP -> ticker              https://www.openfigi.com/api
  Yahoo Finance     daily prices, volume, splits  via the yfinance library
  SEC XBRL frames   shares outstanding            https://www.sec.gov/edgar/sec-api-documentation

Everything is cached in DuckDB, so an interrupted run picks up where it stopped.

Two checks keep the numbers honest:
  * Yahoo adjusts past prices and volumes for later stock splits; 13F share counts are
    as reported at the time. Splits are undone so both use the same shares.
  * Tickers get reused after companies disappear, so a CUSIP can map to the wrong
    company. Each stock's quarter-end close is compared with the median price per share
    13F filers reported. Pairs off by more than 25% are not used.
"""

from __future__ import annotations

import os
import time
from datetime import date
from typing import Callable, Iterable

import duckdb
import pandas as pd

from . import config

OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
OPENFIGI_KEY_ENV = "OPENFIGI_API_KEY"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_FRAMES_URL = "https://data.sec.gov/api/xbrl/frames/{taxonomy}/{concept}/shares/{frame}.json"

ADV_DAYS = 20          # average daily volume window
VOL_DAYS = 60          # volatility window
PRICE_MATCH = (0.8, 1.25)
MAX_PRICE_ATTEMPTS = 3   # tickers that return no data are retried on later runs, up to this many times

# Shares outstanding, in order of preference.
SHARE_CONCEPTS = [
    ("dei", "EntityCommonStockSharesOutstanding", True),   # cover page, instant
    ("us-gaap", "WeightedAverageNumberOfSharesOutstandingBasic", False),  # quarterly
]

Log = Callable[[str], None]


def yahoo_symbol(ticker: str) -> str:
    """OpenFIGI writes share classes as BRK/B; Yahoo and SEC use BRK-B."""
    return ticker.strip().upper().replace("/", "-").replace(" ", "-")


def _session(session):
    if session is not None:
        return session
    import requests

    return requests.Session()


def _insert_rows(con: duckdb.DuckDBPyConnection, table: str, columns: list[str], rows: list[tuple],
                 replace: bool = True) -> None:
    """Insert many rows at once. DuckDB's executemany inserts row by row, which is very
    slow for hundreds of thousands of rows."""
    if not rows:
        return
    df = pd.DataFrame(rows, columns=columns)
    con.register("_rows", df)
    verb = "INSERT OR REPLACE INTO" if replace else "INSERT INTO"
    con.execute(f"{verb} {table} ({', '.join(columns)}) SELECT {', '.join(columns)} FROM _rows").fetchall()
    con.unregister("_rows")


def _ensure_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS cusip_map (
            cusip VARCHAR PRIMARY KEY, ticker VARCHAR, symbol VARCHAR, figi VARCHAR,
            security_type VARCHAR, name VARCHAR, status VARCHAR, mapped_at TIMESTAMP);
        CREATE TABLE IF NOT EXISTS prices (
            symbol VARCHAR, date DATE, close DOUBLE, adj_close DOUBLE, volume DOUBLE, split DOUBLE,
            PRIMARY KEY (symbol, date));
        CREATE TABLE IF NOT EXISTS price_status (symbol VARCHAR PRIMARY KEY, n_rows INTEGER, fetched_at TIMESTAMP);
        ALTER TABLE price_status ADD COLUMN IF NOT EXISTS attempts INTEGER DEFAULT 1;
        CREATE TABLE IF NOT EXISTS sec_tickers (cik VARCHAR, symbol VARCHAR, title VARCHAR);
        CREATE TABLE IF NOT EXISTS shares_out (
            cik VARCHAR, period DATE, shares DOUBLE, source VARCHAR, PRIMARY KEY (cik, period, source));
        CREATE TABLE IF NOT EXISTS shares_status (period DATE, source VARCHAR, n_rows INTEGER,
            PRIMARY KEY (period, source));
        """
    ).fetchall()


# ------------------------------------------------------------------ 1. CUSIP -> ticker


def cusips_to_map(con: duckdb.DuckDBPyConnection) -> list[str]:
    _ensure_tables(con)
    rows = con.execute(
        """
        SELECT DISTINCT cusip FROM fund_positions
        WHERE NOT is_etf AND cusip NOT IN (SELECT cusip FROM cusip_map)
        ORDER BY cusip
        """
    ).fetchall()
    return [r[0] for r in rows]


US_EXCH_CODES = ("US", "UN", "UW", "UQ", "UA", "UR", "UP", "UV", "UF")


def _is_equity(d: dict) -> bool:
    return ("common" in (d.get("securityType") or "").lower()
            or (d.get("securityType2") or "").lower() in ("common stock", "depositary receipt"))


def _parse_figi(cusip: str, result: dict, us_only: bool = False, miss: str = "not_found") -> tuple:
    data = result.get("data") or []
    if us_only:
        # Without an exchange filter OpenFIGI returns every listing worldwide; keep US
        # ones, the composite "US" listing first, since Yahoo uses US tickers.
        data = sorted((d for d in data if d.get("exchCode") in US_EXCH_CODES),
                      key=lambda d: US_EXCH_CODES.index(d["exchCode"]))
    if not data:
        return (cusip, None, None, None, None, None, miss)
    pick = next((d for d in data if _is_equity(d)), data[0])
    ticker = pick.get("ticker")
    return (
        cusip, ticker, yahoo_symbol(ticker) if ticker else None, pick.get("figi"),
        pick.get("securityType"), pick.get("name"), "ok" if ticker else "no_ticker",
    )


def _first_pass_job(cusip: str) -> dict:
    return {"idType": "ID_CUSIP", "idValue": cusip, "exchCode": "US"}


def _retry_job(cusip: str) -> dict:
    """Codes starting with a letter are CINS numbers (companies incorporated outside
    the US, such as ASML or Spotify); OpenFIGI wants those as ID_CINS. No exchange
    filter, so listings OpenFIGI files under another code are also returned."""
    id_type = "ID_CINS" if cusip[:1].isalpha() else "ID_CUSIP"
    return {"idType": id_type, "idValue": cusip}


def _figi_pass(con, s, todo, make_job, parse, key, headers, log, sleep, label) -> int:
    size, pause = (100, 0.25) if key else (10, 2.6)
    found = 0
    n_batches = (len(todo) + size - 1) // size
    log(f"{label}: {len(todo):,} CUSIPs in {n_batches:,} requests"
        + ("" if key else f" (no {OPENFIGI_KEY_ENV} set: about {n_batches * pause / 60:.0f} minutes)"))
    for b, i in enumerate(range(0, len(todo), size), start=1):
        chunk = todo[i:i + size]
        jobs = [make_job(c) for c in chunk]
        for attempt in range(6):
            r = s.post(OPENFIGI_URL, json=jobs, headers=headers, timeout=60)
            if r.status_code == 429:
                sleep(60 if not key else 7)
                continue
            if r.status_code in (401, 403):
                raise RuntimeError(
                    f"OpenFIGI rejected the API key in {OPENFIGI_KEY_ENV}. Copy the key again from "
                    "your OpenFIGI account page, or remove the variable to run without a key."
                )
            r.raise_for_status()
            break
        else:
            raise RuntimeError("OpenFIGI kept refusing requests (HTTP 429). Try again later or set an API key.")
        rows = [parse(c, res) for c, res in zip(chunk, r.json())]
        found += sum(1 for row in rows if row[-1] == "ok")
        _insert_rows(con, "cusip_map",
                     ["cusip", "ticker", "symbol", "figi", "security_type", "name", "status"], rows)
        if b % 50 == 0 or b == n_batches:
            log(f"  {b:,}/{n_batches:,} requests, {found:,} tickers found")
        if b < n_batches:
            sleep(pause)
    return found


def map_cusips(
    con: duckdb.DuckDBPyConnection,
    session=None,
    api_key: str | None = None,
    log: Log = print,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, int]:
    """Map unmapped CUSIPs, then retry the ones OpenFIGI did not find (once each).

    Without an API key OpenFIGI allows about 25 requests a minute of 10 CUSIPs each;
    a free key raises that to 100 CUSIPs per request."""
    todo = cusips_to_map(con)
    retry = [r[0] for r in con.execute(
        "SELECT cusip FROM cusip_map WHERE status = 'not_found' ORDER BY cusip"
    ).fetchall()]
    if not todo and not retry:
        return {"requested": 0, "found": 0, "retried": 0, "found_on_retry": 0}
    s = _session(session)
    key = api_key or os.environ.get(OPENFIGI_KEY_ENV)
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-OPENFIGI-APIKEY"] = key
    args = (key, headers, log, sleep)

    found = _figi_pass(con, s, todo, _first_pass_job, _parse_figi, *args, "Mapping") if todo else 0
    # Retry everything still not found, including what this run just missed.
    retry = [r[0] for r in con.execute(
        "SELECT cusip FROM cusip_map WHERE status = 'not_found' ORDER BY cusip"
    ).fetchall()]
    found_retry = 0
    if retry:
        def parse_retry(c, res):
            return _parse_figi(c, res, us_only=True, miss="not_found_retried")
        found_retry = _figi_pass(con, s, retry, _retry_job, parse_retry, *args,
                                 "Retrying unmatched (CINS ids, all exchanges)")
    return {"requested": len(todo), "found": found, "retried": len(retry), "found_on_retry": found_retry}


# ------------------------------------------------------------------ 2. prices


def yahoo_download(symbols: list[str], start: str) -> pd.DataFrame:
    """Daily close, adjusted close, volume and splits, in long format."""
    import warnings

    import yfinance as yf

    with warnings.catch_warnings():
        # yfinance triggers pandas deprecation warnings on every call; they are harmless.
        warnings.simplefilter("ignore")
        raw = yf.download(
            symbols, start=start, auto_adjust=False, actions=True, group_by="ticker",
            threads=True, progress=False,
        )
    out = []
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["symbol", "date", "close", "adj_close", "volume", "split"])
    multi = isinstance(raw.columns, pd.MultiIndex)
    for sym in symbols:
        if multi:
            if sym not in raw.columns.get_level_values(0):
                continue
            d = raw[sym]
        else:
            d = raw
        if "Close" not in d:
            continue
        frame = pd.DataFrame({
            "symbol": sym,
            "date": pd.to_datetime(d.index).date,
            "close": d["Close"].to_numpy(),
            "adj_close": (d["Adj Close"] if "Adj Close" in d else d["Close"]).to_numpy(),
            "volume": d["Volume"].to_numpy(),
            "split": d["Stock Splits"].to_numpy() if "Stock Splits" in d else 0.0,
        }).dropna(subset=["close"])
        out.append(frame)
    if not out:
        return pd.DataFrame(columns=["symbol", "date", "close", "adj_close", "volume", "split"])
    return pd.concat(out, ignore_index=True)


def fetch_prices(
    con: duckdb.DuckDBPyConnection,
    start: str = "2018-09-01",
    downloader: Callable[[list[str], str], pd.DataFrame] | None = None,
    chunk: int = 50,
    refresh: bool = False,
    log: Log = print,
    sleep: Callable[[float], None] = time.sleep,
    pause: float = 2.0,
) -> dict[str, int]:
    """Download prices for tickers not fetched yet. Tickers that came back empty
    (delisted, or Yahoo was rate limiting) are retried on later runs, up to
    MAX_PRICE_ATTEMPTS times. A batch that comes back completely empty is treated as
    rate limiting: wait a minute and try it once more."""
    _ensure_tables(con)
    dl = downloader or yahoo_download
    where = "" if refresh else f"""AND symbol NOT IN (
        SELECT symbol FROM price_status WHERE n_rows > 0 OR attempts >= {MAX_PRICE_ATTEMPTS})"""
    todo = [r[0] for r in con.execute(
        f"SELECT DISTINCT symbol FROM cusip_map WHERE symbol IS NOT NULL {where} ORDER BY symbol"
    ).fetchall()]
    if not todo:
        return {"requested": 0, "with_data": 0}
    log(f"Downloading daily prices for {len(todo):,} tickers from {start}")
    with_data = 0
    n_chunks = (len(todo) + chunk - 1) // chunk
    for b, i in enumerate(range(0, len(todo), chunk), start=1):
        syms = todo[i:i + chunk]
        df = dl(syms, start)
        if not len(df) and len(syms) > 1:
            log(f"  batch {b} came back empty, probably rate limited: waiting 60 seconds")
            sleep(60)
            df = dl(syms, start)
        if len(df):
            df = df.assign(split=df["split"].fillna(0.0), volume=df["volume"].fillna(0.0))
            con.register("incoming_prices", df)
            con.execute(
                """
                INSERT OR REPLACE INTO prices
                SELECT symbol, CAST(date AS DATE), close, adj_close, volume, split FROM incoming_prices
                """
            ).fetchall()
            con.unregister("incoming_prices")
        counts = df.groupby("symbol").size().to_dict() if len(df) else {}
        with_data += sum(1 for s in syms if counts.get(s, 0) > 0)
        con.executemany(
            """
            INSERT INTO price_status VALUES (?, ?, now(), 1)
            ON CONFLICT (symbol) DO UPDATE SET
                n_rows = excluded.n_rows, fetched_at = excluded.fetched_at,
                attempts = price_status.attempts + 1
            """,
            [(s, int(counts.get(s, 0))) for s in syms],
        )
        if b % 10 == 0 or b == n_chunks:
            log(f"  {b:,}/{n_chunks:,} batches, {with_data:,} tickers with data")
        if b < n_chunks:
            sleep(pause)
    missing = con.execute(
        f"SELECT count(*) FROM price_status WHERE n_rows = 0 AND attempts < {MAX_PRICE_ATTEMPTS}"
    ).fetchall()[0][0]
    if missing:
        log(f"  {missing:,} tickers returned no data; run `ward market` again later to retry them "
            f"(up to {MAX_PRICE_ATTEMPTS} tries each; delisted stocks never return data).")
    return {"requested": len(todo), "with_data": with_data}


# ------------------------------------------------------------------ 3. shares outstanding


def _frame_name(period: date, instant: bool) -> str:
    q = (period.month - 1) // 3 + 1
    return f"CY{period.year}Q{q}" + ("I" if instant else "")


def fetch_shares(
    con: duckdb.DuckDBPyConnection,
    session=None,
    log: Log = print,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, int]:
    _ensure_tables(con)
    s = _session(session)
    headers = {"User-Agent": config.user_agent()}

    r = s.get(SEC_TICKERS_URL, headers=headers, timeout=60)
    r.raise_for_status()
    tick = [(str(v["cik_str"]), yahoo_symbol(v["ticker"]), v.get("title")) for v in r.json().values()]
    con.execute("DELETE FROM sec_tickers").fetchall()
    _insert_rows(con, "sec_tickers", ["cik", "symbol", "title"], tick, replace=False)

    periods = [r[0] for r in con.execute("SELECT DISTINCT period FROM fund_positions ORDER BY period").fetchall()]
    done = {(r[0], r[1]) for r in con.execute("SELECT period, source FROM shares_status").fetchall()}
    added = 0
    for period in periods:
        for taxonomy, concept, instant in SHARE_CONCEPTS:
            source = taxonomy
            if (period, source) in done:
                continue
            url = SEC_FRAMES_URL.format(taxonomy=taxonomy, concept=concept, frame=_frame_name(period, instant))
            sleep(0.15)  # SEC allows 10 requests a second
            resp = s.get(url, headers=headers, timeout=60)
            if resp.status_code == 404:
                rows = []
            else:
                resp.raise_for_status()
                rows = [(str(d["cik"]), period, float(d["val"]), source) for d in resp.json().get("data", [])]
            if rows:
                _insert_rows(con, "shares_out", ["cik", "period", "shares", "source"], rows)
            con.execute("INSERT OR REPLACE INTO shares_status VALUES (?, ?, ?)", [period, source, len(rows)]).fetchall()
            added += len(rows)
    log(f"Shares outstanding: {added:,} new company-quarter values, {len(tick):,} SEC tickers")
    return {"rows": added, "tickers": len(tick)}


# ------------------------------------------------------------------ 4. market table


def build_market(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """One row per (cusip, quarter): raw quarter-end close, average daily volume and
    volatility, shares outstanding, and whether the close matches the 13F price."""
    _ensure_tables(con)
    lo, hi = PRICE_MATCH
    con.execute(
        f"""
        CREATE OR REPLACE TABLE market AS
        WITH px AS (
            -- product of all split factors after each day; undoes Yahoo's split adjustment
            SELECT *, exp(coalesce(sum(ln(CASE WHEN split > 0 THEN split END)) OVER (
                        PARTITION BY symbol ORDER BY date DESC
                        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0)) AS later_splits
            FROM prices
        ),
        raw AS (
            SELECT symbol, date,
                   close * later_splits  AS raw_close,
                   volume / later_splits AS raw_volume,
                   ln(adj_close / lag(adj_close) OVER (PARTITION BY symbol ORDER BY date)) AS ret
            FROM px
        ),
        periods AS (SELECT DISTINCT period FROM fund_positions),
        win AS (
            SELECT r.*, p.period,
                   row_number() OVER (PARTITION BY r.symbol, p.period ORDER BY r.date DESC) AS k
            FROM raw r JOIN periods p
              ON r.date <= p.period AND r.date > p.period - INTERVAL 120 DAY
        ),
        agg AS (
            SELECT symbol, period,
                   max(raw_close)           FILTER (WHERE k = 1)            AS close,
                   max(date)                FILTER (WHERE k = 1)            AS close_date,
                   avg(raw_volume)          FILTER (WHERE k <= {ADV_DAYS})  AS adv_shares,
                   stddev_samp(ret)         FILTER (WHERE k <= {VOL_DAYS})  AS daily_vol
            FROM win GROUP BY ALL
        ),
        implied AS (
            SELECT period, cusip, median(value_usd / nullif(shares, 0)) AS px_13f
            FROM holdings_ok WHERE cusip IN (SELECT cusip FROM cusip_map WHERE symbol IS NOT NULL)
            GROUP BY ALL
        ),
        sh AS (
            SELECT t.symbol, s.period,
                   arg_min(s.shares, CASE s.source WHEN 'dei' THEN 0 ELSE 1 END) AS shares_outstanding
            FROM shares_out s JOIN (SELECT DISTINCT cik, symbol FROM sec_tickers) t USING (cik)
            GROUP BY ALL
        )
        SELECT f.cusip, i.period, f.ticker, f.symbol,
               a.close, a.close_date, i.px_13f, a.close / nullif(i.px_13f, 0) AS price_ratio,
               coalesce(a.close / nullif(i.px_13f, 0) BETWEEN {lo} AND {hi}, false) AS price_match,
               a.adv_shares, a.daily_vol, sh.shares_outstanding
        FROM cusip_map f
        JOIN implied i ON i.cusip = f.cusip
        LEFT JOIN agg a ON a.symbol = f.symbol AND a.period = i.period
        LEFT JOIN sh ON sh.symbol = f.symbol AND sh.period = i.period
        WHERE f.symbol IS NOT NULL
        """
    ).fetchall()
    total, priced, matched, with_shares = con.execute(
        """SELECT count(*), count(close), count(*) FILTER (WHERE price_match),
                  count(*) FILTER (WHERE price_match AND shares_outstanding IS NOT NULL)
           FROM market"""
    ).fetchall()[0]
    return {"rows": total, "priced": priced, "matched": matched, "with_shares": with_shares}


def coverage(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """How much of the universe's stock value has usable market data, latest quarter."""
    row = con.execute(
        """
        SELECT sum(f.value_usd),
               sum(f.value_usd) FILTER (WHERE m.price_match),
               sum(f.value_usd) FILTER (WHERE m.price_match AND m.shares_outstanding IS NOT NULL)
        FROM fund_positions f
        LEFT JOIN market m ON m.cusip = f.cusip AND m.period = f.period
        WHERE f.period = (SELECT max(period) FROM fund_positions) AND NOT f.is_etf
        """
    ).fetchall()[0]
    total = row[0] or 0
    return {
        "value_priced_share": (row[1] or 0) / total if total else 0.0,
        "value_with_shares_share": (row[2] or 0) / total if total else 0.0,
    }


def run_all(
    con: duckdb.DuckDBPyConnection,
    start: str,
    skip: Iterable[str] = (),
    refresh_prices: bool = False,
    log: Log = print,
) -> dict:
    skip = set(skip)
    out = {}
    if "figi" not in skip:
        out["figi"] = map_cusips(con, log=log)
    if "prices" not in skip:
        out["prices"] = fetch_prices(con, start=start, refresh=refresh_prices, log=log)
    if "shares" not in skip:
        out["shares"] = fetch_shares(con, log=log)
    out["market"] = build_market(con)
    return out
