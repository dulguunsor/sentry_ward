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
