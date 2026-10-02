import { sparkline, type ScoreView } from "../../lib/views/album.ts";
import css from "../../app/album/album.module.css";

const W = 160;
const H = 32;

function Spark({ label, values, unit, className }: { label: string; values: number[]; unit: string; className: string }) {
  const line = sparkline(values, { w: W, h: H });
  const last = values.at(-1);
  return (
    <div className={`${css.spark} ${className}`}>
      <div className={css.sparkHead}>
        <span className="muted">{label}</span>
        <b>{last == null ? "—" : `${Math.round(last * 10) / 10}${unit}`}</b>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${label} over ${values.length} snapshots`}>
        {line && <path d={line.d} />}
        {line && <circle cx={line.x} cy={line.y} r={3} />}
      </svg>
    </div>
  );
}

export function ScorePanel({ score, scores, cash }: { score: ScoreView; scores: number[]; cash: number[] }) {
  return (
    <>
      <div className={css.total}>
        total <b>{score.total.toFixed(1)}</b> · rank #{score.rank ?? "—"}{score.deals != null && ` · ${score.deals} deals`}
      </div>
      <div className={css.bars}>
        {score.bars.map((b) => (
          <div key={b.key} className={css.bar} title={`${b.key} = ${b.points}`}>
            <span className={css.barLabel}>{b.label}</span>
            <span className={css.track}><span className={css.fill} style={{ width: `${b.pct}%` }} /></span>
            <span className={css.num}>{b.points.toFixed(1)}</span>
          </div>
        ))}
      </div>
      <div className={css.sparks}>
        <Spark label="Score" values={scores} unit="" className={css.sparkScore} />
        <Spark label="Cash" values={cash} unit=" P" className={css.sparkCash} />
      </div>
    </>
  );
}
