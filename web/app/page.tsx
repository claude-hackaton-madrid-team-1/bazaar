"use client";

import { useMemo, useState } from "react";
import { NowStrip } from "../components/agent/NowStrip.tsx";
import { TickCard } from "../components/agent/TickCard.tsx";
import { Empty, Panel } from "../components/ui.tsx";
import { useGame } from "../lib/stream.tsx";
import { now, timeline, type Filter } from "../lib/views/agent.ts";
import css from "./agent.module.css";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "actions", label: "Actions only" },
  { id: "deals", label: "Deals only" },
];

export default function AgentPage() {
  const { state, version } = useGame();
  const [filter, setFilter] = useState<Filter>("all");
  const [frozenAt, setFrozenAt] = useState<number | null>(null);
  const cards = useMemo(() => timeline(state, { filter, upToTick: frozenAt }), [state, version, filter, frozenAt]);
  const current = useMemo(() => now(state), [state, version]);
  const newer = frozenAt == null ? 0 : Math.max(0, state.tick - frozenAt);
  const controls = (
    <div className={css.controls}>
      <div className="seg" role="group" aria-label="Show">
        {FILTERS.map((f) => (
          <button key={f.id} type="button" className={filter === f.id ? "on" : ""} aria-pressed={filter === f.id} onClick={() => setFilter(f.id)}>
            {f.label}
          </button>
        ))}
      </div>
      <button
        type="button"
        className={frozenAt == null ? "on" : ""}
        aria-pressed={frozenAt == null}
        onClick={() => setFrozenAt(frozenAt == null ? state.tick : null)}
        title="When off, the timeline stays on the ticks it has now"
      >
        Follow live
      </button>
      {newer > 0 && (
        <button type="button" className={css.newer} onClick={() => setFrozenAt(null)}>
          {newer} newer {newer === 1 ? "tick" : "ticks"} ↑
        </button>
      )}
    </div>
  );
  return (
    <>
      <NowStrip now={current} />
      <Panel title="Timeline" sub="our agent only, newest tick first" actions={controls}>
        {cards.length ? (
          <div className={css.ticks}>
            {cards.map((card) => <TickCard key={card.tick} card={card} current={card.tick === state.tick} />)}
          </div>
        ) : (
          <Empty>{filter === "all" ? "Waiting for our agent's first tick" : "Nothing of this kind yet"}</Empty>
        )}
      </Panel>
    </>
  );
}
