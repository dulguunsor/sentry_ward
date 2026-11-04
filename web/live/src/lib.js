// Pure helpers for the live screen. Tested in lib.test.js.

export const isNum = (v) => typeof v === "number" && Number.isFinite(v);

/** 0.0123 -> "+1.23%". */
export function pct(v, digits = 2, sign = true) {
  if (!isNum(v)) return "--";
  const s = (100 * v).toFixed(digits);
  return sign && v > 0 ? `+${s}%` : `${s}%`;
}

/** How unusual the gap is: "alert" at or below the threshold, "watch" past half of it. */
export function zLevel(z, alertZ) {
  if (!isNum(z)) return "unknown";
  if (z <= alertZ) return "alert";
  if (z <= alertZ / 2) return "watch";
  return "normal";
}

/** "live" if the last good update is recent, "stale" if it is overdue, "offline" if none. */
export function freshness(lastUpdateIso, intervalSec, nowMs = Date.now()) {
  if (!lastUpdateIso) return "offline";
  const age = (nowMs - Date.parse(lastUpdateIso)) / 1000;
  return age <= 2 * intervalSec + 30 ? "live" : "stale";
}

/** Linear scale from a numeric domain to a pixel range. */
export function scale([d0, d1], [r0, r1]) {
  const span = d1 - d0 || 1;
  return (v) => r0 + ((v - d0) / span) * (r1 - r0);
}

/** Round tick values covering [lo, hi], about n of them. */
export function ticks(lo, hi, n = 4) {
  if (!isNum(lo) || !isNum(hi) || hi <= lo) return [];
  const raw = (hi - lo) / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag)
    .reduce((best, s) => (Math.abs(s - raw) < Math.abs(best - raw) ? s : best));
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-12; v += step) out.push(+v.toFixed(12));
  return out;
}

/** Extent of several series, padded, always including zero. */
export function extent(values, pad = 0.12) {
  const v = values.filter(isNum);
  if (!v.length) return [-0.01, 0.01];
  let lo = Math.min(0, ...v), hi = Math.max(0, ...v);
  const p = (hi - lo) * pad || 0.002;
  return [lo - p, hi + p];
}

/** "2026-04-10T15:55:00-04:00" -> "15:55". */
export const hhmm = (iso) => (iso ? iso.slice(11, 16) : "--:--");

/** Index of the data point nearest to x, for n points spread evenly from x0 to x1. */
export function nearestIndex(x, x0, x1, n) {
  if (n <= 1) return 0;
  const i = Math.round(((x - x0) / (x1 - x0)) * (n - 1));
  return Math.max(0, Math.min(n - 1, i));
}

/** About k evenly spaced indexes into n points, always including the first and last. */
export function labelIndexes(n, k = 6) {
  if (n <= 0) return [];
  if (n <= k) return [...Array(n).keys()];
  const out = new Set();
  for (let j = 0; j < k; j++) out.add(Math.round((j * (n - 1)) / (k - 1)));
  return [...out];
}

export const RANGE_KEYS = ["1H", "3H", "1D", "5D", "1M", "3M", "1Y"];

/** The range to show: the saved choice if the server has it, else 1D, else the first available. */
export function pickRange(saved, available) {
  if (available.includes(saved)) return saved;
  if (available.includes("1D")) return "1D";
  return available[0] ?? null;
}

/** 1234567890 -> "$1.23B". */
export function usd(v) {
  if (!isNum(v)) return "--";
  const a = Math.abs(v);
  const [d, s] = a >= 1e12 ? [1e12, "T"] : a >= 1e9 ? [1e9, "B"] : a >= 1e6 ? [1e6, "M"] : a >= 1e3 ? [1e3, "K"] : [1, ""];
  return `${v < 0 ? "-" : ""}$${(a / d).toFixed(d === 1 ? 0 : a / d >= 100 ? 0 : a / d >= 10 ? 1 : 2)}${s}`;
}

/** 1234567 -> "1,234,567"; fractional counts are rounded. */
export const num = (v, digits = 0) => (isNum(v) ? v.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits }) : "--");

/** "#/stock/NVDA" -> { page: "stock", key: "NVDA" }. Anything unknown is the live page. */
export function parseRoute(hash) {
  const m = /^#\/(stock|fund)\/(.+)$/.exec(hash || "");
  if (!m) return { page: "live", key: null };
  let key = m[2];
  try { key = decodeURIComponent(key); } catch { /* keep as is */ }
  return { page: m[1], key };
}

export const stockHref = (key) => `#/stock/${encodeURIComponent(key)}`;
export const fundHref = (name) => `#/fund/${encodeURIComponent(name)}`;

/** "BRK.B", "brk-b" and "BRK/B" all become "BRKB", so tickers from different sources match. */
export const normTicker = (t) => (t || "").toUpperCase().replace(/[^A-Z0-9]/g, "");

/** A pasted YouTube link (video, live, short link or channel) -> an embed URL, or null. */
export function youtubeEmbed(input) {
  const s = (input || "").trim();
  if (!s) return null;
  const opts = "autoplay=1&mute=1&playsinline=1";
  const channel = /youtube\.com\/channel\/(UC[\w-]{22})/.exec(s) || /^(UC[\w-]{22})$/.exec(s);
  if (channel) return `https://www.youtube.com/embed/live_stream?channel=${channel[1]}&${opts}`;
  const id = /(?:v=|youtu\.be\/|\/live\/|\/embed\/|\/shorts\/)([\w-]{11})(?![\w-])/.exec(s) || /^([\w-]{11})$/.exec(s);
  return id ? `https://www.youtube.com/embed/${id[1]}?${opts}` : null;
}

/** "2026-04-10T14:05:00Z" -> "10:05" in New York time. */
export function nyTime(iso, seconds = false) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--:--";
  return d.toLocaleTimeString("en-GB", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit",
    ...(seconds ? { second: "2-digit" } : {}) });
}

/** Tracked headlines from the last `hours`, grouped by normalized ticker. */
export function newsByTicker(items, nowMs, hours = 3) {
  const out = {};
  for (const n of items || []) {
    const t = Date.parse(n.created_at);
    if (!(nowMs - t <= hours * 3600e3)) continue;
    for (const s of n.tracked || []) (out[normTicker(s)] ||= []).push(n);
  }
  return out;
}

/**
 * Where tracked headlines fall on an intraday path labelled "HH:MM": each headline goes to the
 * last bar at or before its New York time. Paths with other labels (5D, daily) get no marks.
 */
export function newsMarks(path, items, todayIso) {
  if (!path?.length || !/^\d\d:\d\d$/.test(path[0].t)) return [];
  const day = todayIso?.slice(0, 10);
  const marks = new Map();
  for (const n of items || []) {
    if (!n.tracked?.length) continue;
    const d = new Date(n.created_at);
    const nyDay = d.toLocaleDateString("en-CA", { timeZone: "America/New_York" });
    if (day && nyDay !== day) continue;
    const hm = nyTime(n.created_at);
    let i = -1;
    for (let k = 0; k < path.length; k++) if (path[k].t.slice(0, 5) <= hm) i = k;
    if (i < 0) continue;
    if (!marks.has(i)) marks.set(i, []);
    marks.get(i).push(n);
  }
  return [...marks].map(([i, items]) => ({ i, items }));
}

/** The channel ID in a YouTube channel link ("UC" + 22 characters), or null. */
export function channelIdOf(link) {
  const m = /youtube\.com\/channel\/(UC[\w-]{22})/.exec(link || "") || /^(UC[\w-]{22})$/.exec((link || "").trim());
  return m ? m[1] : null;
}

/** A YouTube embed URL with the player API turned on, so the page can tell when a video ends or fails. */
export function playerUrl(embed, origin) {
  if (!embed) return null;
  return `${embed}${embed.includes("?") ? "&" : "?"}enablejsapi=1&origin=${encodeURIComponent(origin || "")}`;
}

/**
 * Stocks worth a video search: news in the last `minutes` and a move against peers of at least
 * `minRel` either way. Largest move first.
 */
export function videoTriggers(stocks, recentNews, refMs, minRel = 0.015, minutes = 60) {
  const out = [];
  for (const s of stocks || []) {
    if (!isNum(s.rel) || Math.abs(s.rel) < minRel) continue;
    const items = (recentNews[normTicker(s.symbol || s.ticker)] || [])
      .filter((n) => refMs - Date.parse(n.created_at) <= minutes * 60e3);
    if (items.length) out.push({ ticker: s.ticker, issuer: s.issuer, rel: s.rel, headline: items[0].headline });
  }
  return out.sort((a, b) => Math.abs(b.rel) - Math.abs(a.rel));
}
