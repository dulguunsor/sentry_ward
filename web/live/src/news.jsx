import { useState } from "react";
import { nyTime, stockHref, youtubeEmbed } from "./lib.js";

const STATUS = {
  live: ["LIVE", "up"], starting: ["CONNECTING", "or"], reconnecting: ["RECONNECTING", "or"], error: ["ERROR", "down"],
};

/** Headlines as they arrive: newest first, with the outlet's or Claude's short summary and a link. */
export function NewsPanel({ news }) {
  const [all, setAll] = useState(false);
  if (!news) {
    return (
      <div className="panel c-wide">
        <div className="ph"><h2>LIVE NEWS</h2><small className="dim">off</small></div>
        <div className="empty">
          Live news needs a free Alpaca account. Set <code>APCA_API_KEY_ID</code> and <code>APCA_API_SECRET_KEY</code>,
          then restart <code>ward live</code>.
        </div>
      </div>
    );
  }
  const items = all ? news.items : news.items.filter((n) => n.tracked?.length);
  const [label, cls] = STATUS[news.status] || [news.status?.toUpperCase() || "--", "dim"];
  return (
    <div className="panel c-wide">
      <div className="ph">
        <h2>LIVE NEWS</h2>
        <span className={`dot ${cls === "up" ? "live" : cls === "or" ? "stale" : "offline"}`}>{label}</span>
        <div className="ranges" role="group" aria-label="Which news">
          <button type="button" aria-pressed={!all} onClick={() => setAll(false)}>TRACKED STOCKS</button>
          <button type="button" aria-pressed={all} onClick={() => setAll(true)}>ALL</button>
        </div>
      </div>
      <div className="pb">
        {news.error && <div className="note down">{news.error}</div>}
        <ul className="news">
          {items.length === 0 && (
            <li className="dim">{all ? "No headlines yet." : "No headlines about the tracked stocks yet. They appear here the moment they are published."}</li>
          )}
          {items.map((n) => (
            <li key={n.id} className={n.tracked?.length ? "tracked" : ""}>
              <div className="nmeta">
                <span className="ntime">{nyTime(n.created_at)}</span>
                <span className="nsrc">{n.source}</span>
                {(n.symbols || []).slice(0, 6).map((s) => (
                  (n.tracked || []).includes(s)
                    ? <a key={s} className="chip on" href={stockHref(s)}>{s}</a>
                    : <span key={s} className="chip">{s}</span>
                ))}
              </div>
              <div className="nhead">
                {n.url ? <a href={n.url} target="_blank" rel="noopener noreferrer">{n.headline}</a> : n.headline}
              </div>
              {n.summary && (
                <div className="nsum">
                  {n.summary_by === "ai" && <span className="aitag" title="Written by Claude from the article; check the source for details">AI SUMMARY</span>}
                  {n.summary}
                </div>
              )}
            </li>
          ))}
        </ul>
        <div className="note">
          Headlines from Alpaca (sources such as Benzinga), times in New York. Only the headline, a short summary and a
          link are shown; open the link for the full article.{news.summaries ? " Items without the outlet's summary get one written by Claude." : ""}
          {" "}News next to a price move may or may not be the reason for it.
        </div>
      </div>
    </div>
  );
}

const VIDEO_KEY = "sentryward.video";
const loadVideo = () => { try { return localStorage.getItem(VIDEO_KEY) || ""; } catch { return ""; } };
const saveVideo = (v) => { try { localStorage.setItem(VIDEO_KEY, v); } catch { /* storage unavailable */ } };

/** A news channel's official live stream, played through YouTube's own embedded player. */
export function VideoPanel() {
  const [link, setLink] = useState(loadVideo);
  const [draft, setDraft] = useState(link);
  const [open, setOpen] = useState(true);
  const src = youtubeEmbed(link);
  const bad = draft.trim() && !youtubeEmbed(draft);
  const apply = (e) => { e.preventDefault(); if (!bad) { setLink(draft.trim()); saveVideo(draft.trim()); } };
  return (
    <div className="panel c-side">
      <div className="ph">
        <h2>LIVE TV</h2>
        <button type="button" className="more" onClick={() => setOpen((o) => !o)}>{open ? "HIDE" : "SHOW"}</button>
      </div>
      {open && (
        <div className="pb">
          <form className="vform" onSubmit={apply}>
            <input type="url" value={draft} onChange={(e) => setDraft(e.target.value)} aria-label="YouTube live link"
              placeholder="Paste a YouTube live stream link" />
            <button type="submit" className="more" disabled={!!bad}>PLAY</button>
          </form>
          {bad && <div className="note down">That doesn't look like a YouTube video or channel link.</div>}
          {src ? (
            <div className="video">
              <iframe src={src} title="Live news stream" allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
                allowFullScreen referrerPolicy="strict-origin-when-cross-origin" />
            </div>
          ) : (
            <div className="empty">
              Open a financial news channel's live stream on YouTube, copy its link and paste it here. It starts muted.
            </div>
          )}
          <div className="note">
            Plays through YouTube's player, with the channel's own branding and ads. Some channels don't allow
            embedding; if the player says so, use another stream. Your choice is remembered in this browser.
          </div>
        </div>
      )}
    </div>
  );
}
