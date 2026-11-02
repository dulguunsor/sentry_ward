// Sentry Ward dashboard. Inlined into one self-contained HTML file by dashboard.py;
// DATA is defined just before this script runs.
// ---------------------------------------------------------------- helpers
function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null) continue;
    if (k === "class") e.className = v; else if (k === "style") e.style.cssText = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null) e.append(c instanceof Node ? c : String(c));
  return e;
}
const SVGNS = "http://www.w3.org/2000/svg";
function sv(tag, attrs, text) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
  if (text != null) e.textContent = text;
  return e;
}
const C = { orange: "#d97706", orangeHi: "#f59e0b", up: "#16a34a", down: "#dc2626", cyan: "#0891b2", fg: "#e5e5e5", dim: "#8a8a8a", faint: "#5c5c5c", grid: "#525252", line: "#1a1a1a" };
const isNum = v => typeof v === "number" && isFinite(v);
const pct = (v, d = 1, sign = false) => isNum(v) ? (sign && v > 0 ? "+" : "") + (100 * v).toFixed(d) + "%" : "--";
const num = (v, d = 1) => isNum(v) ? v.toFixed(d) : "--";
const bn = v => isNum(v) ? "$" + (v / 1e9).toFixed(v >= 1e10 ? 1 : 2) + "B" : "--";
const money = v => !isNum(v) ? "--" : v >= 1e9 ? "$" + (v / 1e9).toFixed(1) + "B" : "$" + (v / 1e6).toFixed(0) + "M";
const quarter = p => { const [y, m] = p.split("-").map(Number); return `Q${Math.ceil(m / 3)} ${y}`; };
function bar(v, max, color, label) {
  const w = isNum(v) ? Math.max(0, Math.min(100, 100 * v / max)) : 0;
  return el("div", { class: "bar" }, el("div", { class: "t" }, el("div", { class: "f", style: `width:${w}%;background:${color}` })),
    el("span", {}, label != null ? label : (isNum(v) ? v.toFixed(0) : "--")));
}
function table(id, head, rows) {
  document.getElementById(id).replaceChildren(
    el("thead", {}, el("tr", {}, head.map(([label, cls]) => el("th", { class: cls || null, scope: "col" }, label)))),
    el("tbody", {}, rows.length ? rows : [el("tr", {}, el("td", { class: "l dim", colspan: head.length }, "No data"))]));
}
function seg(id, options, current, onPick) {
  const box = document.getElementById(id);
  const draw = sel => {
    box.querySelectorAll("button").forEach(b => b.remove());
    for (const [val, label] of options)
      box.append(el("button", { type: "button", "aria-pressed": String(val === sel), onclick: () => { draw(val); onPick(val); } }, label));
  };
  draw(current);
}
// More crowded than last quarter is the risk direction: red. Less crowded: green.
const changeCell = v => el("td", { class: isNum(v) ? (v > 0 ? "down" : "up") : "dim" },
  isNum(v) ? (v > 0 ? "▲ +" : "▼ ") + v.toFixed(1) : "NEW");

// ---------------------------------------------------------------- screens (hash routing)
const SCREENS = ["dashboard", "crowded", "overlap", "stress", "unwind", "funds", "about"];
function show() {
  const name = SCREENS.includes(location.hash.slice(1)) ? location.hash.slice(1) : "dashboard";
  document.querySelectorAll(".screen").forEach(s => { s.hidden = s.dataset.screen !== name; });
  document.querySelectorAll(".tabs a").forEach(a => { if (a.getAttribute("href") === "#" + name) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
}
window.addEventListener("hashchange", show);
show();

// ---------------------------------------------------------------- menu bar, status bar, KPIs
const idx = DATA.index, last = idx[idx.length - 1] || {};
const hardSet = new Set(DATA.hard_exit.map(r => r.ticker));
document.getElementById("mbLive").textContent = `DATA ${quarter(DATA.period)}`;
document.getElementById("mbBuilt").textContent = `BUILT ${DATA.generated}`;
document.getElementById("mbFunds").textContent = `${DATA.n_funds} FUNDS`;
document.getElementById("stPeriod").textContent = `QUARTER ENDING ${DATA.period}` + (DATA.missing_funds.length ? ` · NO FILING: ${DATA.missing_funds.join(", ")}` : "");
document.getElementById("crowdNote").textContent = `${quarter(DATA.period)} · ETFs excluded · top ${DATA.stocks.length}`;
if (DATA.coverage) document.getElementById("aboutCov").textContent =
  `Latest quarter: ${pct(DATA.coverage.value_priced_share)} of the funds' stock value has usable prices, ${pct(DATA.coverage.value_with_shares_share)} also has shares outstanding.`;

function kpi(label, value, unit, detail, detailClass, valueColor) {
  return el("div", { class: "kpi" }, el("div", { class: "l" }, label),
    el("div", { class: "v", style: valueColor ? `color:${valueColor}` : null }, value, unit ? el("small", {}, unit) : null),
    el("div", { class: "d " + (detailClass || "") }, detail));
}
const chg = v => isNum(v) ? `${v > 0 ? "▲ +" : "▼ "}${(100 * v).toFixed(2)} pts QoQ · same funds` : "first quarter";
const chgCls = v => isNum(v) ? (v > 0 ? "down" : "up") : "";
const h1 = DATA.stress.primary && DATA.stress.primary.h1 ? DATA.stress.primary.h1.summary : null;
const topStock = DATA.stocks[0];
document.getElementById("kpis").append(
  kpi("RAW CROWDING INDEX", num(last.raw, 3), null, chg(last.raw_change), chgCls(last.raw_change)),
  kpi("ACTIVE CROWDING INDEX", num(last.active, 3), null, chg(last.active_change), chgCls(last.active_change)),
  kpi("MOST CROWDED", topStock ? topStock.ticker : "--", topStock ? `${num(topStock.score, 0)}` : null,
      topStock ? `${topStock.holders} of ${topStock.n_funds} funds hold it` : ""),
  kpi("PRICE COVERAGE", DATA.coverage ? pct(DATA.coverage.value_priced_share) : "--", null,
      DATA.coverage ? `of fund stock value · ${pct(DATA.coverage.value_with_shares_share, 0)} with shares out.` : "run ward market"),
  kpi("STRESS TEST H1", h1 ? pct(h1.avg_diff, 1, true) : "--", null,
      h1 ? `${h1.ci_high < 0 ? "SUPPORTED" : "NOT SUPPORTED"} · [${pct(h1.ci_low, 1, true)}, ${pct(h1.ci_high, 1, true)}]` : "run ward stress",
      h1 ? (h1.ci_high < 0 ? "down" : "dim") : "", h1 ? (h1.ci_high < 0 ? C.down : C.fg) : null),
);

// ---------------------------------------------------------------- crowding map
function tileColor(r) {
  if (!isNum(r)) return ["#161616", C.fg];
  if (r >= 66) return ["#4c0e0e", "#fca5a5"];
  if (r >= 33) return ["#4a3206", "#fcd34d"];
  return ["#0b3d1c", "#86efac"];
}
document.getElementById("tiles").replaceChildren(...DATA.stocks.slice(0, 12).map(s => {
  const [bg, fg] = tileColor(s.exit_risk);
  return el("div", { class: "tile", style: `background:${bg};color:${fg};${hardSet.has(s.ticker) ? "border-color:" + C.orangeHi : ""}`,
                     title: `${s.issuer}: crowding ${num(s.score, 0)}, exit risk ${num(s.exit_risk, 0)}, ${num(s.dte)} days to exit` },
    el("b", {}, s.ticker), el("span", {}, `${num(s.score, 0)} · ${isNum(s.dte) ? num(s.dte) + "d" : "--"}`));
}));

// ---------------------------------------------------------------- index chart
function lineChart(host, key, label, color) {
  const pts = idx.filter(r => isNum(r[key]));
  const W = 900, H = 260, L = 58, R = 18, T = 14, B = 28;
  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", role: "img", "aria-label": `${label} by quarter` });
  if (pts.length < 2) { host.replaceChildren(svg); return; }
  const t0 = Date.parse(pts[0].period), t1 = Date.parse(pts[pts.length - 1].period);
  const ys = pts.map(p => p[key]);
  let lo = Math.min(...ys), hi = Math.max(...ys); const pad = (hi - lo) * 0.15 || 0.01; lo -= pad; hi += pad;
  const x = t => L + (W - L - R) * (t - t0) / (t1 - t0), y = v => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
  for (let k = 0; k <= 4; k++) {
    const v = lo + (hi - lo) * k / 4, yy = y(v);
    svg.append(sv("line", { x1: L, x2: W - R, y1: yy, y2: yy, stroke: C.grid, "stroke-opacity": 0.35, "stroke-dasharray": "2 4" }));
    svg.append(sv("text", { x: L - 8, y: yy + 4, fill: C.dim, "font-size": 11, "text-anchor": "end" }, v.toFixed(3)));
  }
  for (const ev of DATA.events) {
    const t = Date.parse(ev.start); if (t < t0 || t > t1) continue;
    const xx = x(t);
    svg.append(sv("line", { x1: xx, x2: xx, y1: T, y2: H - B, stroke: C.down, "stroke-opacity": 0.5, "stroke-dasharray": "3 3" }));
    svg.append(sv("text", { x: xx + 3, y: T + 10, fill: C.down, "fill-opacity": 0.8, "font-size": 10 }, ev.name.split(",")[0].toUpperCase()));
  }
  let lastYear = null;
  for (const p of pts) {
    const yr = p.period.slice(0, 4);
    if (yr !== lastYear) { lastYear = yr; svg.append(sv("text", { x: x(Date.parse(p.period)), y: H - 8, fill: C.dim, "font-size": 11, "text-anchor": "middle" }, yr)); }
  }
  const d = pts.map((p, i) => `${i ? "L" : "M"}${x(Date.parse(p.period)).toFixed(1)},${y(p[key]).toFixed(1)}`).join("");
  svg.append(sv("path", { d: `${d}L${x(t1).toFixed(1)},${H - B}L${L},${H - B}Z`, fill: color, "fill-opacity": 0.1 }));
  svg.append(sv("path", { d, fill: "none", stroke: color, "stroke-width": 2 }));
  const lp = pts[pts.length - 1];
  svg.append(sv("circle", { cx: x(t1), cy: y(lp[key]), r: 4, fill: color }));
  svg.append(sv("text", { x: x(t1) - 8, y: y(lp[key]) - 10, fill: color, "font-size": 12, "font-weight": 700, "text-anchor": "end" }, lp[key].toFixed(3)));
  host.replaceChildren(svg);
}
const IDX = { raw: ["raw", "RAW INDEX", C.orangeHi, "Raw: average share of portfolios two funds have in common."],
              active: ["active", "ACTIVE INDEX", C.cyan, "Active: average share of the bets beyond the market that two funds have in common."] };
function drawIdx(which) {
  const [key, label, color, text] = IDX[which];
  lineChart(document.getElementById("chIdx"), key, label, color);
  const ch = idx.filter(p => isNum(p[which + "_change"]));
  const total = ch.reduce((s, p) => s + p[which + "_change"], 0);
  document.getElementById("idxNote").textContent = `${text} Over ${ch.length} quarter-to-quarter changes measured on the same fund pairs, it moved ${(100 * total).toFixed(1)} points in total. Red dashed lines: stress test events.`;
}
seg("segIdx", [["raw", "RAW"], ["active", "ACTIVE"]], "raw", drawIdx);
drawIdx("raw");

// ---------------------------------------------------------------- hard exit, pairs
table("tExit", [["SYMBOL", "l"], ["FUNDS"], ["OWN"], ["EXIT DAYS"], ["RISK"]],
  DATA.hard_exit.map(s => el("tr", {},
    el("td", { class: "l tk", title: s.issuer }, s.ticker), el("td", {}, s.holders), el("td", {}, pct(s.own, 1)),
    el("td", { class: "down" }, num(s.dte)), el("td", {}, num(s.exit_risk, 0)))));
const pairRows = n => DATA.pairs.slice(0, n).map(p => el("tr", {},
  el("td", { class: "l" }, `${p.fund_a} · ${p.fund_b}`), el("td", { class: "tk" }, num(p.active_overlap, 3)),
  el("td", {}, num(p.raw_overlap, 3)), el("td", { class: "dim" }, p.shared_stocks)));
table("tPairsMini", [["PAIR", "l"], ["ACTIVE"], ["RAW"], ["STOCKS"]], pairRows(6));
table("tPairs", [["PAIR", "l"], ["ACTIVE"], ["RAW"], ["STOCKS"]], pairRows(20));

// ---------------------------------------------------------------- crowded table
table("tStocks",
  [["#"], ["SYMBOL", "l"], ["NAME", "l"], ["FUNDS"], ["OWN"], ["EXIT DAYS"], ["EXIT RISK", "l"], ["SCORE", "l"], ["Δ QoQ"]],
  DATA.stocks.map(s => el("tr", {},
    el("td", { class: "dim" }, s.rank),
    el("td", { class: "l tk" }, s.ticker, hardSet.has(s.ticker) ? el("span", { class: "chip down" }, "HARD EXIT") : null),
    el("td", { class: "l nm", title: s.issuer }, s.issuer),
    el("td", {}, `${s.holders}/${s.n_funds}`), el("td", {}, pct(s.own, 2)),
    el("td", { class: isNum(s.dte) && s.dte >= 15 ? "down" : null }, num(s.dte)),
    el("td", { class: "l" }, bar(s.exit_risk, 100, C.down)),
    el("td", { class: "l" }, bar(s.score, 100, C.orange)),
    changeCell(s.change))));

// ---------------------------------------------------------------- overlap matrix
const styleOf = Object.fromEntries(DATA.funds.map(f => [f.fund, f.style || ""]));
const STYLE_ORDER = ["multi-manager", "quant", "macro", "tiger-cub", "fundamental", "activist"];
const abbr = n => n.replace(/[^A-Za-z0-9]/g, "").slice(0, 5).toUpperCase();
function drawHeat(which) {
  const m = which === "active" ? DATA.active_overlap : DATA.overlap;
  const order = m.funds.map((f, i) => [f, i]).sort((a, b) =>
    (STYLE_ORDER.indexOf(styleOf[a[0]]) - STYLE_ORDER.indexOf(styleOf[b[0]])) || a[0].localeCompare(b[0]));
  const vals = m.values.flat().filter(v => isNum(v) && v < 0.9999);
  const max = Math.max(0.05, ...vals);
  const heat = document.getElementById("heat");
  heat.style.gridTemplateColumns = `124px repeat(${order.length}, minmax(32px, 1fr))`;
  heat.style.minWidth = `${124 + 34 * order.length}px`;
  const cells = [el("div", {})];
  for (const [f] of order) cells.push(el("div", { class: "cl", title: f }, abbr(f)));
  for (const [fa, i] of order) {
    cells.push(el("div", { class: "rl", title: styleOf[fa] }, fa));
    for (const [fb, j] of order) {
      if (i === j) { cells.push(el("div", { style: "background:#e5e5e5;color:#080808;font-weight:700" }, "1.00")); continue; }
      const v = m.values[i][j];
      if (!isNum(v)) { cells.push(el("div", { style: `color:${C.faint}` }, "--")); continue; }
      const t = Math.min(v / max, 1);
      const r = Math.round(10 + (217 - 10) * t), g = Math.round(10 + (119 - 10) * t), b = Math.round(10 + (6 - 10) * t);
      cells.push(el("div", { title: `${fa} × ${fb}: ${v.toFixed(3)}`,
        style: `background:rgb(${r},${g},${b});color:${t > 0.6 ? "#080808" : "#e5e5e5"};font-weight:${t > 0.6 ? 700 : 400}` }, v.toFixed(2).replace(/^0/, "")));
    }
  }
  heat.replaceChildren(...cells);
  document.getElementById("ovExplain").textContent = (which === "active"
    ? "ACTIVE overlap: of the bets each fund makes beyond the combined 13F market, the share that is the same bet. Removes overlap that comes only from both funds holding the big index names."
    : "RAW overlap: the share of two portfolios that is identical. Large diversified funds score high partly because both resemble the market.")
    + ` Funds grouped by style. Darkest cell = ${max.toFixed(2)}. ${quarter(DATA.period)}.`;
}
seg("segOv", [["active", "ACTIVE"], ["raw", "RAW"]], "active", drawHeat);
drawHeat("active");

// ---------------------------------------------------------------- stress test
function stressChart(pe, sm, W = 1360, L = 300, R = 260) {
  const lows = pe.map(p => p.ci_low).concat(sm.ci_low, 0), highs = pe.map(p => p.ci_high).concat(sm.ci_high, 0);
  let lo = Math.min(...lows.filter(isNum)), hi = Math.max(...highs.filter(isNum));
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  const rowH = 34, T = 28, n = pe.length + 1, H = T + rowH * n + 28;
  const x = v => L + (W - L - R) * (v - lo) / (hi - lo);
  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", role: "img", style: `min-width:640px;max-width:${W}px`,
                          "aria-label": "Crowded minus matched return in each sell-off, with 95% intervals" });
  svg.append(sv("text", { x: x(lo) + 2, y: 14, fill: C.down, "font-size": 11 }, "◀ CROWDED FELL MORE"));
  svg.append(sv("text", { x: x(hi) - 2, y: 14, fill: C.up, "font-size": 11, "text-anchor": "end" }, "CROWDED FELL LESS ▶"));
  svg.append(sv("line", { x1: x(0), x2: x(0), y1: T - 4, y2: H - 24, stroke: C.grid }));
  const step = (hi - lo) > 0.08 ? 0.02 : 0.01;
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) {
    svg.append(sv("line", { x1: x(v), x2: x(v), y1: T - 4, y2: H - 24, stroke: C.grid, "stroke-opacity": 0.25, "stroke-dasharray": "2 4" }));
    svg.append(sv("text", { x: x(v), y: H - 8, fill: C.dim, "font-size": 10, "text-anchor": "middle" }, pct(v, 0, true)));
  }
  const rows = pe.map(p => ({ label: p.event, sub: `holdings ${p.formation}`, d: p.diff, lo: p.ci_low, hi: p.ci_high }))
    .concat([{ label: `AVERAGE OF ${sm.events}`, sub: "all events", d: sm.avg_diff, lo: sm.ci_low, hi: sm.ci_high, avg: true }]);
  rows.forEach((r, k) => {
    const yy = T + rowH * k + rowH / 2;
    if (r.avg) svg.append(sv("line", { x1: 0, x2: W, y1: yy - rowH / 2, y2: yy - rowH / 2, stroke: "#262626" }));
    svg.append(sv("text", { x: 0, y: yy - 2, fill: r.avg ? C.orangeHi : C.fg, "font-size": 12, "font-weight": r.avg ? 700 : 400 }, r.label.toUpperCase()));
    svg.append(sv("text", { x: 0, y: yy + 11, fill: C.faint, "font-size": 10 }, r.sub));
    const col = isNum(r.hi) && r.hi < 0 ? C.down : isNum(r.lo) && r.lo > 0 ? C.up : C.dim;
    if (isNum(r.lo) && isNum(r.hi)) {
      svg.append(sv("line", { x1: x(r.lo), x2: x(r.hi), y1: yy, y2: yy, stroke: col, "stroke-width": 2 }));
      for (const e of [r.lo, r.hi]) svg.append(sv("line", { x1: x(e), x2: x(e), y1: yy - 5, y2: yy + 5, stroke: col, "stroke-width": 2 }));
    }
    if (isNum(r.d)) svg.append(sv(r.avg ? "rect" : "circle", r.avg
      ? { x: x(r.d) - 5, y: yy - 5, width: 10, height: 10, fill: C.orangeHi }
      : { cx: x(r.d), cy: yy, r: 5, fill: col === C.dim ? C.fg : col }));
    svg.append(sv("text", { x: W - R + 16, y: yy + 4, fill: r.avg ? C.orangeHi : (isNum(r.d) && r.d < 0 ? "#f87171" : "#4ade80"), "font-size": 12, "font-weight": r.avg ? 700 : 400 },
      `${pct(r.d, 1, true)}  [${pct(r.lo, 1, true)}, ${pct(r.hi, 1, true)}]`));
  });
  return svg;
}
function verdict(test, sm) {
  const supported = isNum(sm.ci_high) && sm.ci_high < 0;
  const name = test === "h1" ? "H1: crowded stocks (top 10%) fall more than matched peers." : "H2: crowded and hard-to-exit stocks fall more than matched peers.";
  return el("div", { class: "verdict" }, el("b", { class: supported ? "down" : "or" }, supported ? "SUPPORTED" : "NOT SUPPORTED"),
    el("span", {}, `${name} Average ${pct(sm.avg_diff, 1, true)}, 95% interval ${pct(sm.ci_low, 1, true)} to ${pct(sm.ci_high, 1, true)}; crowded fell more in ${sm.events_negative} of ${sm.events} events.`));
}
const NO_STRESS = "No stress test results yet. Run `ward stress` and `ward stress --public`, then `ward dashboard` again.";
let sTest = "h1", sRun = "primary";
function drawStress() {
  const body = document.getElementById("stressBody"), res = DATA.stress[sRun];
  if (!res || !res[sTest]) { body.replaceChildren(el("div", { class: "note" }, NO_STRESS)); return; }
  const { per_event: pe, summary: sm } = res[sTest];
  body.replaceChildren(verdict(sTest, sm), el("div", { class: "scroll" }, stressChart(pe, sm)),
    el("div", { class: "note" }, "Each line is a 95% bootstrap interval for the crowded group's return minus that of stocks with similar size and beta. Red: crowded fell significantly more. Green: significantly less. Grey: no significant difference. Events, groups and matching were committed in PREREGISTRATION.md before the test was run. Holdings: LAST QUARTER END uses the quarter end before each event; PUBLIC 45+ DAYS uses only holdings the public could have seen."));
}
seg("segTest", [["h1", "H1 CROWDED"], ["h2", "H2 HARD EXIT"]], sTest, v => { sTest = v; drawStress(); });
seg("segRun", [["primary", "LAST QTR END"], ["public", "PUBLIC 45D+"]], sRun, v => { sRun = v; drawStress(); });
drawStress();
(() => {
  const host = document.getElementById("stressMini"), res = DATA.stress.primary;
  if (!res || !res.h1) { host.replaceChildren(el("div", { class: "note" }, NO_STRESS)); return; }
  host.replaceChildren(verdict("h1", res.h1.summary), el("div", { class: "scroll" }, stressChart(res.h1.per_event, res.h1.summary, 960, 230, 230)),
    el("div", { class: "note" }, el("a", { href: "#stress" }, "Open the STRESS screen"), " for H2 and the public-holdings run."));
})();

// ---------------------------------------------------------------- unwind simulator
let uFrac = 0.25, uDays = 5;
function drawUnwind() {
  const rows = DATA.stocks.filter(s => isNum(s.adv_shares) && s.adv_shares > 0 && isNum(s.daily_vol) && isNum(s.total_shares))
    .map(s => {
      const sold = uFrac * s.total_shares, part = sold / uDays / s.adv_shares;
      const move = 0.7 * s.daily_vol * Math.sqrt(sold / s.adv_shares);
      return { s, value: isNum(s.close) ? sold * s.close : uFrac * s.value_usd, part, move };
    }).sort((a, b) => b.move - a.move);
  table("tUnwind", [["SYMBOL", "l"], ["NAME", "l"], ["VALUE SOLD"], ["PER DAY, % OF VOLUME"], ["EST. PRICE MOVE"], ["", "l"]],
    rows.map(r => el("tr", {},
      el("td", { class: "l tk" }, r.s.ticker), el("td", { class: "l nm", title: r.s.issuer }, r.s.issuer),
      el("td", {}, money(r.value)),
      el("td", { class: r.part > 0.2 ? "down" : null }, pct(r.part, 0)),
      el("td", { class: r.move >= 0.025 ? "down" : null, style: "font-weight:700" }, pct(-r.move, 1)),
      el("td", { class: "l" }, el("div", { class: "bar", style: "min-width:160px" }, el("div", { class: "t" },
        el("div", { class: "f", style: `width:${Math.min(100, 100 * r.move / 0.08)}%;background:${r.move >= 0.025 ? C.down : C.orange}` })))))));
}
seg("segFrac", [[0.1, "10%"], [0.25, "25%"], [0.5, "50%"]], uFrac, v => { uFrac = v; drawUnwind(); });
seg("segDays", [[1, "1D"], [5, "5D"], [10, "10D"]], uDays, v => { uDays = v; drawUnwind(); });
drawUnwind();

// ---------------------------------------------------------------- funds and ETFs
table("tFunds", [["FUND", "l"], ["STYLE", "l"], ["STOCKS"], ["13F VALUE"], ["ETF SHARE"], ["ACTIVE SHARE", "l"]],
  DATA.funds.map(f => el("tr", {},
    el("td", { class: "l", style: "font-weight:700" }, f.fund), el("td", { class: "l dim" }, (f.style || "").toUpperCase()), el("td", {}, f.stocks),
    el("td", {}, bn(f.value_usd)), el("td", {}, pct(f.etf_share, 0)),
    el("td", { class: "l" }, bar(f.active_share, 1, C.orange, num(f.active_share, 2))))));
table("tEtfs", [["NAME", "l"], ["CLASS", "l"], ["FUNDS"], ["VALUE"]],
  DATA.etfs.map(e => el("tr", {},
    el("td", { class: "l nm", title: e.issuer }, e.issuer), el("td", { class: "l dim" }, e.class || ""),
    el("td", {}, e.holders), el("td", {}, bn(e.value_usd)))));
