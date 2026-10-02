"use client";

import { fmtP } from "../../lib/game.ts";
import { useGame } from "../../lib/stream.tsx";
import { deltaText, deltaTone, type TapeRow } from "../../lib/views/market.ts";
import { Empty, EventLink } from "../ui.tsx";
import { CardRef } from "./CardRef.tsx";

export function Tape({ rows, team }: { rows: TapeRow[]; team: string }) {
  const g = useGame();
  if (!rows.length) return <Empty>No trades match yet.</Empty>;
  const party = (p: string) => <span className={p === team ? "us-text" : undefined}>{p}</span>;
  return (
    <div className="scroll">
      <table className="table">
        <thead>
          <tr>
            <th className="r">tick</th><th>venue</th><th>seller → buyer</th><th>card</th>
            <th className="r">price</th><th className="r">vs book</th><th className="r">fee</th><th>settle</th><th>event</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((t) => (
            <tr key={`${t.eventId}-${t.assetId ?? t.serial}-${t.ref}`} className={[t.ours && "ours", g.selected === t.eventId && "sel"].filter(Boolean).join(" ") || undefined}>
              <td className="r">{t.tick ?? "—"}</td>
              <td>{t.venue}</td>
              <td>{party(t.seller)} <span className="muted">→</span> {party(t.buyer)}</td>
              <td><CardRef code={t.ref} name={t.name} /></td>
              <td className="r">{fmtP(t.price)}</td>
              <td className={`r ${deltaTone(t.delta)}`} title={t.book == null ? "no book price" : `book ${fmtP(t.book)}`}>{deltaText(t.delta)}</td>
              <td className="r">{t.fee ? fmtP(t.fee) : <span className="muted">—</span>}</td>
              <td className="muted">{t.settlementId == null ? "—" : `s${t.settlementId}`}</td>
              <td><EventLink id={t.eventId} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
