"use client";

import { useMemo, useState } from "react";
import { EventTable } from "../../components/debug/EventTable.tsx";
import { Filters, type FilterState } from "../../components/debug/Filters.tsx";
import { StreamStats } from "../../components/debug/StreamStats.tsx";
import { Panel } from "../../components/ui.tsx";
import { useGame } from "../../lib/stream.tsx";
import { debugRows, streamStats } from "../../lib/views/debug.ts";

const LIMIT = 300;

export default function DebugPage() {
  const g = useGame();
  const [filters, setFilters] = useState<FilterState>({ source: "all", families: [], query: "", unknownOnly: false });
  const result = useMemo(() => debugRows(g.state, { ...filters, limit: LIMIT }), [g.state, g.version, filters]);
  const stats = useMemo(() => streamStats(g.state), [g.state, g.version]);
  const shown = result.rows.length < result.matched ? `${result.rows.length} of ${result.matched}` : `${result.matched}`;
  return (
    <Panel title="Event stream" sub={`${shown} shown · ${result.scanned} in ${filters.source === "ours" ? "ours" : "window"}`}>
      <StreamStats stats={stats} />
      <Filters value={filters} counts={result.families} onChange={setFilters} />
      <EventTable rows={result.rows} selected={g.selected} onSelect={g.select} />
    </Panel>
  );
}
