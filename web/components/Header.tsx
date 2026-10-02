"use client";

import { useState } from "react";
import { fmtP } from "../lib/game.ts";
import { useGame, useNow } from "../lib/stream.tsx";

export function Header() {
  const g = useGame();
  const now = useNow();
  const s = g.state;
  const [draft, setDraft] = useState<string | null>(null);
  const left = g.clockAt ? Math.max(0, s.tickSeconds - (now - g.clockAt) / 1000) : s.tickSeconds;
  const fill = s.tickSeconds ? 100 * (1 - left / s.tickSeconds) : 0;
  return (
    <header className="top">
      <div className="brand">
        <span className="logo">BAZAAR</span>
        <span className="team">{s.name || s.team || "—"}{s.team && s.name ? ` · ${s.team}` : ""}</span>
      </div>
      <div className="clock" title={`${Math.ceil(left)} s to the next tick`}>
        <span className="day">{s.day || "—"}</span>
        <span className="tick">tick {s.tick}</span>
        <span className="tickbar" aria-hidden="true"><span style={{ width: `${fill}%` }} /></span>
      </div>
      <dl className="kpis">
        <div><dt>cash</dt><dd>{fmtP(s.cash)}</dd></div>
        <div><dt>score</dt><dd>{s.score.score ?? "—"}</dd></div>
        <div><dt>rank</dt><dd>{s.score.rank != null ? `#${s.score.rank}` : "—"}</dd></div>
      </dl>
      <form
        className="ws"
        onSubmit={(ev) => {
          ev.preventDefault();
          if (draft) g.setUrl(draft.trim());
          setDraft(null);
        }}
      >
        <span className={`dot ${g.status === "live" ? "live" : g.status === "connecting" ? "wait" : "down"}`} />
        <span className="wsstatus">{g.status}</span>
        <input
          spellCheck={false}
          aria-label="WebSocket URL"
          value={draft ?? g.url}
          onChange={(ev) => setDraft(ev.target.value)}
          onBlur={() => setDraft(null)}
        />
      </form>
      <button type="button" className={g.paused ? "on" : undefined} onClick={() => g.setPaused(!g.paused)} title="Freeze the view">
        {g.paused ? "Resume" : "Pause"}
      </button>
    </header>
  );
}
