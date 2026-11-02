import { extent, isNum, pct, scale, ticks } from "./lib.js";

const C = { orange: "#f59e0b", cyan: "#0891b2", up: "#16a34a", down: "#dc2626", fg: "#e5e5e5", dim: "#8a8a8a", grid: "#525252" };

function Axis({ y, values, W, L, R }) {
  return values.map((v) => (
    <g key={v}>
      <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke={v === 0 ? C.dim : C.grid}
        strokeOpacity={v === 0 ? 0.7 : 0.3} strokeDasharray={v === 0 ? null : "2 4"} />
      <text x={L - 8} y={y(v) + 4} fill={C.dim} fontSize="11" textAnchor="end">{pct(v, 1)}</text>
    </g>
  ));
}

/** Today's path: crowded basket and peer basket since yesterday's close. */
export function IntradayChart({ path }) {
  const W = 900, H = 280, L = 62, R = 64, T = 14, B = 28;
  const ys = extent(path.flatMap((p) => [p.crowded, p.peers, p.spy]));
  const x = scale([0, Math.max(1, path.length - 1)], [L, W - R]);
  const y = scale(ys, [H - B, T]);
  const line = (k) => path.map((p, i) => (isNum(p[k]) ? `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[k]).toFixed(1)}` : "")).join("");
  const last = path[path.length - 1] || {};
  const labels = path.map((p, i) => [p.t, i]).filter(([t]) => t.endsWith(":00") || t.endsWith(":30")).filter((_, k) => k % 2 === 0);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Crowded basket and matched peers, return since yesterday's close">
      <Axis y={y} values={ticks(ys[0], ys[1], 4)} W={W} L={L} R={R} />
      {labels.map(([t, i]) => <text key={t} x={x(i)} y={H - 8} fill={C.dim} fontSize="11" textAnchor="middle">{t}</text>)}
      <path d={line("spy")} fill="none" stroke={C.dim} strokeWidth="1.5" strokeDasharray="4 3" />
      <path d={line("peers")} fill="none" stroke={C.cyan} strokeWidth="2" />
      <path d={line("crowded")} fill="none" stroke={C.orange} strokeWidth="2.5" />
      {["crowded", "peers"].map((k) => isNum(last[k]) && (
        <g key={k}>
          <circle cx={x(path.length - 1)} cy={y(last[k])} r="4" fill={k === "crowded" ? C.orange : C.cyan} />
          <text x={x(path.length - 1) + 8} y={y(last[k]) + 4} fill={k === "crowded" ? C.orange : C.cyan} fontSize="12" fontWeight="700">{pct(last[k])}</text>
        </g>
      ))}
    </svg>
  );
}

/** Daily spread over recent sessions, with the alert line at alertZ x sigma, and today. */
export function HistoryChart({ history, sigma, alertZ, today }) {
  const W = 1400, H = 260, L = 62, R = 18, T = 22, B = 28;
  const pts = history.concat(isNum(today) ? [{ date: "TODAY", spread: today, today: true }] : []);
  const band = isNum(sigma) ? alertZ * sigma : null;
  const ys = extent(pts.map((p) => p.spread).concat(band ?? [], band ? -band : []));
  const bw = (W - L - R) / Math.max(1, pts.length);
  const y = scale(ys, [H - B, T]);
  const x = (i) => L + i * bw;
  const months = pts.map((p, i) => [p.date.slice(0, 7), i]).filter(([m, i]) => i === 0 || m !== pts[i - 1].date.slice(0, 7));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Daily crowded minus peers spread with the alert line">
      <Axis y={y} values={ticks(ys[0], ys[1], 4)} W={W} L={L} R={R} />
      {band !== null && (
        <g>
          <line x1={L} x2={W - R} y1={y(band)} y2={y(band)} stroke={C.down} strokeDasharray="5 4" />
          <text x={L + 6} y={y(band) - 6} fill={C.down} fontSize="11">ALERT LINE {alertZ}σ = {pct(band, 2)}</text>
        </g>
      )}
      {pts.map((p, i) => (
        <rect key={p.date} x={x(i) + 1} width={Math.max(1, bw - 2)}
          y={Math.min(y(0), y(p.spread))} height={Math.abs(y(p.spread) - y(0))}
          fill={p.today ? C.orange : p.spread < 0 ? C.down : C.up} fillOpacity={p.today ? 1 : 0.75} />
      ))}
      {months.filter(([m]) => m !== "TODAY").map(([m, i]) => (
        <text key={m} x={x(i)} y={H - 8} fill={C.dim} fontSize="11">{m}</text>
      ))}
    </svg>
  );
}
