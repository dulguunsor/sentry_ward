import { useEffect, useRef, useState } from "react";
import { channelIdOf, pct, playerUrl, youtubeEmbed } from "./lib.js";

const KEY = { channels: "sentryward.tv.channels", idx: "sentryward.tv.idx", auto: "sentryward.tv.auto", open: "sentryward.tv.open" };
const load = (k, fallback) => { try { const v = localStorage.getItem(k); return v === null ? fallback : JSON.parse(v); } catch { return fallback; } };
const save = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage unavailable */ } };
const RECHECK_MS = 30 * 60e3;

function initialChannels() {
  const saved = load(KEY.channels, null);
  if (Array.isArray(saved)) return saved;
  try {   // a single link saved by the earlier version of this panel
    const old = localStorage.getItem("sentryward.video");
    if (old && youtubeEmbed(old)) return [{ name: "Channel 1", link: old, embed: youtubeEmbed(old), channelId: channelIdOf(old) }];
  } catch { /* storage unavailable */ }
  return [];
}

/**
 * A small TV: your own list of YouTube live streams with channel up and down, played through
 * YouTube's embedded player. With a YouTube key on the server it also looks for recent videos
 * about a crowded stock that has news and is moving unusually, and can switch to them.
 */
export function TvPanel({ triggers = [] }) {
  const [channels, setChannels] = useState(initialChannels);
  const [idx, setIdx] = useState(() => load(KEY.idx, 0));
  const [auto, setAuto] = useState(() => load(KEY.auto, false));
  const [open, setOpen] = useState(() => load(KEY.open, true));
  const [enabled, setEnabled] = useState(false);
  const [override, setOverride] = useState(null);   // a video playing instead of the channel
  const [offer, setOffer] = useState(null);         // a relevant video found, not playing yet
  const [osd, setOsd] = useState(null);
  const [err, setErr] = useState(null);
  const [draft, setDraft] = useState({ link: "", name: "" });
  const [formErr, setFormErr] = useState(null);
  const frame = useRef(null);
  const checked = useRef(new Map());
  const seen = useRef(new Set());

  const n = channels.length;
  const cur = n ? channels[((idx % n) + n) % n] : null;
  const src = playerUrl(override ? youtubeEmbed(override.video_id) : cur?.embed, window.location.origin);

  useEffect(() => { save(KEY.channels, channels); }, [channels]);
  useEffect(() => { save(KEY.idx, idx); }, [idx]);
  useEffect(() => { save(KEY.auto, auto); }, [auto]);
  useEffect(() => { save(KEY.open, open); }, [open]);
  useEffect(() => {
    fetch("/api/tv/status").then((r) => r.json()).then((s) => setEnabled(!!s.enabled)).catch(() => setEnabled(false));
  }, []);
  useEffect(() => { if (!osd) return undefined; const t = setTimeout(() => setOsd(null), 2500); return () => clearTimeout(t); }, [osd]);

  const tune = (i) => {
    if (!n) return;
    const k = ((i % n) + n) % n;
    setIdx(k); setOverride(null); setErr(null);
    setOsd(`CH ${k + 1} · ${channels[k].name}`);
  };

  // Hear from YouTube's player: when a switched-to video ends, go back to the channel; if a
  // stream can't play here, say so.
  useEffect(() => {
    const on = (e) => {
      if (!/youtube(-nocookie)?\.com$/.test(new URL(e.origin).hostname) || e.source !== frame.current?.contentWindow) return;
      let m;
      try { m = typeof e.data === "string" ? JSON.parse(e.data) : e.data; } catch { return; }
      const state = m.event === "onStateChange" ? m.info : m.event === "infoDelivery" ? m.info?.playerState : undefined;
      if (state === 0 && override) { setOverride(null); setOsd("BACK TO LIVE"); }
      if (m.event === "onError") setErr("This stream can't play here. It may be off air or may not allow embedding.");
    };
    window.addEventListener("message", on);
    return () => window.removeEventListener("message", on);
  }, [override]);
  const listen = () => {
    setErr(null);
    try { frame.current?.contentWindow?.postMessage(JSON.stringify({ event: "listening", id: 1, channel: "widget" }), "https://www.youtube.com"); }
    catch { /* the player may not be ready */ }
  };

  // Look for a relevant video when a crowded stock has fresh news and an unusual move.
  const channelIds = channels.map((c) => c.channelId).filter(Boolean);
  const top = triggers[0];
  useEffect(() => {
    if (!enabled || !top || !channelIds.length) return;
    const last = checked.current.get(top.ticker);
    if (last && Date.now() - last < RECHECK_MS) return;
    checked.current.set(top.ticker, Date.now());
    const q = new URLSearchParams({ issuer: top.issuer || "", ticker: top.ticker, channels: channelIds.join(",") });
    fetch(`/api/tv/related?${q}`).then((r) => r.json()).then((js) => {
      const v = (js.videos || []).find((x) => !seen.current.has(x.video_id));
      if (!v) return;
      seen.current.add(v.video_id);
      const found = { ...v, ticker: top.ticker, rel: top.rel, headline: top.headline };
      if (auto) { setOverride(found); setOsd(`${top.ticker} · RELEVANT VIDEO`); } else setOffer(found);
    }).catch(() => {});
  }, [enabled, top?.ticker, channelIds.join(","), auto]);   // eslint-disable-line react-hooks/exhaustive-deps

  const add = async (e) => {
    e.preventDefault();
    setFormErr(null);
    const link = draft.link.trim();
    let entry = null;
    if (youtubeEmbed(link)) {
      entry = { name: draft.name.trim() || `Channel ${n + 1}`, link, embed: youtubeEmbed(link), channelId: channelIdOf(link) };
    } else if (/@[\w.-]+/.test(link)) {
      if (!enabled) { setFormErr("Adding a channel by @name needs YOUTUBE_API_KEY on the server. Paste the channel's live video link instead."); return; }
      try {
        const js = await (await fetch(`/api/tv/resolve?url=${encodeURIComponent(link)}`)).json();
        if (!js.channel_id) { setFormErr(js.error || "Channel not found."); return; }
        entry = { name: draft.name.trim() || js.name, link, embed: youtubeEmbed(js.channel_id), channelId: js.channel_id };
      } catch { setFormErr("Could not reach the server."); return; }
    } else { setFormErr("Paste a YouTube live video link or a channel link."); return; }
    setChannels((cs) => [...cs, entry]);
    setIdx(n); setOverride(null); setOsd(`CH ${n + 1} · ${entry.name}`);
    setDraft({ link: "", name: "" });
  };
  const remove = (i) => {
    setChannels((cs) => cs.filter((_, k) => k !== i));
    if (i <= idx) setIdx((k) => Math.max(0, k - 1));
  };

  return (
    <div className="panel c-side tv" tabIndex={-1}
      onKeyDown={(e) => { if (e.target.tagName === "INPUT") return; if (e.key === "ArrowUp") { e.preventDefault(); tune(idx + 1); } if (e.key === "ArrowDown") { e.preventDefault(); tune(idx - 1); } }}>
      <div className="ph">
        <h2>LIVE TV</h2>
        <span className="chname dim">{override ? `${override.ticker} · VIDEO` : cur ? `CH ${idx % n + 1} · ${cur.name}` : "NO CHANNELS"}</span>
        <div className="ranges">
          <button type="button" disabled={n < 2} onClick={() => tune(idx - 1)} title="Previous channel (↓)">CH ▼</button>
          <button type="button" disabled={n < 2} onClick={() => tune(idx + 1)} title="Next channel (↑)">CH ▲</button>
          {enabled && (
            <button type="button" aria-pressed={auto} onClick={() => setAuto((a) => !a)}
              title="Switch automatically to a recent video when a crowded stock has news and an unusual move">AUTO</button>
          )}
          <button type="button" onClick={() => setOpen((o) => !o)}>{open ? "HIDE" : "SHOW"}</button>
        </div>
      </div>
      {open && (
        <div className="pb">
          {offer && !override && (
            <div className="breaking">
              <b>{offer.ticker} {pct(offer.rel)} vs peers</b>
              <span className="btitle">{offer.title}</span>
              <span className="faint">{offer.channel}</span>
              <span className="bact">
                <button type="button" className="more" onClick={() => { setOverride(offer); setOffer(null); }}>WATCH</button>
                <button type="button" className="more" onClick={() => setOffer(null)}>DISMISS</button>
              </span>
            </div>
          )}
          {override && (
            <div className="breaking on">
              <b>{override.ticker}</b><span className="btitle">{override.title}</span>
              <span className="bact"><button type="button" className="more" onClick={() => { setOverride(null); setOsd("BACK TO LIVE"); }}>BACK TO LIVE</button></span>
            </div>
          )}
          {src ? (
            <div className="video">
              <iframe ref={frame} key={src} src={src} onLoad={listen} title="Live TV"
                allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowFullScreen
                referrerPolicy="strict-origin-when-cross-origin" />
              {osd && <div className="osd">{osd}</div>}
              {err && (
                <div className="offair">
                  <div>{err}</div>
                  {n > 1 && <button type="button" className="more" onClick={() => tune(idx + 1)}>NEXT CHANNEL ▲</button>}
                </div>
              )}
            </div>
          ) : (
            <div className="empty">Add a channel below: a news channel's YouTube live link. It starts muted.</div>
          )}
          {n > 0 && (
            <ol className="chlist">
              {channels.map((c, i) => (
                <li key={`${c.link}${i}`} className={i === idx % n && !override ? "on" : ""}>
                  <button type="button" className="chbtn" onClick={() => tune(i)}>{i + 1}. {c.name}</button>
                  <button type="button" className="x" aria-label={`Remove ${c.name}`} onClick={() => remove(i)}>×</button>
                </li>
              ))}
            </ol>
          )}
          <form className="vform" onSubmit={add}>
            <input type="url" value={draft.link} onChange={(e) => setDraft({ ...draft, link: e.target.value })}
              placeholder={enabled ? "YouTube live link or channel (@name)" : "YouTube live link"} aria-label="Channel link" />
            <input type="text" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              placeholder="Name" aria-label="Channel name" className="short" />
            <button type="submit" className="more">ADD</button>
          </form>
          {formErr && <div className="note down">{formErr}</div>}
          <div className="note">
            Plays through YouTube's player with each channel's own branding and ads. Use ▲ ▼ or the arrow keys to change
            channel.{enabled
              ? " With AUTO on, a recent video from your channels about a crowded stock with news and an unusual move plays by itself, then it returns to live. Only channels added by channel link or @name are searched."
              : " Set YOUTUBE_API_KEY on the server to add channels by @name and find relevant videos automatically."}
          </div>
        </div>
      )}
    </div>
  );
}
