"""Live monitor: the most crowded stocks against matched peers, through the trading day.

The holdings are quarterly, but prices move every minute. If funds start selling
their crowded positions together, the crowded basket falls behind comparable stocks
before any filing shows it. This module tracks that gap.

  crowded basket  the LIVE_TOP stocks with the highest crowding score (latest quarter)
  peer basket     for each crowded stock, PEERS_PER stocks from the same size x beta
                  bucket (same matching as the stress test), closest in size
  spread          crowded basket return today - peer basket return today
  z               spread / the standard deviation of the daily spread over the last
                  HIST_DAYS trading days; an alert when z <= ALERT_Z

Quotes come from Yahoo Finance and are delayed about 15 minutes. The chart can show
any range in RANGES, from the last hour (1-minute bars) to the last year (daily closes).
"""

from __future__ import annotations

import math
import threading
import time
import warnings
from datetime import datetime, timezone
from typing import Callable

import duckdb
import numpy as np
import pandas as pd

from . import stress

LIVE_TOP = 30
PEERS_PER = 2
HIST_DAYS = 250
HIST_SHOWN = 60
ALERT_Z = -2.0
MARKET_TZ = "America/New_York"
DAILY_EVERY = 900  # seconds between daily-close downloads


def clean(v):
    """Make a value JSON-safe: NaN and infinity become None, numpy and dates become plain types."""
    if isinstance(v, dict):
        return {k: clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [clean(x) for x in v]
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        v = v.item()
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, (pd.Timestamp, datetime)):
        return v.isoformat()
    if v is pd.NaT:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


# ------------------------------------------------------------------ baskets


def _names(con, period) -> dict:
    cols = {r[0] for r in con.execute("DESCRIBE stock_crowding").fetchall()}
    tick = "coalesce(ticker, cusip)" if "ticker" in cols else "cusip"
    iss = "issuer" if "issuer" in cols else "NULL"
    rows = con.execute(
        f"SELECT issuer_id, {tick}, {iss} FROM stock_crowding WHERE period = ?", [period]
    ).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def build_baskets(con: duckdb.DuckDBPyConnection, top: int = LIVE_TOP, peers_per: int = PEERS_PER) -> dict:
    period = con.execute("SELECT max(period) FROM stock_crowding").fetchall()[0][0]
    last_px = con.execute(
        f"SELECT max(date) FROM prices WHERE symbol <> '{stress.BENCHMARK}'"
    ).fetchall()[0][0]
    if period is None or last_px is None:
        raise RuntimeError("Run `ward build` and `ward market` first.")
    df = stress._sample(con, period, last_px, last_px)
    if len(df) < top * 3:
        raise RuntimeError(f"Only {len(df)} stocks have prices and beta for {period}; need more for matching.")
    df = stress.label_groups(df).sort_values(["crowding_score", "symbol"], ascending=[False, True])
    names = _names(con, period)
    crowded = df.head(top)
    pool = df.iloc[top:].copy()
    pool["log_size"] = np.log(pool["market_weight"])
    used: set[str] = set()
    out = []
    for _, c in crowded.iterrows():
        target = math.log(c["market_weight"])
        picks = []
        for cond in (pool["bucket"] == c["bucket"], pool["beta_q"] == c["beta_q"]):
            cand = pool[cond & ~pool["symbol"].isin(used)].copy()
            cand["dist"] = (cand["log_size"] - target).abs()
            for s in cand.sort_values(["dist", "symbol"])["symbol"]:
                if len(picks) == peers_per:
                    break
                picks.append(s)
                used.add(s)
            if len(picks) == peers_per:
                break
        ticker, issuer = names.get(c["issuer_id"], (c["symbol"], None))
        out.append({"symbol": c["symbol"], "ticker": ticker, "issuer": issuer,
                    "score": float(c["crowding_score"]), "beta": float(c["beta"]), "peers": picks})
    peer_rows = df[df["symbol"].isin(used)]
    peers = [{"symbol": r["symbol"], "ticker": names.get(r["issuer_id"], (r["symbol"],))[0],
              "beta": float(r["beta"])} for _, r in peer_rows.iterrows()]
    return {"period": str(period), "prices_through": str(last_px), "crowded": out, "peers": peers,
            "avg_beta_crowded": float(crowded["beta"].mean()),
            "avg_beta_peers": float(peer_rows["beta"].mean()) if len(peer_rows) else None}


def basket_symbols(baskets: dict) -> tuple[list[str], list[str]]:
    return [c["symbol"] for c in baskets["crowded"]], [p["symbol"] for p in baskets["peers"]]


# ------------------------------------------------------------------ history


def history(con: duckdb.DuckDBPyConnection, baskets: dict, days: int = HIST_DAYS, shown: int = HIST_SHOWN) -> dict:
    """Daily crowded-minus-peers spread over the last `days` sessions, and its spread."""
    cs, ps = basket_symbols(baskets)
    syms = cs + ps
    px = con.execute(
        f"SELECT symbol, date, adj_close FROM prices WHERE symbol IN ({','.join('?' * len(syms))}) "
        "AND date > (SELECT max(date) FROM prices) - INTERVAL 420 DAY",
        syms,
    ).df()
    wide = px.pivot_table(index="date", columns="symbol", values="adj_close").sort_index()
    rets = wide.pct_change(fill_method=None).iloc[1:].tail(days)
    crowd = rets[[s for s in cs if s in rets]].mean(axis=1)
    peer = rets[[s for s in ps if s in rets]].mean(axis=1)
    spread = (crowd - peer).dropna()
    sigma = float(spread.std()) if len(spread) > 20 else None
    tail = pd.DataFrame({"crowded": crowd, "peers": peer, "spread": crowd - peer}).dropna().tail(shown)
    series = [{"date": str(d)[:10], **{k: float(v) for k, v in r.items()}} for d, r in tail.iterrows()]
    return {"sigma": sigma, "days": int(len(spread)), "series": series}


# ------------------------------------------------------------------ quotes


def _yahoo(symbols: list[str], period: str, interval: str, adjust: bool) -> pd.DataFrame:
    import yfinance as yf

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        raw = yf.download(symbols, period=period, interval=interval, group_by="ticker", auto_adjust=adjust,
                          prepost=False, threads=True, progress=False)
    frames = []
    if raw is not None and not raw.empty:
        multi = isinstance(raw.columns, pd.MultiIndex)
        for s in symbols:
            if multi and s not in raw.columns.get_level_values(0):
                continue
            d = raw[s] if multi else raw
            if "Close" not in d:
                continue
            c = d["Close"].dropna()
            frames.append(pd.DataFrame({"symbol": s, "ts": c.index, "close": c.to_numpy()}))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["symbol", "ts", "close"])


def yahoo_intraday(symbols: list[str]) -> pd.DataFrame:
    """1-minute bars for the last week of sessions (Yahoo's limit for 1-minute data): symbol, ts, close."""
    return _yahoo(symbols, "7d", "1m", adjust=False)


def yahoo_daily(symbols: list[str]) -> pd.DataFrame:
    """Daily closes, adjusted for splits and dividends, for the last two years: symbol, date, close."""
    d = _yahoo(symbols, "2y", "1d", adjust=True)
    return pd.DataFrame({"symbol": d["symbol"], "date": pd.to_datetime(d["ts"]).dt.date, "close": d["close"]})


def db_daily(con: duckdb.DuckDBPyConnection, symbols: list[str], days: int = 400) -> pd.DataFrame:
    """Daily closes from the local database, used until the first Yahoo daily download arrives."""
    return con.execute(
        f"SELECT symbol, date, adj_close AS close FROM prices WHERE symbol IN ({','.join('?' * len(symbols))}) "
        f"AND date > (SELECT max(date) FROM prices) - INTERVAL {int(days)} DAY ORDER BY symbol, date",
        symbols,
    ).df()


# Chart ranges. Intraday ones use minute bars, the rest daily closes.
#   key: (kind, length, bar, label format)
RANGES = {
    "1H": ("minutes", 60, "1min", "%H:%M"),
    "3H": ("minutes", 180, "1min", "%H:%M"),
    "1D": ("session", 1, "5min", "%H:%M"),
    "5D": ("sessions", 5, "15min", "%a %H:%M"),
    "1M": ("daily", 21, None, "%b %d"),
    "3M": ("daily", 63, None, "%b %d"),
    "1Y": ("daily", 252, None, "%b %Y"),
}
BAR_LABEL = {"1min": "1-minute bars", "5min": "5-minute bars", "15min": "15-minute bars", None: "daily closes"}


def _last_close(rows: pd.DataFrame, key: str) -> pd.Series:
    return rows.sort_values(key).groupby("symbol")["close"].last()


def _window(rows: pd.DataFrame, key: str, base: pd.Series, bar: str | None, fmt: str, baskets: dict) -> dict | None:
    """Equal-weight basket returns from `base` through every bar in `rows`."""
    if rows.empty:
        return None
    r = rows.sort_values(key).copy()
    if bar:
        r[key] = r[key].dt.floor(bar)
    wide = r.groupby([key, "symbol"])["close"].last().unstack().sort_index().ffill()
    base = base.reindex(wide.columns).fillna(wide.bfill().iloc[0])  # no earlier close: start from the first one
    rets = wide / base - 1
    cs, ps = basket_symbols(baskets)
    cs_ok = [s for s in cs if s in rets and rets[s].notna().any()]
    ps_ok = [s for s in ps if s in rets and rets[s].notna().any()]
    if not cs_ok or not ps_ok:
        return None
    path = pd.DataFrame({"crowded": rets[cs_ok].mean(axis=1), "peers": rets[ps_ok].mean(axis=1)})
    path["spread"] = path["crowded"] - path["peers"]
    if stress.BENCHMARK in rets:
        path["spy"] = rets[stress.BENCHMARK]
    path = path.dropna(subset=["spread"])
    if path.empty:
        return None
    lastrow = rets.iloc[-1]
    tick = {p["symbol"]: p["ticker"] for p in baskets["peers"]}
    stocks = []
    for c in baskets["crowded"]:
        v = lastrow.get(c["symbol"])
        ret = None if v is None or pd.isna(v) else float(v)
        pr = [lastrow.get(p) for p in c["peers"]]
        pr = [float(x) for x in pr if x is not None and not pd.isna(x)]
        peer = float(np.mean(pr)) if pr else None
        stocks.append({"ticker": c["ticker"], "issuer": c["issuer"], "score": c["score"], "beta": c.get("beta"),
                       "peers": [{"ticker": tick.get(p, p), "ret": None if pd.isna(lastrow.get(p, np.nan))
                                  else float(lastrow.get(p))} for p in c["peers"]],
                       "ret": ret, "peers_ret": peer, "rel": None if ret is None or peer is None else ret - peer})
    last = path.iloc[-1]
    return {
        "bar": BAR_LABEL[bar],
        "crowded": float(last["crowded"]), "peers": float(last["peers"]), "spread": float(last["spread"]),
        "spy": float(last["spy"]) if "spy" in path and not pd.isna(last.get("spy")) else None,
        "coverage": {"crowded": len(cs_ok), "crowded_total": len(cs), "peers": len(ps_ok), "peers_total": len(ps)},
        "path": [{"t": pd.Timestamp(t).strftime(fmt),
                  **{k: round(float(v), 6) for k, v in row.items() if not pd.isna(v)}}
                 for t, row in path.iterrows()],
        "stocks": stocks,
    }


def _intraday_ranges(b: pd.DataFrame, baskets: dict) -> dict:
    sessions = sorted(b["date"].unique())
    today = sessions[-1]
    out = {}
    for key, (kind, n, bar, fmt) in RANGES.items():
        if kind == "minutes":
            last_ts = b.loc[b["date"] == today, "ts"].max()
            start = last_ts - pd.Timedelta(minutes=n)
            rows, earlier = b[(b["date"] == today) & (b["ts"] > start)], b[b["ts"] <= start]
            since = f"since {start.strftime('%H:%M')}" if start >= b.loc[b["date"] == today, "ts"].min() \
                else "since yesterday's close"
        elif kind == "session":
            rows, earlier, since = b[b["date"] == today], b[b["date"] < today], "since yesterday's close"
        elif kind == "sessions":
            first = sessions[-n] if len(sessions) >= n else sessions[0]
            rows, earlier = b[b["date"] >= first], b[b["date"] < first]
            since = f"since the close before {first:%b %d}" if not earlier.empty else f"since {first:%b %d}"
        else:
            continue
        w = _window(rows, "ts", _last_close(earlier, "ts"), bar, fmt, baskets)
        if w:
            out[key] = {**w, "since": since}
    return out


def _daily_ranges(daily: pd.DataFrame, live_close: pd.Series, session, baskets: dict) -> dict:
    """Daily ranges, with today's latest intraday price standing in for today's close."""
    d = daily[["symbol", "date", "close"]].copy()
    d["date"] = pd.to_datetime(d["date"])
    if session is not None and len(live_close):
        sd = pd.Timestamp(session)
        d = pd.concat([d[d["date"] != sd],
                       pd.DataFrame({"symbol": live_close.index, "date": sd, "close": live_close.to_numpy()})])
    dates = sorted(d["date"].unique())
    out = {}
    for key, (kind, n, bar, fmt) in RANGES.items():
        if kind != "daily" or len(dates) < 2:
            continue
        first = dates[-n] if len(dates) >= n + 1 else dates[1]
        rows, earlier = d[d["date"] >= first], d[d["date"] < first]
        w = _window(rows, "date", _last_close(earlier, "date"), None, fmt, baskets)
        if w:
            out[key] = {**w, "since": f"since {pd.Timestamp(earlier['date'].max()):%Y-%m-%d}"}
    return out


def snapshot(bars: pd.DataFrame, baskets: dict, sigma: float | None, daily: pd.DataFrame | None = None) -> dict:
    """Today's basket returns and the alert, plus the chart for every range in RANGES."""
    if bars is None or bars.empty:
        return {"status": "no_data"}
    b = bars.copy()
    ts = pd.to_datetime(b["ts"], utc=True).dt.tz_convert(MARKET_TZ)
    b["ts"], b["date"] = ts, ts.dt.date
    session = b["date"].max()
    ranges = _intraday_ranges(b, baskets)
    day = ranges.get("1D")
    if day is None:
        return {"status": "no_data", "session": str(session)}
    if daily is not None and not daily.empty:
        ranges.update(_daily_ranges(daily, _last_close(b[b["date"] == session], "ts"), session, baskets))
    z = float(day["spread"] / sigma) if sigma else None
    return clean({
        "status": "ok",
        "session": str(session),
        "as_of": b.loc[b["date"] == session, "ts"].max().isoformat(),
        "crowded": day["crowded"], "peers": day["peers"], "spread": day["spread"], "spy": day["spy"],
        "z": z, "alert": bool(z is not None and z <= ALERT_Z), "alert_z": ALERT_Z,
        "coverage": day["coverage"], "path": day["path"], "stocks": day["stocks"],
        "ranges": {k: ranges[k] for k in RANGES if k in ranges},
    })


# ------------------------------------------------------------------ monitor


class LiveMonitor:
    """Fetches quotes every `interval` seconds on a background thread and keeps the latest snapshot."""

    def __init__(self, baskets: dict, hist: dict, fetcher: Callable[[list[str]], pd.DataFrame] | None = None,
                 interval: int = 120, daily: pd.DataFrame | None = None,
                 daily_fetcher: Callable[[list[str]], pd.DataFrame] | None = None, daily_every: int = DAILY_EVERY):
        self.baskets, self.hist = baskets, hist
        self.fetcher = fetcher or yahoo_intraday
        self.interval = interval
        self.daily = daily                      # starts from the database, then Yahoo replaces it
        self.daily_fetcher = daily_fetcher
        self.daily_every = daily_every
        self._daily_at: float | None = None
        cs, ps = basket_symbols(baskets)
        self.symbols = cs + ps + [stress.BENCHMARK]
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._latest: dict | None = None
        self._last_ok: float | None = None
        self._last_try: float | None = None
        self._error: str | None = None

    def refresh_once(self) -> None:
        self._last_try = time.time()
        if self.daily_fetcher and (self._daily_at is None or time.time() - self._daily_at >= self.daily_every):
            self._daily_at = time.time()
            try:
                d = self.daily_fetcher(self.symbols)
                if d is not None and not d.empty:
                    self.daily = d
            except Exception:  # daily ranges keep the older data; the intraday view is unaffected
                pass
        try:
            snap = snapshot(self.fetcher(self.symbols), self.baskets, self.hist.get("sigma"), self.daily)
            with self._lock:
                self._latest, self._last_ok, self._error = snap, time.time(), None
        except Exception as e:  # keep serving the last good snapshot
            with self._lock:
                self._error = f"{type(e).__name__}: {e}"

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.refresh_once()
            self._stop.wait(self.interval)

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self._loop, name="sentryward-live", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()

    def state(self) -> dict:
        iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat() if t else None  # noqa: E731
        with self._lock:
            return clean({
                "snapshot": self._latest,
                "last_update": iso(self._last_ok),
                "last_try": iso(self._last_try),
                "error": self._error,
                "interval": self.interval,
            })


def meta(baskets: dict, hist: dict) -> dict:
    return clean({
        "period": baskets["period"], "prices_through": baskets["prices_through"],
        "crowded": baskets["crowded"], "n_peers": len(baskets["peers"]),
        "avg_beta_crowded": baskets["avg_beta_crowded"], "avg_beta_peers": baskets["avg_beta_peers"],
        "sigma": hist["sigma"], "hist_days": hist["days"], "history": hist["series"],
        "alert_z": ALERT_Z, "source": "Yahoo Finance, delayed about 15 minutes",
    })
