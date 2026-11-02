import { useEffect, useMemo, useState } from "react";
import { HistoryChart, IntradayChart } from "./charts.jsx";
import { freshness, hhmm, isNum, pct, zLevel } from "./lib.js";

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

function Kpi({ label, value, detail, cls }) {
  return (
    <div className="kpi">
      <div className="l">{label}</div>
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

function BasketTable({ stocks }) {
  const rows = useMemo(() => [...stocks].sort((a, b) => (a.rel ?? 1) - (b.rel ?? 1)), [stocks]);
  const max = Math.max(0.01, ...rows.map((r) => Math.abs(r.rel ?? 0)));
  return (
    <table>
      <thead><tr>
        <th className="l">SYMBOL</th><th className="l">NAME</th><th>SCORE</th><th>TODAY</th><th>PEERS</th><th>VS PEERS</th><th className="l"></th>
      </tr></thead>
      <tbody>
        {rows.map((s) => (
          <tr key={s.ticker}>
            <td className="l tk">{s.ticker}</td>
            <td className="l nm" title={s.issuer || ""}>{s.issuer || ""}</td>
            <td>{isNum(s.score) ? s.score.toFixed(0) : "--"}</td>
            <td className={signCls(s.ret)}>{pct(s.ret)}</td>
            <td className={signCls(s.peers_ret)}>{pct(s.peers_ret)}</td>
            <td className={signCls(s.rel)} style={{ fontWeight: 700 }}>{pct(s.rel)}</td>
            <td className="l" style={{ minWidth: 120 }}>
              {isNum(s.rel) && <span className="relbar" style={{
                width: `${Math.round(100 * Math.abs(s.rel) / max)}px`,
                background: s.rel < 0 ? "var(--down)" : "var(--up)" }} />}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function App() {
  const { meta, state, failed, now } = useApi();
  const snap = state?.snapshot;
  const ok = snap?.status === "ok";
  const fresh = failed ? "offline" : freshness(state?.last_update, state?.interval ?? 120, now);
  const alertZ = meta?.alert_z ?? -2;

  return (
    <>
      <header className="menubar">
        <span className="brand"><b>SENTRY</b> <i>WARD</i></span>
        <span className="dim">13F CROWDING RESEARCH</span>
        <span className={`dot ${fresh}`}>{{ live: "LIVE", stale: "STALE", offline: "OFFLINE" }[fresh]}</span>
        <span className="dim">{ok ? `SESSION ${snap.session} · ${hhmm(snap.as_of)} ET` : "WAITING FOR QUOTES"}</span>
        <span className="push or outline">DELAYED ~15 MIN</span>
      </header>
      <nav className="tabs" aria-label="Screens">
        <a href="/" aria-current="page">LIVE</a>
        <a href="/dashboard">DASHBOARD</a>
      </nav>

      <main>
        <Banner snap={snap} alertZ={alertZ} />
        <div className="kpis">
          <Kpi label="CROWDED BASKET" value={ok ? pct(snap.crowded) : "--"} cls={ok ? signCls(snap.crowded) : "dim"}
            detail={meta ? `top ${meta.crowded.length} by crowding score · ${meta.period}` : ""} />
          <Kpi label="MATCHED PEERS" value={ok ? pct(snap.peers) : "--"} cls={ok ? signCls(snap.peers) : "dim"}
            detail={meta ? `${meta.n_peers} stocks, same size and beta` : ""} />
          <Kpi label="SPREAD" value={ok ? pct(snap.spread) : "--"} cls={ok ? signCls(snap.spread) : "dim"}
            detail="crowded minus peers, today" />
          <Kpi label="Z-SCORE" value={ok && isNum(snap.z) ? snap.z.toFixed(2) : "--"}
            cls={ok ? { alert: "down", watch: "or", normal: "", unknown: "dim" }[zLevel(snap.z, alertZ)] : "dim"}
            detail={meta && isNum(meta.sigma) ? `normal day: ±${pct(meta.sigma, 2, false)} (${meta.hist_days} days)` : ""} />
          <Kpi label="S&P 500 (SPY)" value={ok ? pct(snap.spy) : "--"} cls={ok ? signCls(snap.spy) : "dim"} detail="for reference" />
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
              <div className="ph"><h2>TODAY</h2><small>return since yesterday's close</small></div>
              <div className="pb">
                <IntradayChart path={snap.path} />
                <div className="legend">
                  <span><i style={{ background: "#f59e0b" }} />CROWDED</span>
                  <span><i style={{ background: "#0891b2" }} />MATCHED PEERS</span>
                  <span><i style={{ background: "#8a8a8a" }} />SPY</span>
                  <span>Coverage: {snap.coverage.crowded}/{snap.coverage.crowded_total} crowded, {snap.coverage.peers}/{snap.coverage.peers_total} peers quoted</span>
                </div>
              </div>
            </div>
            <div className="panel c-side">
              <div className="ph"><h2>CROWDED VS PEERS</h2><small>worst first</small></div>
              <div className="pb"><div className="scroll tablewrap"><BasketTable stocks={snap.stocks} /></div></div>
            </div>
          </div>
        )}

        {meta && (
          <div className="panel">
            <div className="ph"><h2>DAILY SPREAD, LAST {meta.history.length} SESSIONS</h2><small>crowded minus matched peers · today in orange</small></div>
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
