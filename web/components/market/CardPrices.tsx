import { fmtP } from "../../lib/game.ts";
import type { CardStat } from "../../lib/views/market.ts";
import { Empty } from "../ui.tsx";
import { CardRef } from "./CardRef.tsx";
import { Sparkline } from "./Sparkline.tsx";

export function CardPrices({ cards }: { cards: CardStat[] }) {
  if (!cards.length) return <Empty>No card has traded yet.</Empty>;
  return (
    <div className="scroll">
      <table className="table">
        <thead>
          <tr>
            <th>card</th><th className="r">trades</th><th className="r">last</th><th className="r">median</th>
            <th className="r">min – max</th><th className="r">book</th><th>trend</th>
          </tr>
        </thead>
        <tbody>
          {cards.map((c) => (
            <tr key={c.ref}>
              <td><CardRef code={c.ref} name={c.name} /></td>
              <td className="r">{c.trades}</td>
              <td className="r">{fmtP(c.last)}</td>
              <td className="r">{fmtP(c.median)}</td>
              <td className="r">{c.min === c.max ? fmtP(c.min) : `${fmtP(c.min)} – ${fmtP(c.max)}`}</td>
              <td className="r muted">{fmtP(c.book)}</td>
              <td><Sparkline values={c.trend} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
