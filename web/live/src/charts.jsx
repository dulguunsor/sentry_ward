import { useRef, useState } from "react";
import { extent, isNum, labelIndexes, nearestIndex, pct, scale, ticks } from "./lib.js";

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

/** Tracks which data point the pointer is over. `toIndex` maps an x in viewBox units to an index. */
function useHover(W, toIndex) {
  const ref = useRef(null);
  const [hover, setHover] = useState(null);
  const onMove = (e) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box || !box.width) return;
    const vx = ((e.clientX - box.left) / box.width) * W;
    setHover({ i: toIndex(vx), left: e.clientX - box.left, width: box.width });
  };
  return { ref, hover, handlers: { onPointerMove: onMove, onPointerDown: onMove, onPointerLeave: () => setHover(null) } };
}

function Tip({ hover, children }) {
  if (!hover) return null;
  const flip = hover.left > hover.width * 0.6;
  return (
    <div className="charttip" style={flip ? { right: hover.width - hover.left + 14 } : { left: hover.left + 14 }}>
      {children}
    </div>
  );
}

const Row = ({ color, label, v, cls }) => (
  <div className="tiprow">
    <span><i style={{ background: color }} />{label}</span>
    <b className={cls ?? (isNum(v) ? (v < 0 ? "down" : "up") : "dim")}>{pct(v)}</b>
  </div>
);

/** Crowded basket, matched peers and SPY over the selected range, with a hover crosshair. */
export function PathChart({ path, since }) {
  const W = 900, H = 280, L = 62, R = 64, T = 14, B = 28;
  const n = path.length;
  const ys = extent(path.flatMap((p) => [p.crowded, p.peers, p.spy]));
  const x = scale([0, Math.max(1, n - 1)], [L, W - R]);
  const y = scale(ys, [H - B, T]);
  const { ref, hover, handlers } = useHover(W, (vx) => nearestIndex(vx, L, W - R, n));
  const line = (k) => path.map((p, i) => (isNum(p[k]) ? `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[k]).toFixed(1)}` : "")).join("");
  const last = path[n - 1] || {};
  const h = hover ? path[hover.i] : null;
  return (
    <div className="chartbox" ref={ref} {...handlers}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={`Crowded basket and matched peers, return ${since}`}>
        <Axis y={y} values={ticks(ys[0], ys[1], 4)} W={W} L={L} R={R} />
        {labelIndexes(n, 7).map((i) => (
          <text key={i} x={x(i)} y={H - 8} fill={C.dim} fontSize="11" textAnchor="middle">{path[i].t}</text>
        ))}
        <path d={line("spy")} fill="none" stroke={C.dim} strokeWidth="1.5" strokeDasharray="4 3" />
        <path d={line("peers")} fill="none" stroke={C.cyan} strokeWidth="2" />
        <path d={line("crowded")} fill="none" stroke={C.orange} strokeWidth="2.5" />
        {!h && ["crowded", "peers"].map((k) => isNum(last[k]) && (
          <g key={k}>
            <circle cx={x(n - 1)} cy={y(last[k])} r="4" fill={k === "crowded" ? C.orange : C.cyan} />
            <text x={x(n - 1) + 8} y={y(last[k]) + 4} fill={k === "crowded" ? C.orange : C.cyan} fontSize="12" fontWeight="700">{pct(last[k])}</text>
          </g>
        ))}
        {h && (
          <g>
            <line x1={x(hover.i)} x2={x(hover.i)} y1={T} y2={H - B} stroke={C.fg} strokeOpacity="0.35" />
            {[["spy", C.dim], ["peers", C.cyan], ["crowded", C.orange]].map(([k, c]) => isNum(h[k]) && (
              <circle key={k} cx={x(hover.i)} cy={y(h[k])} r="4" fill={c} stroke="#080808" strokeWidth="1.5" />
            ))}
          </g>
        )}
      </svg>
      <Tip hover={hover}>
        {h && <>
          <div className="tiphead">{h.t}</div>
          <Row color={C.orange} label="Crowded" v={h.crowded} />
          <Row color={C.cyan} label="Peers" v={h.peers} />
          <Row color={C.dim} label="SPY" v={h.spy} />
          <div className="tiprow sep"><span>Crowded − peers</span><b className={isNum(h.spread) && h.spread < 0 ? "down" : "up"}>{pct(h.spread)}</b></div>
          <div className="tipnote">Return {since}</div>
        </>}
      </Tip>
    </div>
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
  const { ref, hover, handlers } = useHover(W, (vx) => Math.max(0, Math.min(pts.length - 1, Math.floor((vx - L) / bw))));
  const months = pts.map((p, i) => [p.date.slice(0, 7), i]).filter(([m, i]) => i === 0 || m !== pts[i - 1].date.slice(0, 7));
  const h = hover ? pts[hover.i] : null;
  const z = h && isNum(sigma) ? h.spread / sigma : null;
  return (
    <div className="chartbox" ref={ref} {...handlers}>
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
            y={Math.min(y(0), y(p.spread))} height={Math.max(1, Math.abs(y(p.spread) - y(0)))}
            fill={p.today ? C.orange : p.spread < 0 ? C.down : C.up}
            fillOpacity={hover && hover.i !== i ? 0.35 : p.today ? 1 : 0.75} />
        ))}
        {months.filter(([m]) => m !== "TODAY").map(([m, i]) => (
          <text key={m} x={x(i)} y={H - 8} fill={C.dim} fontSize="11">{m}</text>
        ))}
      </svg>
      <Tip hover={hover}>
        {h && <>
          <div className="tiphead">{h.today ? "Today (live)" : h.date}</div>
          {!h.today && <Row color={C.orange} label="Crowded" v={h.crowded} />}
          {!h.today && <Row color={C.cyan} label="Peers" v={h.peers} />}
          <div className="tiprow sep"><span>Crowded − peers</span><b className={h.spread < 0 ? "down" : "up"}>{pct(h.spread)}</b></div>
          <div className="tiprow"><span>z-score</span><b className={isNum(z) && z <= alertZ ? "down" : ""}>{isNum(z) ? z.toFixed(2) : "--"}</b></div>
          {isNum(z) && z <= alertZ && <div className="tipnote down">Below the alert line</div>}
        </>}
      </Tip>
    </div>
  );
}

/**
 * A plain line chart over quarters (or any labelled points), with a hover readout.
 *   data    [{ t, ...values }]
 *   series  [{ k, label, color }]
 *   fmt     formats a value for the axis and the readout
 */
export function LineChart({ data, series, fmt, domain, height = 220, width = 900, label }) {
  const W = width, H = height, L = 70, R = 24, T = 14, B = 28;
  const n = data.length;
  const vals = data.flatMap((d) => series.map((s) => d[s.k])).filter(isNum);
  if (!vals.length) return <div className="empty">No history yet.</div>;
  let lo = domain ? domain[0] : Math.min(...vals), hi = domain ? domain[1] : Math.max(...vals);
  if (!domain) { const p = (hi - lo) * 0.1 || Math.abs(hi) * 0.1 || 1; lo -= p; hi += p; }
  const x = scale([0, Math.max(1, n - 1)], [L, W - R]);
  const y = scale([lo, hi], [H - B, T]);
  const { ref, hover, handlers } = useHover(W, (vx) => nearestIndex(vx, L, W - R, n));
  const h = hover ? data[hover.i] : null;
  const line = (k) => {
    let d = "", pen = false;
    data.forEach((p, i) => {
      if (!isNum(p[k])) { pen = false; return; }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(p[k]).toFixed(1)}`;
      pen = true;
    });
    return d;
  };
  return (
    <div className="chartbox" ref={ref} {...handlers}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={label}>
        {ticks(lo, hi, 4).map((v) => (
          <g key={v}>
            <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke={C.grid} strokeOpacity="0.3" strokeDasharray="2 4" />
            <text x={L - 8} y={y(v) + 4} fill={C.dim} fontSize="11" textAnchor="end">{fmt(v)}</text>
          </g>
        ))}
        {labelIndexes(n, 6).map((i) => (
          <text key={i} x={x(i)} y={H - 8} fill={C.dim} fontSize="11" textAnchor="middle">{data[i].t}</text>
        ))}
        {series.map((s) => <path key={s.k} d={line(s.k)} fill="none" stroke={s.color} strokeWidth="2" />)}
        {series.map((s) => data.map((p, i) => isNum(p[s.k]) && (n <= 40 || i === n - 1) && (
          <circle key={`${s.k}${i}`} cx={x(i)} cy={y(p[s.k])} r={i === n - 1 ? 3.5 : 2.5} fill={s.color} />
        )))}
        {h && <line x1={x(hover.i)} x2={x(hover.i)} y1={T} y2={H - B} stroke={C.fg} strokeOpacity="0.35" />}
      </svg>
      <Tip hover={hover}>
        {h && <>
          <div className="tiphead">{h.t}</div>
          {series.map((s) => (
            <div className="tiprow" key={s.k}>
              <span><i style={{ background: s.color }} />{s.label}</span><b>{isNum(h[s.k]) ? fmt(h[s.k]) : "--"}</b>
            </div>
          ))}
          {h.note && <div className="tipnote">{h.note}</div>}
        </>}
      </Tip>
    </div>
  );
}
