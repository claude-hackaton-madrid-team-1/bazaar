import styles from "../../app/negotiations/negotiations.module.css";
import { fmtP } from "../../lib/game.ts";
import type { DuelRow } from "../../lib/views/negotiations.ts";
import { Badge, Empty, EventLink } from "../ui.tsx";

const days = (d: number | null) => (d == null ? "" : ` · ${d}d`);

export function DuelsTable({ rows }: { rows: DuelRow[] }) {
  if (!rows.length) return <Empty>No duels yet.</Empty>;
  return (
    <div className={`${styles.duels} scroll`}>
      <table className="table">
        <thead>
          <tr>
            <th>duel</th><th>role</th><th className="r">us</th><th className="r">them</th><th className="r">gap</th>
            <th className="r">rounds</th><th>status</th><th className="r">deal</th><th className="r">points</th><th>event</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((d) => (
            <tr key={d.id}>
              <td>#{d.id}</td>
              <td>{d.role}</td>
              <td className="r us-text">{fmtP(d.ourPrice)}{days(d.ourDays)}</td>
              <td className="r them-text">{fmtP(d.theirPrice)}{days(d.theirDays)}</td>
              <td className="r">{fmtP(d.gap)}</td>
              <td className="r">{d.rounds}</td>
              <td><Badge tone={d.tone}>{d.status}</Badge></td>
              <td className="r">{fmtP(d.dealPrice)}</td>
              <td className="r">{d.points ?? "—"}</td>
              <td><EventLink id={d.lastEventId} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
