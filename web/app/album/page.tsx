"use client";

import { useState } from "react";
import { AlbumGrid } from "../../components/album/AlbumGrid.tsx";
import { ScorePanel } from "../../components/album/ScorePanel.tsx";
import { Summary } from "../../components/album/Summary.tsx";
import { Empty, EventLink, Panel } from "../../components/ui.tsx";
import { useGame } from "../../lib/stream.tsx";
import { albumRows, albumSummary, scoreBars, series, type AlbumSort } from "../../lib/views/album.ts";
import css from "./album.module.css";

export default function AlbumPage() {
  const { state } = useGame();
  const [sort, setSort] = useState<AlbumSort>("closest");
  const rows = albumRows(state, { sort });
  const sum = albumSummary(state);
  const toggle = (
    <div className="seg">
      <button type="button" className={sort === "closest" ? "on" : ""} onClick={() => setSort("closest")}>Closest</button>
      <button type="button" className={sort === "set" ? "on" : ""} onClick={() => setSort("set")}>Set order</button>
    </div>
  );
  return (
    <>
      <Summary sum={sum} />
      <div className={css.layout}>
        <Panel title="Album" sub={<EventLink id={state.meEventId}>agent.me</EventLink>} actions={toggle}>
          {rows.length ? <AlbumGrid rows={rows} /> : <Empty>Waiting for agent.me</Empty>}
        </Panel>
        <Panel title="Score" sub={`${state.history.length} snapshots`}>
          <ScorePanel score={scoreBars(state)} scores={series(state.history, "score")} cash={series(state.history, "cash")} />
        </Panel>
      </div>
    </>
  );
}
