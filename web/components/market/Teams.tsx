import { fmtP } from "../../lib/game.ts";
import type { TeamStat } from "../../lib/views/market.ts";
import { Empty } from "../ui.tsx";

export function Teams({ teams }: { teams: TeamStat[] }) {
  if (!teams.length) return <Empty>No counterparty has traded yet.</Empty>;
  return (
    <div className="scroll">
      <table className="table">
        <thead>
          <tr>
            <th>team</th><th className="r">trades</th><th className="r">volume</th>
            <th className="r">bought</th><th className="r">sold</th><th className="r">last tick</th>
          </tr>
        </thead>
        <tbody>
          {teams.map((t) => (
            <tr key={t.team}>
              <td><b>{t.team}</b></td>
              <td className="r">{t.trades}</td>
              <td className="r">{fmtP(t.volume)}</td>
              <td className="r">{t.asBuyer}</td>
              <td className="r">{t.asSeller}</td>
              <td className="r muted">{t.lastTick ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
