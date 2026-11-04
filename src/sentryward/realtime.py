"""Real-time prices and news for `ward live`, from Alpaca's market data API.

Prices: one request a second returns the latest trade for every tracked stock. On the
free plan these come from the IEX exchange only: real trades, in real time, but IEX
handles a small share of US volume, so a quiet stock can go a while between trades.
Each stock's last-trade time is passed to the screen so stale prices are visible.

News: a streaming connection pushes headlines seconds after they are published. Only
the headline, the outlet's own short summary (when it has one) and a link are shown,
never the article text. With ANTHROPIC_API_KEY set, items without a summary get a one or
two sentence summary written by Claude.

Keys come from the environment and are never written to disk:
  APCA_API_KEY_ID, APCA_API_SECRET_KEY   free at alpaca.markets (a paper account is enough)
  ANTHROPIC_API_KEY                      optional, for news summaries
"""

from __future__ import annotations

import html
import json
import os
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo

import numpy as np
import requests

from . import stress

DATA_URL = "https://data.alpaca.markets"
NEWS_STREAM_URL = "wss://stream.data.alpaca.markets/v1beta1/news"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
SUMMARY_MODEL = os.environ.get("SENTRYWARD_SUMMARY_MODEL", "claude-haiku-5-5")
NY = ZoneInfo("America/New_York")
STALE_SECONDS = 60
NEWS_KEEP = 300


def alpaca_keys() -> tuple[str, str] | None:
    key, secret = os.environ.get("APCA_API_KEY_ID", "").strip(), os.environ.get("APCA_API_SECRET_KEY", "").strip()
    return (key, secret) if key and secret else None


# Yahoo writes share classes with a dash (BRK-B), Alpaca with a dot (BRK.B).
def to_alpaca(symbol: str) -> str:
    return symbol.replace("-", ".")


def from_alpaca(symbol: str) -> str:
    return symbol.replace(".", "-")


def _ts(s: str | None) -> float | None:
    if not s:
        return None
    try:  # Alpaca sends nanoseconds; Python parses up to microseconds
        s = re.sub(r"(\.\d{6})\d+", r"\1", s).replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def regular_hours(now: float) -> bool:
    t = datetime.fromtimestamp(now, NY)
    return t.weekday() < 5 and (9, 30) <= (t.hour, t.minute) < (16, 0)


# ------------------------------------------------------------------ prices


def parse_snapshots(js: dict) -> dict[str, dict]:
    """Alpaca's multi-symbol snapshot response -> {symbol: {price, ts, prev_close}}."""
    out = {}
    for sym, snap in (js or {}).items():
        if not isinstance(snap, dict):
            continue
        trade = snap.get("latestTrade") or {}
        prev = snap.get("prevDailyBar") or {}
        price = trade.get("p")
        if price is None:
            continue
        out[from_alpaca(sym)] = {"price": float(price), "ts": _ts(trade.get("t")),
                                 "prev_close": float(prev["c"]) if prev.get("c") is not None else None}
    return out


class AlpacaClient:
    def __init__(self, key: str, secret: str, session: requests.Session | None = None, feed: str = "iex"):
        self.key, self.secret, self.feed = key, secret, feed
        self.http = session or requests.Session()
        self.http.headers.update({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})

    def snapshots(self, symbols: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for i in range(0, len(symbols), 100):
            r = self.http.get(f"{DATA_URL}/v2/stocks/snapshots", timeout=10, params={
                "symbols": ",".join(to_alpaca(s) for s in symbols[i:i + 100]), "feed": self.feed})
            if r.status_code in (401, 403):
                raise PermissionError("Alpaca rejected the API key. Check APCA_API_KEY_ID and APCA_API_SECRET_KEY.")
            r.raise_for_status()
            out.update(parse_snapshots(r.json()))
        return out

    def news(self, symbols: list[str], limit: int = 50) -> list[dict]:
        r = self.http.get(f"{DATA_URL}/v1beta1/news", timeout=15, params={
            "symbols": ",".join(to_alpaca(s) for s in symbols), "limit": limit, "sort": "desc"})
        r.raise_for_status()
        return [norm_news(n) for n in r.json().get("news", [])]


# ------------------------------------------------------------------ per-second update


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return float(np.mean(xs)) if xs else None


def apply_prices(rng: dict, prices: dict[str, float], baskets: dict) -> dict:
    """Update one chart range with the latest prices, using the base prices it was built on."""
    base, last = rng.get("_base") or {}, dict(rng.get("_last") or {})
    last.update({s: p for s, p in prices.items() if s in base})
    ret = {s: last[s] / base[s] - 1 for s in base if s in last and base[s]}
    cs = [c["symbol"] for c in baskets["crowded"]]
    ps = [p["symbol"] for p in baskets["peers"]]
    crowded, peers = _mean(ret.get(s) for s in cs), _mean(ret.get(s) for s in ps)
    if crowded is None or peers is None:
        return rng
    tick = {s: c for s, c in zip([c["ticker"] for c in baskets["crowded"]], cs)}
    stocks = []
    for st in rng.get("stocks", []):
        sym = tick.get(st["ticker"])
        c = next((c for c in baskets["crowded"] if c["symbol"] == sym), None)
        r = ret.get(sym)
        pr = _mean(ret.get(p) for p in (c["peers"] if c else []))
        peers_list = [{**p, "ret": ret.get(ps_sym, p.get("ret"))}
                      for p, ps_sym in zip(st.get("peers", []), c["peers"] if c else [])]
        stocks.append({**st, "ret": r if r is not None else st.get("ret"),
                       "peers_ret": pr if pr is not None else st.get("peers_ret"),
                       "rel": (r - pr) if r is not None and pr is not None else st.get("rel"),
                       "peers": peers_list})
    out = {**rng, "crowded": crowded, "peers": peers, "spread": crowded - peers,
           "spy": ret.get(stress.BENCHMARK, rng.get("spy")), "stocks": stocks}
    return out


def merge_tick(snap: dict, rt: dict[str, dict], baskets: dict, sigma: float | None, now: float,
               alert_z: float) -> dict:
    """The full snapshot with every range brought up to the latest real-time prices.

    Intraday ranges get one extra live point at the current minute; daily ranges have
    today's point replaced. Ranges keep the start points they were built with until the
    next full refresh, a minute or two later.
    """
    if not snap or snap.get("status") != "ok" or not rt:
        return snap
    from .live import RANGES  # avoid a circular import at module load

    session = snap["session"]
    now_ny = datetime.fromtimestamp(now, NY)
    today = now_ny.date().isoformat()
    prices = {s: v["price"] for s, v in rt.items()
              if v.get("ts") and datetime.fromtimestamp(v["ts"], NY).date().isoformat() >= session}
    if not prices:
        return snap
    ranges = {}
    for key, rng in snap.get("ranges", {}).items():
        kind, _, _, fmt = RANGES[key]
        if today > session and kind in ("minutes", "session"):
            # The delayed bars have not reached today yet: measure from yesterday's close.
            rng = {**rng, "_base": dict(rng.get("_last") or {}), "path": [],
                   "since": "since yesterday's close"}
        upd = apply_prices(rng, prices, baskets)
        point = {"t": now_ny.strftime(fmt), "live": True,
                 **{k: upd[k] for k in ("crowded", "peers", "spread", "spy") if upd.get(k) is not None}}
        path = [p for p in rng.get("path", []) if not p.get("live")]
        if kind == "daily" and path and path[-1]["t"] == point["t"]:
            path[-1] = {**path[-1], **point}
        elif path and path[-1]["t"] == point["t"]:
            path[-1] = point
        else:
            path.append(point)
        ranges[key] = {**upd, "path": path}
    day = ranges.get("1D", snap["ranges"].get("1D"))
    ages = {s: now - v["ts"] for s, v in rt.items() if v.get("ts")}
    sym_of = {c["ticker"]: c["symbol"] for c in baskets["crowded"]}
    for r in ranges.values():
        r["stocks"] = [{**s, "age": ages.get(sym_of.get(s["ticker"]))} for s in r.get("stocks", [])]
    z = day["spread"] / sigma if sigma else None
    return {**snap, "ranges": ranges, "session": max(session, today),
            "as_of": now_ny.isoformat(),
            "crowded": day["crowded"], "peers": day["peers"], "spread": day["spread"], "spy": day.get("spy"),
            "z": z, "alert": bool(z is not None and z <= alert_z),
            "stocks": ranges["1D"]["stocks"] if "1D" in ranges else snap.get("stocks"),
            "path": ranges["1D"]["path"] if "1D" in ranges else snap.get("path"),
            "stale": sorted(s for s, a in ages.items() if a > STALE_SECONDS)}


# ------------------------------------------------------------------ news


_TAGS = re.compile(r"<[^>]+>")


def plain(text: str | None, limit: int = 4000) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAGS.sub(" ", text or ""))).strip()[:limit]


def norm_news(n: dict) -> dict:
    return {
        "id": n.get("id"),
        "headline": plain(n.get("headline"), 400),
        "summary": plain(n.get("summary"), 600) or None,
        "summary_by": "source" if plain(n.get("summary")) else None,
        "url": n.get("url") or None,
        "source": n.get("source") or "",
        "symbols": [from_alpaca(s) for s in n.get("symbols") or []],
        "created_at": n.get("created_at"),
        "_text": plain(n.get("content")),  # used only to write a summary; never sent to the browser
    }


def summarize(item: dict, api_key: str, session: requests.Session | None = None) -> str | None:
    """One or two sentences in Claude's own words; None if the call fails."""
    text = item.get("_text") or ""
    prompt = ("Summarize this market news item in one or two plain sentences for an investor: which "
              "company it concerns and what happened. Use your own words, do not quote the article, "
              "and do not predict price moves.\n\n"
              f"Headline: {item['headline']}\n\nArticle text: {text or '(not provided)'}")
    try:
        r = (session or requests).post(ANTHROPIC_URL, timeout=30, headers={
            "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": SUMMARY_MODEL, "max_tokens": 160, "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        parts = r.json().get("content", [])
        out = " ".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()
        return out or None
    except Exception:
        return None


class NewsFeed:
    """Keeps the latest headlines. A background thread holds the streaming connection open
    and reconnects if it drops; another writes summaries when an Anthropic key is set."""

    def __init__(self, tracked: list[str], client: AlpacaClient | None = None, anthropic_key: str | None = None,
                 connect: Callable | None = None, summarizer: Callable | None = None):
        self.tracked = set(tracked)
        self.client = client
        self.anthropic_key = anthropic_key
        self._connect = connect
        self._summarize = summarizer or (lambda item: summarize(item, anthropic_key) if anthropic_key else None)
        self._items: deque[dict] = deque(maxlen=NEWS_KEEP)
        self._ids: set = set()
        self._lock = threading.Lock()
        self._queue: deque[dict] = deque()
        self._stop = threading.Event()
        self.status = "starting"
        self.error: str | None = None
        self.version = 0

    # -- storing

    def add(self, item: dict) -> bool:
        with self._lock:
            if item["id"] in self._ids:
                return False
            item["tracked"] = sorted(set(item["symbols"]) & self.tracked)
            self._items.appendleft(item)
            self._ids.add(item["id"])
            self.version += 1
        if self.anthropic_key and item["tracked"] and not item.get("summary"):
            self._queue.append(item)
        return True

    def items(self, limit: int = 80) -> list[dict]:
        with self._lock:
            ordered = sorted(self._items, key=lambda n: n.get("created_at") or "", reverse=True)
            return [{k: v for k, v in n.items() if not k.startswith("_")} for n in ordered[:limit]]

    def state(self) -> dict:
        return {"status": self.status, "error": self.error, "summaries": bool(self.anthropic_key),
                "items": self.items()}

    # -- feeding

    def handle(self, raw: str) -> None:
        """One message from the stream: a JSON list of control messages and news items."""
        for m in json.loads(raw) if raw else []:
            t = m.get("T")
            if t == "n":
                self.add(norm_news(m))
            elif t == "success" and m.get("msg") == "authenticated":
                self.status, self.error = "live", None
            elif t == "error" and "already authenticated" not in str(m.get("msg", "")):
                self.status, self.error = "error", f"{m.get('code', '')} {m.get('msg', '')}".strip()

    def backlog(self) -> None:
        if not self.client:
            return
        try:
            for item in reversed(self.client.news(sorted(self.tracked), limit=50)):
                self.add(item)
        except Exception as e:
            self.error = f"Could not load recent news: {type(e).__name__}: {e}"

    def _stream(self) -> None:
        wait = 2
        while not self._stop.is_set():
            try:
                ws = self._connect()
                ws.send(json.dumps({"action": "auth", "key": self.client.key, "secret": self.client.secret}))
                ws.send(json.dumps({"action": "subscribe", "news": ["*"]}))
                wait = 2
                while not self._stop.is_set():
                    try:
                        self.handle(ws.recv(timeout=30))
                    except TimeoutError:
                        continue  # quiet minute; the connection is still open
            except Exception as e:
                if self.status != "error" or not self.error:
                    self.status, self.error = "reconnecting", f"{type(e).__name__}: {e}"
                self._stop.wait(wait)
                wait = min(60, wait * 2)

    def _summaries(self) -> None:
        while not self._stop.is_set():
            if not self._queue:
                self._stop.wait(1)
                continue
            item = self._queue.popleft()
            text = self._summarize(item)
            if text:
                with self._lock:
                    item["summary"], item["summary_by"] = text, "ai"
                    self.version += 1

    def start(self) -> None:
        threading.Thread(target=self.backlog, name="sentryward-news-backlog", daemon=True).start()
        if self._connect is None and self.client is not None:
            self._connect = _default_connect(self.client)
        if self._connect is not None:
            threading.Thread(target=self._stream, name="sentryward-news", daemon=True).start()
        if self.anthropic_key:
            threading.Thread(target=self._summaries, name="sentryward-summaries", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()


def _default_connect(client: AlpacaClient):
    def connect():
        from websockets.sync.client import connect as ws_connect
        return ws_connect(NEWS_STREAM_URL, open_timeout=15)
    return connect


# ------------------------------------------------------------------ price loop


class PriceFeed:
    """Asks Alpaca for the latest trades every `every` seconds and keeps them."""

    def __init__(self, symbols: list[str], fetch: Callable[[list[str]], dict], every: float = 1.0):
        self.symbols, self.fetch, self.every = symbols, fetch, every
        self.latest: dict[str, dict] = {}
        self.error: str | None = None
        self.last_ok: float | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.on_update: Callable[[], None] | None = None

    def poll_once(self) -> None:
        try:
            got = self.fetch(self.symbols)
            with self._lock:
                self.latest.update(got)
                self.last_ok, self.error = time.time(), None
        except PermissionError as e:
            self.error = str(e)
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
        if self.on_update:
            self.on_update()

    def prices(self) -> dict[str, dict]:
        with self._lock:
            return dict(self.latest)

    def _loop(self) -> None:
        while not self._stop.is_set():
            t0 = time.time()
            self.poll_once()
            self._stop.wait(max(0.05, self.every - (time.time() - t0)))

    def start(self) -> None:
        threading.Thread(target=self._loop, name="sentryward-prices", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()


def utc_now() -> float:
    return datetime.now(timezone.utc).timestamp()
