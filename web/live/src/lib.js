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
