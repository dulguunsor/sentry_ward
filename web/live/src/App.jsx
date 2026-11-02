import { useEffect, useMemo, useState } from "react";
import { HistoryChart, PathChart } from "./charts.jsx";
import { RANGE_KEYS, freshness, hhmm, isNum, pct, pickRange, zLevel } from "./lib.js";

const POLL_MS = 15000;

function useApi() {
  const [meta, setMeta] = useState(null);
  const [state, setState] = useState(null);
  const [failed, setFailed] = useState(false);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    fetch("/api/meta").then((r) => r.json()).then(setMeta).catch(() => setFailed(true));
  }, []);
  useEffect(() => {
    let alive = true;
    const load = () =>
      fetch("/api/live", { cache: "no-store" })
        .then((r) => r.json())
        .then((s) => { if (alive) { setState(s); setFailed(false); setNow(Date.now()); } })
        .catch(() => { if (alive) { setFailed(true); setNow(Date.now()); } });
    load();
    const id = setInterval(load, POLL_MS);
    return () => { alive = false; clearInterval(id); };
  }, []);
  return { meta, state, failed, now };
}

const RANGE_KEY = "sentryward.range";
const loadRange = () => { try { return localStorage.getItem(RANGE_KEY) || "1D"; } catch { return "1D"; } };
const saveRange = (r) => { try { localStorage.setItem(RANGE_KEY, r); } catch { /* storage unavailable */ } };

/** A small "i" that explains a number on hover or keyboard focus. */
function Info({ children, wide }) {
  return (
    <span className="info" tabIndex={0} aria-label="More information">
      i<span className={`bubble ${wide ? "wide" : ""}`} role="tooltip">{children}</span>
    </span>
  );
}

function Kpi({ label, value, detail, cls, tip }) {
  return (
    <div className="kpi">
      <div className="l">{label}{tip && <Info>{tip}</Info>}</div>
      <div className={`v ${cls || ""}`}>{value}</div>
      <div className="d">{detail}</div>
    </div>
  );
}

const signCls = (v) => (isNum(v) ? (v < 0 ? "down" : "up") : "dim");

function Banner({ snap, alertZ }) {
  if (!snap || snap.status !== "ok") return null;
  const level = zLevel(snap.z, alertZ);
  const z = isNum(snap.z) ? snap.z.toFixed(2) : "--";
  const text = {
    alert: ["UNWIND WARNING", `Crowded stocks are ${pct(-snap.spread, 2, false)} behind matched peers today, z = ${z}. Gaps this large are rare outside sell-offs that hit crowded positions.`],
    watch: ["WATCH", `Crowded stocks are lagging matched peers by ${pct(-snap.spread, 2, false)}, z = ${z}. Not yet at the alert level (${alertZ}).`],
    normal: ["NO UNUSUAL GAP", `Crowded minus peers: ${pct(snap.spread)} today, z = ${z}. Alert below z = ${alertZ}.`],
    unknown: ["NO BASELINE", "Not enough price history to judge how unusual today's gap is."],
  }[level];
  return (
    <div className={`banner ${level}`} role={level === "alert" ? "alert" : "status"}>
      <b>{text[0]}</b><span>{text[1]}</span>
    </div>
  );
}

function StockDetail({ s, range }) {
  if (!s) return <div className="detail dim">Hover a row to see the stock's matched peers.</div>;
  return (
    <div className="detail">
      <div><span className="tk">{s.ticker}</span> <span className="dim">{s.issuer || ""}</span></div>
      <div className="dim">
        Crowding score {isNum(s.score) ? s.score.toFixed(0) : "--"} · beta {isNum(s.beta) ? s.beta.toFixed(2) : "--"} ·
        {" "}{range}: <b className={signCls(s.ret)}>{pct(s.ret)}</b>, peers <b className={signCls(s.peers_ret)}>{pct(s.peers_ret)}</b>
      </div>
      <div className="peers">
        Matched peers (same size and beta group):{" "}
        {(s.peers || []).map((p) => (
          <span key={p.ticker}><b>{p.ticker}</b> <span className={signCls(p.ret)}>{pct(p.ret)}</span></span>
        ))}
      </div>
    </div>
  );
}

function BasketTable({ stocks, range }) {
  const rows = useMemo(() => [...stocks].sort((a, b) => (a.rel ?? 1) - (b.rel ?? 1)), [stocks]);
  const max = Math.max(0.01, ...rows.map((r) => Math.abs(r.rel ?? 0)));
  const [hot, setHot] = useState(null);
  const shown = rows.find((r) => r.ticker === hot) || null;
  return (
    <>
      <div className="scroll tablewrap" onMouseLeave={() => setHot(null)}>
    <table>
      <thead><tr>
        <th className="l">SYMBOL</th>
        <th>SCORE<Info>Crowding score, 0 to 100: how widely and heavily the tracked hedge funds hold the stock, scaled to the most crowded stock this quarter.</Info></th>
        <th>{range}<Info>The stock's return over the selected range.</Info></th>
        <th>PEERS<Info>Average return of its two matched peers over the same range.</Info></th>
        <th>VS PEERS<Info>Stock minus its peers. Negative means the crowded stock did worse than comparable stocks.</Info></th>
        <th className="l"></th>
      </tr></thead>
      <tbody>
        {rows.map((s) => (
          <tr key={s.ticker} className={hot === s.ticker ? "hot" : ""} tabIndex={0}
            onMouseEnter={() => setHot(s.ticker)} onFocus={() => setHot(s.ticker)}>
            <td className="l tk" title={s.issuer || ""}>{s.ticker}</td>
            <td>{isNum(s.score) ? s.score.toFixed(0) : "--"}</td>
            <td className={signCls(s.ret)}>{pct(s.ret)}</td>
            <td className={signCls(s.peers_ret)}>{pct(s.peers_ret)}</td>
            <td className={signCls(s.rel)} style={{ fontWeight: 700 }}>{pct(s.rel)}</td>
            <td className="l" style={{ minWidth: 56 }}>
              {isNum(s.rel) && <span className="relbar" style={{
                width: `${Math.round(48 * Math.abs(s.rel) / max)}px`,
                background: s.rel < 0 ? "var(--down)" : "var(--up)" }} />}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
      </div>
      <StockDetail s={shown} range={range} />
    </>
  );
}

export default function App() {
  const { meta, state, failed, now } = useApi();
  const snap = state?.snapshot;
  const ok = snap?.status === "ok";
  const fresh = failed ? "offline" : freshness(state?.last_update, state?.interval ?? 120, now);
  const alertZ = meta?.alert_z ?? -2;
  const [wanted, setWanted] = useState(loadRange);
  const available = ok ? RANGE_KEYS.filter((k) => snap.ranges?.[k]) : [];
  const range = pickRange(wanted, available);
  const view = ok ? snap.ranges?.[range] : null;
  const choose = (k) => { setWanted(k); saveRange(k); };

  return (
    <>
      <header className="menubar">
        <span className="brand"><b>SENTRY</b> <i>WARD</i></span>
        <span className="dim">13F CROWDING RESEARCH</span>
        <span className={`dot ${fresh}`}>{{ live: "LIVE", stale: "STALE", offline: "OFFLINE" }[fresh]}</span>
        <span className="dim">{ok ? `SESSION ${snap.session} · ${hhmm(snap.as_of)} ET` : "WAITING FOR QUOTES"}</span>
        <span className="push or outline" title="Quotes from Yahoo Finance arrive about 15 minutes late. Yahoo's smallest bar is 1 minute.">DELAYED ~15 MIN</span>
      </header>
      <nav className="tabs" aria-label="Screens">
        <a href="/" aria-current="page">LIVE</a>
        <a href="/dashboard">DASHBOARD</a>
      </nav>

      <main>
        <Banner snap={snap} alertZ={alertZ} />
        <div className="kpis">
          <Kpi label="CROWDED BASKET" value={ok ? pct(snap.crowded) : "--"} cls={ok ? signCls(snap.crowded) : "dim"}
            tip="Equal-weight return today, since yesterday's close, of the stocks most crowded among the tracked hedge funds in the latest 13F filings."
            detail={meta ? `top ${meta.crowded.length} by crowding score · ${meta.period}` : ""} />
          <Kpi label="MATCHED PEERS" value={ok ? pct(snap.peers) : "--"} cls={ok ? signCls(snap.peers) : "dim"}
            tip="Two comparison stocks per crowded stock, from the same market-size and beta group but not crowded. They show what the crowded stocks would have done without the crowding."
            detail={meta ? `${meta.n_peers} stocks, same size and beta` : ""} />
          <Kpi label="SPREAD" value={ok ? pct(snap.spread) : "--"} cls={ok ? signCls(snap.spread) : "dim"}
            tip="Crowded minus peers today. If funds sell crowded positions together, this turns negative before any filing shows it."
            detail="crowded minus peers, today" />
          <Kpi label="Z-SCORE" value={ok && isNum(snap.z) ? snap.z.toFixed(2) : "--"}
            tip={`Today's spread divided by its usual daily size (standard deviation over the last ${meta?.hist_days ?? 250} sessions). The warning shows at ${alertZ} or lower. It always uses today's move, whatever chart range is selected.`}
            cls={ok ? { alert: "down", watch: "or", normal: "", unknown: "dim" }[zLevel(snap.z, alertZ)] : "dim"}
            detail={meta && isNum(meta.sigma) ? `normal day: ±${pct(meta.sigma, 2, false)} (${meta.hist_days} days)` : ""} />
          <Kpi label="S&P 500 (SPY)" value={ok ? pct(snap.spy) : "--"} cls={ok ? signCls(snap.spy) : "dim"} detail="for reference"
            tip="The whole market today. A falling market with a flat spread is a broad sell-off, not a crowding unwind." />
        </div>

        {!ok && (
          <div className="panel"><div className="empty">
            {failed ? "Cannot reach the Sentry Ward server. Is `ward live` still running?"
              : state?.error ? `Quote source error: ${state.error}. Retrying every ${state.interval} s.`
              : "Fetching the first quotes. This takes up to a minute."}
          </div></div>
        )}

        {ok && (
          <div className="row">
            <div className="panel c-wide">
              <div className="ph">
                <h2>PRICE PATH</h2>
                <div className="ranges" role="group" aria-label="Chart range">
                  {RANGE_KEYS.map((k) => (
                    <button key={k} type="button" aria-pressed={k === range} disabled={!snap.ranges?.[k]}
                      title={snap.ranges?.[k] ? `${snap.ranges[k].since}, ${snap.ranges[k].bar}` : "Not available yet"}
                      onClick={() => choose(k)}>{k}</button>
                  ))}
                </div>
              </div>
              <div className="pb">
                <div className="since">Return {view?.since} · {view?.bar} · hover the chart for values</div>
                {view && <PathChart key={range} path={view.path} since={view.since} />}
                <div className="legend">
                  <span><i style={{ background: "#f59e0b" }} />CROWDED</span>
                  <span><i style={{ background: "#0891b2" }} />MATCHED PEERS</span>
                  <span><i style={{ background: "#8a8a8a" }} />SPY</span>
                  {view && <span>Coverage: {view.coverage.crowded}/{view.coverage.crowded_total} crowded, {view.coverage.peers}/{view.coverage.peers_total} peers quoted</span>}
                </div>
              </div>
            </div>
            <div className="panel c-side">
              <div className="ph"><h2>CROWDED VS PEERS · {range}</h2><small>worst first</small></div>
              <div className="pb">{view && <BasketTable stocks={view.stocks} range={range} />}</div>
            </div>
          </div>
        )}

        {meta && (
          <div className="panel">
            <div className="ph"><h2>DAILY SPREAD, LAST {meta.history.length} SESSIONS</h2><small>crowded minus matched peers · today in orange · hover a bar</small></div>
            <div className="pb">
              <HistoryChart history={meta.history} sigma={meta.sigma} alertZ={alertZ} today={ok ? snap.spread : null} />
              <div className="note">
                The alert fires when today's spread falls below {alertZ} times its normal daily size. In the pre-registered
                stress test, crowded stocks did not fall significantly more than matched peers in most sell-offs, so an
                alert here is a reason to look closer, not a forecast. Quotes: {meta.source}.
              </div>
            </div>
          </div>
        )}
      </main>

      <footer className="status">
        <span>SENTRY WARD LIVE</span>
        <span className="opt">HOLDINGS {meta?.period ?? "--"} · PRICES THROUGH {meta?.prices_through ?? "--"}</span>
        <span className="opt">UPDATED {state?.last_update ? new Date(state.last_update).toLocaleTimeString() : "--"} · EVERY {state?.interval ?? "--"} S</span>
        <span className={`end ${fresh === "live" ? "up" : fresh === "stale" ? "or" : "down"}`}>
          {fresh === "live" ? "READY" : fresh === "stale" ? "STALE" : "OFFLINE"}
        </span>
      </footer>
    </>
  );
}
