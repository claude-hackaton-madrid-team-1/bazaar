"use client";

import { Badge, Empty } from "../ui.tsx";
import type { DebugRow } from "../../lib/views/debug.ts";
import css from "../../app/debug/debug.module.css";

export function EventTable({ rows, selected, onSelect }: {
  rows: DebugRow[]; selected: number | null; onSelect: (id: number) => void;
}) {
  if (!rows.length) return <Empty>No events match these filters.</Empty>;
  return (
    <div className="scroll">
      <table className={`table ${css.rows}`}>
        <thead>
          <tr><th className="r">id</th><th className="r">tick</th><th>type</th><th>actor</th><th>scope</th><th>source</th><th>summary</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.id} className={selected === r.id ? "sel" : r.ours ? "ours" : undefined} tabIndex={0}
              onClick={() => onSelect(r.id)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(r.id); } }}
            >
              <td className="r">#{r.id}</td>
              <td className="r">{r.tick ?? "—"}</td>
              <td><span className={`${css.type} ${css[r.family]}`} title={r.known ? r.family : "not in KNOWN_TYPES"}>{r.type}</span></td>
              <td>{r.actor || <span className={css.dim}>—</span>}</td>
              <td className={css.dim}>{r.scope || "—"}</td>
              <td>{r.ours ? <Badge tone="us">OURS</Badge> : <Badge>MARKET</Badge>}</td>
              <td className={css.summary} title={r.summary}>{r.summary}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
