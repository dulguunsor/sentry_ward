"""YouTube look-ups for the live TV panel, through the official YouTube Data API.

Optional. With YOUTUBE_API_KEY set (free from Google Cloud), the server can:
  - turn a channel link like youtube.com/@name into the channel's ID, so its live
    stream can be embedded
  - find a channel's recent videos about a company, for the "relevant news" switch

Videos always play through YouTube's own embedded player. The key stays on the server.
The free quota is 10,000 units a day; a search costs 100 and a channel look-up 1, so
searches are cached and the server stops at DAILY_BUDGET units.
"""

from __future__ import annotations

import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

API = "https://www.googleapis.com/youtube/v3"
DAILY_BUDGET = 9000          # leave a margin under Google's 10,000
SEARCH_COST, CHANNEL_COST = 100, 1
CACHE_SECONDS = 1800

_SUFFIX = re.compile(
    r"\b(INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|PLC|HOLDINGS?|GROUP|CLASS [A-Z]|CL [A-Z]|"
    r"COM|COMMON|NEW|THE|SA|NV|AG|SE|LLC|LP|ADR|SPONSORED|SHS|ORD)\b\.?", re.I)


def youtube_key() -> str | None:
    return os.environ.get("YOUTUBE_API_KEY", "").strip() or None


def company_query(issuer: str | None, ticker: str | None) -> str:
    """'NVIDIA CORP' -> 'NVIDIA'; falls back to the ticker."""
    name = re.sub(r"[^\w&' -]", " ", _SUFFIX.sub(" ", issuer or ""))
    name = re.sub(r"\s+", " ", name).strip().title()
    return name or (ticker or "")


class YouTube:
    def __init__(self, key: str, session: requests.Session | None = None, clock=time.time):
        self.key = key
        self.http = session or requests.Session()
        self.clock = clock
        self._lock = threading.Lock()
        self._day = None
        self.used = 0
        self._cache: dict = {}

    def _spend(self, units: int) -> None:
        day = datetime.fromtimestamp(self.clock(), timezone.utc).date()
        with self._lock:
            if day != self._day:
                self._day, self.used = day, 0
            if self.used + units > DAILY_BUDGET:
                raise RuntimeError("Today's YouTube search budget is used up; it resets at midnight UTC.")
            self.used += units

    def _get(self, path: str, params: dict, cost: int) -> dict:
        self._spend(cost)
        r = self.http.get(f"{API}/{path}", params={**params, "key": self.key}, timeout=10)
        if r.status_code in (400, 403):
            msg = ""
            try:
                msg = r.json()["error"]["message"]
            except Exception:
                pass
            raise PermissionError(f"YouTube refused the request: {msg or r.status_code}. Check YOUTUBE_API_KEY.")
        r.raise_for_status()
        return r.json()

    def resolve(self, link: str) -> dict | None:
        """A channel link or @handle -> {"channel_id", "name"}; None if not a channel link."""
        m = re.search(r"youtube\.com/channel/(UC[\w-]{22})", link) or re.fullmatch(r"(UC[\w-]{22})", link.strip())
        if m:
            js = self._get("channels", {"part": "snippet", "id": m.group(1)}, CHANNEL_COST)
        else:
            h = re.search(r"(?:youtube\.com/)?(@[\w.-]+)", link.strip())
            if not h:
                return None
            js = self._get("channels", {"part": "snippet", "forHandle": h.group(1)}, CHANNEL_COST)
        items = js.get("items") or []
        if not items:
            return None
        return {"channel_id": items[0]["id"], "name": items[0]["snippet"]["title"]}

    def related(self, query: str, channel_ids: list[str], hours: int = 12, per_channel: int = 2) -> list[dict]:
        """Recent videos about `query` from the given channels, newest first. Cached for 30 minutes."""
        key = (query.lower(), tuple(sorted(channel_ids)), hours)
        now = self.clock()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < CACHE_SECONDS:
                return hit[1]
        after = (datetime.fromtimestamp(now, timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        out = []
        for cid in channel_ids[:5]:
            js = self._get("search", {"part": "snippet", "type": "video", "order": "date", "q": query,
                                      "channelId": cid, "publishedAfter": after, "maxResults": per_channel},
                           SEARCH_COST)
            for it in js.get("items") or []:
                vid = (it.get("id") or {}).get("videoId")
                sn = it.get("snippet") or {}
                if vid:
                    out.append({"video_id": vid, "title": sn.get("title", ""), "channel": sn.get("channelTitle", ""),
                                "published_at": sn.get("publishedAt"), "live": sn.get("liveBroadcastContent") == "live"})
        out.sort(key=lambda v: v["published_at"] or "", reverse=True)
        with self._lock:
            self._cache[key] = (now, out)
        return out
