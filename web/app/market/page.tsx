"use client";

import { useMemo, useState } from "react";
import { CardPrices } from "../../components/market/CardPrices.tsx";
import { Tape } from "../../components/market/Tape.tsx";
import { Teams } from "../../components/market/Teams.tsx";
import { Panel } from "../../components/ui.tsx";
import { useGame } from "../../lib/stream.tsx";
import { cardStats, marketTape, teamStats, type Include } from "../../lib/views/market.ts";
import css from "./market.module.css";

export default function MarketPage() {
  const { state, version } = useGame();
  const [include, setInclude] = useState<Include>("others");
  const [query, setQuery] = useState("");
  const view = useMemo(() => {
    const rows = marketTape(state, { include, query });
    return {
      rows,
      volume: rows.reduce((a, t) => a + t.price, 0),
      cards: cardStats(state, include),
      teams: teamStats(state, include),
    };
  }, [state, version, include, query]);
  const actions = (
    <>
      <div className="seg" role="group" aria-label="Which trades">
        {(["others", "all"] as const).map((k) => (
          <button key={k} type="button" className={include === k ? "on" : undefined} aria-pressed={include === k} onClick={() => setInclude(k)}>
            {k === "others" ? "Others" : "All"}
          </button>
        ))}
      </div>
      <input
        type="search"
        className={css.search}
        placeholder="team, card, venue, s/e/# id"
        aria-label="Filter trades"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
    </>
  );
  return (
    <>
      <Panel title="Market tape" sub={`${view.rows.length} trades · ${view.volume} P`} actions={actions}>
        <Tape rows={view.rows} team={state.team} />
      </Panel>
      <div className={css.grid}>
        <Panel title="Card prices" sub={`${view.cards.length} cards · by trades`}>
          <CardPrices cards={view.cards} />
        </Panel>
        <Panel title="Most active teams" sub={`${view.teams.length} counterparties`}>
          <Teams teams={view.teams} />
        </Panel>
      </div>
    </>
  );
}
