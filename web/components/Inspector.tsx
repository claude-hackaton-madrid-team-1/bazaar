"use client";

import { isOurs } from "../lib/state.ts";
import { useGame } from "../lib/stream.tsx";

export function Inspector() {
  const g = useGame();
  if (g.selected == null) return null;
  const e = g.state.byId[g.selected];
  return (
    <aside className="inspector" aria-label="Event inspector">
      <div className="insp-head">
        <strong>{e ? `#${e.id} ${e.type}` : `#${g.selected} (evicted)`}</strong>
        <span>
          {e && <button type="button" onClick={() => navigator.clipboard?.writeText(JSON.stringify(e, null, 2))}>Copy</button>}
          <button type="button" aria-label="Close" onClick={() => g.select(null)}>✕</button>
        </span>
      </div>
      {e && (
        <>
          <div className="insp-meta">
            tick {e.tick ?? "—"} · {e.actor || "—"} · {e.scope ?? "—"} · {isOurs(g.state, e) ? "ours" : "market"}
          </div>
          <pre>{JSON.stringify(e, null, 2)}</pre>
        </>
      )}
    </aside>
  );
}
