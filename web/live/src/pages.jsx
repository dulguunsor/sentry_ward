import { useEffect, useRef, useState } from "react";
import { LineChart } from "./charts.jsx";
import { fundHref, isNum, num, pct, stockHref, usd } from "./lib.js";

const ORANGE = "#f59e0b", CYAN = "#0891b2";
const signCls = (v) => (isNum(v) ? (v < 0 ? "down" : "up") : "dim");
const CHANGE_CLS = { new: "up", added: "up", trimmed: "down", "sold out": "down", unchanged: "dim", "first filing": "dim" };
const quarter = (p) => (p ? `${p.slice(0, 4)} Q${Math.ceil(Number(p.slice(5, 7)) / 3)}` : "--");

/** Loads JSON from the API whenever `url` changes. */
function useJson(url) {
  const [res, setRes] = useState({ loading: true });
  useEffect(() => {
    if (!url) return undefined;
    let alive = true;
    setRes({ loading: true });
    fetch(url)
      .then(async (r) => ({ ok: r.ok, body: await r.json().catch(() => ({})) }))
      .then(({ ok, body }) => alive && setRes(ok ? { data: body } : { error: body.error || "Request failed." }))
      .catch(() => alive && setRes({ error: "Cannot reach the Sentry Ward server." }));
    return () => { alive = false; };
  }, [url]);
  return res;
}

function Status({ res, what }) {
  if (res.loading) return <div className="panel"><div className="empty">Loading {what}…</div></div>;
  return (
    <div className="panel"><div className="empty">
      {res.error === "Not found." ? `No ${what} found. Try the search box.` : res.error}
      <br /><a href="#/" className="back">← BACK TO LIVE</a>
    </div></div>
  );
}

const Stat = ({ label, value, detail, cls }) => (
  <div className="kpi">
    <div className="l">{label}</div>
    <div className={`v ${cls || ""}`}>{value}</div>
    <div className="d">{detail}</div>
  </div>
);

const Change = ({ c }) => <span className={`chg ${CHANGE_CLS[c] || "dim"}`}>{c}</span>;

// ------------------------------------------------------------------ search

export function SearchBox() {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [hits, setHits] = useState({ stocks: [], funds: [] });
  const [active, setActive] = useState(0);
  const box = useRef(null);

  useEffect(() => {
    const term = q.trim();
    if (!term) { setHits({ stocks: [], funds: [] }); return undefined; }
    let alive = true;
    const id = setTimeout(() => {
      fetch(`/api/search?q=${encodeURIComponent(term)}`)
        .then((r) => (r.ok ? r.json() : { stocks: [], funds: [] }))
        .then((h) => { if (alive) { setHits(h); setActive(0); } })
        .catch(() => {});
    }, 150);
    return () => { alive = false; clearTimeout(id); };
  }, [q]);

  useEffect(() => {
    const close = (e) => { if (box.current && !box.current.contains(e.target)) setOpen(false); };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, []);

  const items = [
    ...hits.stocks.map((s) => ({ href: stockHref(s.key), main: s.ticker || s.issuer_id, sub: s.issuer, tag: isNum(s.score) ? `score ${s.score.toFixed(0)}` : `${s.n_holders} fund${s.n_holders === 1 ? "" : "s"}` })),
    ...hits.funds.map((f) => ({ href: fundHref(f.fund), main: f.fund, sub: f.style, tag: "fund" })),
  ];
  const go = (it) => { window.location.hash = it.href; setQ(""); setOpen(false); };
  const onKey = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(items.length - 1, a + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(0, a - 1)); }
    else if (e.key === "Enter" && items[active]) go(items[active]);
    else if (e.key === "Escape") setOpen(false);
  };

  return (
    <div className="search" ref={box}>
      <input type="search" value={q} placeholder="Search ticker, company or fund" aria-label="Search stocks and funds"
        onChange={(e) => { setQ(e.target.value); setOpen(true); }} onFocus={() => setOpen(true)} onKeyDown={onKey} />
      {open && q.trim() && (
        <ul className="results" role="listbox">
          {items.length === 0 && <li className="dim none">No matches</li>}
          {items.map((it, i) => (
            <li key={it.href} role="option" aria-selected={i === active} className={i === active ? "on" : ""}
              onPointerEnter={() => setActive(i)} onPointerDown={(e) => { e.preventDefault(); go(it); }}>
              <b>{it.main}</b><span className="sub">{it.sub}</span><span className="tag">{it.tag}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ stock page

export function StockPage({ id }) {
  const res = useJson(`/api/stock/${encodeURIComponent(id)}`);
  if (!res.data) return <Status res={res} what="stock" />;
  const s = res.data, n = s.now;
  const hist = s.history.map((h) => ({ t: quarter(h.period), score: h.score, note: `${h.n_holders} of ${h.n_funds} funds` }));
  const own = s.history.map((h) => ({ t: quarter(h.period), own: h.hf_ownership, dte: h.days_to_exit }));
  const hasOwn = own.some((o) => isNum(o.own));
  return (
    <>
      <div className="pagehead">
        <a href="#/" className="back">← LIVE</a>
        <h1><span className="or">{s.ticker || s.issuer_id}</span> {s.issuer}</h1>
        <span className="dim">CUSIP {s.cusip} · holdings {quarter(s.period)}</span>
      </div>
      <div className="kpis">
        <Stat label="CROWDING SCORE" value={n ? n.score.toFixed(0) : "--"} cls="or"
          detail={n ? `rank ${n.rank} of ${n.n_ranked}${isNum(n.score_change) ? ` · ${n.score_change >= 0 ? "+" : ""}${n.score_change.toFixed(1)} vs last quarter` : ""}` : "held by fewer than 2 tracked funds"} />
        <Stat label="FUNDS HOLDING" value={`${s.holders.length} / ${s.n_funds_filed}`} detail="tracked funds that filed this quarter" />
        <Stat label="VALUE HELD" value={usd(n?.total_value_usd ?? s.holders.reduce((a, h) => a + (h.value_usd || 0), 0))} detail="by the tracked funds" />
        <Stat label="HEDGE FUND OWNERSHIP" value={n && isNum(n.hf_ownership) ? pct(n.hf_ownership, 1, false) : "--"} detail="share of the company they own" />
        <Stat label="DAYS TO EXIT" value={n && isNum(n.days_to_exit) ? num(n.days_to_exit, 1) : "--"}
          cls={n && n.days_to_exit > 5 ? "down" : ""} detail="selling at 20% of daily volume" />
      </div>
      <div className="row">
        <div className="panel c-wide">
          <div className="ph"><h2>CROWDING SCORE BY QUARTER</h2><small>0 to 100 · hover for details</small></div>
          <div className="pb">
            <LineChart data={hist} series={[{ k: "score", label: "Score", color: ORANGE }]} fmt={(v) => v.toFixed(0)}
              domain={[0, 100]} label="Crowding score by quarter" />
            {hasOwn && (
              <LineChart data={own} height={170} series={[{ k: "own", label: "Hedge fund ownership", color: CYAN }]}
                fmt={(v) => pct(v, 1, false)} label="Hedge fund ownership by quarter" />
            )}
          </div>
        </div>
        <div className="panel c-side">
          <div className="ph"><h2>WHO HOLDS IT</h2><small>vs each fund's previous filing</small></div>
          <div className="pb scroll">
            <table>
              <thead><tr><th className="l">FUND</th><th>VALUE</th><th>WEIGHT</th><th>SHARES</th><th className="l">CHANGE</th></tr></thead>
              <tbody>
                {s.holders.map((h) => (
                  <tr key={h.fund}>
                    <td className="l"><a className="tk" href={fundHref(h.fund)}>{h.fund}</a></td>
                    <td>{usd(h.value_usd)}</td>
                    <td>{pct(h.weight, 2, false)}</td>
                    <td title={isNum(h.prev_shares) ? `${num(h.prev_shares)} shares in ${quarter(h.prev_period)}` : ""}>{num(h.shares)}</td>
                    <td className="l"><Change c={h.change} /></td>
                  </tr>
                ))}
                {s.sold_out.map((h) => (
                  <tr key={h.fund} className="faded">
                    <td className="l"><a className="tk" href={fundHref(h.fund)}>{h.fund}</a></td>
                    <td className="dim">{usd(h.prev_value_usd)}</td><td className="dim">--</td>
                    <td className="dim">0</td><td className="l"><Change c="sold out" /></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="note">Weight is the stock's share of the fund's stock holdings. Hover the shares to see the previous filing.</div>
          </div>
        </div>
      </div>
    </>
  );
}

// ------------------------------------------------------------------ fund page

export function FundPage({ id }) {
  const res = useJson(`/api/fund/${encodeURIComponent(id)}`);
  const [showAll, setShowAll] = useState(false);
  if (!res.data) return <Status res={res} what="fund" />;
  const f = res.data, n = f.now;
  if (!n) return <div className="panel"><div className="empty">{f.fund} has no holdings in the database.</div></div>;
  const hist = f.history.map((h) => ({ t: quarter(h.period), value: h.total_value_usd, note: `${h.n_stocks} stocks` }));
  const act = f.history.map((h) => ({ t: quarter(h.period), active: h.active_share }));
  const rows = showAll ? f.holdings : f.holdings.slice(0, 25);
  const behind = f.latest_period && f.period !== f.latest_period;
  const c = f.changes;
  return (
    <>
      <div className="pagehead">
        <a href="#/" className="back">← LIVE</a>
        <h1><span className="or">{f.fund}</span></h1>
        <span className="dim">{f.style} · filed as {(f.filers || []).join(", ")} · holdings {quarter(f.period)}</span>
      </div>
      {behind && <div className="banner watch"><b>NOT FILED YET</b><span>The latest filing is for {quarter(f.period)}; other funds have filed {quarter(f.latest_period)}.</span></div>}
      <div className="kpis">
        <Stat label="13F VALUE" value={usd(n.total_value_usd)} detail="long US positions reported" />
        <Stat label="STOCKS" value={num(n.n_stocks)} detail={f.etf.n ? `plus ${f.etf.n} ETFs (${usd(f.etf.value_usd)})` : "no ETFs"} />
        <Stat label="ACTIVE SHARE" value={pct(n.active_share, 0, false)} cls="or" detail="how far from the 13F market it is" />
        <Stat label="CHANGES" value={`+${c.new} / −${c.closed}`} detail={`new / closed since ${quarter(f.prev_period)} · ${c.added} added, ${c.trimmed} trimmed`} />
        <Stat label="CLOSEST FUND" value={f.overlap[0] ? f.overlap[0].fund : "--"} detail={f.overlap[0] ? `${pct(f.overlap[0].overlap, 0, false)} of holdings in common` : ""} />
      </div>
      <div className="row">
        <div className="panel c-wide">
          <div className="ph"><h2>HOLDINGS</h2><small>{f.n_holdings} stocks · largest first · vs {quarter(f.prev_period)}</small></div>
          <div className="pb scroll">
            <table>
              <thead><tr>
                <th className="l">SYMBOL</th><th className="l">COMPANY</th><th>VALUE</th><th>WEIGHT</th><th>VS MARKET</th>
                <th>CROWDING</th><th className="l">CHANGE</th>
              </tr></thead>
              <tbody>
                {rows.map((h) => (
                  <tr key={h.issuer_id}>
                    <td className="l"><a className="tk" href={stockHref(h.key)}>{h.ticker || h.issuer_id}</a></td>
                    <td className="l nm" title={h.issuer}>{h.issuer}</td>
                    <td>{usd(h.value_usd)}</td>
                    <td title={isNum(h.prev_weight) ? `${pct(h.prev_weight, 2, false)} in ${quarter(f.prev_period)}` : ""}>{pct(h.weight, 2, false)}</td>
                    <td className={signCls(h.weight - (h.market_weight ?? 0))}
                      title={`Its weight in all 13F holdings: ${pct(h.market_weight, 2, false)}`}>{pct(h.weight - (h.market_weight ?? 0), 2)}</td>
                    <td>{isNum(h.score) ? h.score.toFixed(0) : <span className="dim">--</span>}</td>
                    <td className="l"><Change c={h.change} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {f.holdings.length > 25 && (
              <button type="button" className="more" onClick={() => setShowAll((v) => !v)}>
                {showAll ? "Show top 25" : `Show all ${f.holdings.length}`}
              </button>
            )}
            <div className="note">
              VS MARKET is the fund's weight minus the stock's weight across all 13F filers. CROWDING is blank for
              stocks held by fewer than two tracked funds. Hover a weight to see the previous filing.
            </div>
          </div>
        </div>
        <div className="c-side col">
          <div className="panel">
            <div className="ph"><h2>MOST SIMILAR FUNDS</h2><small>share of holdings in common</small></div>
            <div className="pb">
              <table>
                <thead><tr><th className="l">FUND</th><th>OVERLAP</th><th>ACTIVE</th><th>SHARED</th></tr></thead>
                <tbody>
                  {f.overlap.slice(0, 10).map((o) => (
                    <tr key={o.fund}>
                      <td className="l"><a className="tk" href={fundHref(o.fund)}>{o.fund}</a></td>
                      <td>{pct(o.overlap, 0, false)}</td>
                      <td title="Overlap of the bets each fund makes beyond the market">{pct(o.active_overlap, 0, false)}</td>
                      <td>{o.shared_stocks}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          {f.closed.length > 0 && (
            <div className="panel">
              <div className="ph"><h2>CLOSED SINCE {quarter(f.prev_period)}</h2><small>largest first</small></div>
              <div className="pb">
                <table><tbody>
                  {f.closed.slice(0, 12).map((h) => (
                    <tr key={h.issuer_id}>
                      <td className="l"><a className="tk" href={stockHref(h.key)}>{h.ticker || h.issuer_id}</a></td>
                      <td className="l nm" title={h.issuer}>{h.issuer}</td>
                      <td className="dim">{usd(h.prev_value_usd)}</td>
                    </tr>
                  ))}
                </tbody></table>
              </div>
            </div>
          )}
        </div>
      </div>
      <div className="panel">
        <div className="ph"><h2>OVER TIME</h2><small>hover for values</small></div>
        <div className="pb two">
          <div>
            <div className="since">13F VALUE · long US positions at each quarter end</div>
            <LineChart data={hist} width={560} height={230} series={[{ k: "value", label: "13F value", color: ORANGE }]} fmt={usd} label="13F value by quarter" />
          </div>
          <div>
            <div className="since">ACTIVE SHARE · 0% means the fund looks like the whole 13F market</div>
            <LineChart data={act} width={560} height={230} series={[{ k: "active", label: "Active share", color: CYAN }]} fmt={(v) => pct(v, 0, false)}
              domain={[0, 1]} label="Active share by quarter" />
          </div>
        </div>
      </div>
    </>
  );
}
