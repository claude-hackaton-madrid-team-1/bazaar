import { fmtP } from "../../lib/game.ts";
import { sparkEnd, sparkPath } from "../../lib/views/market.ts";
import css from "../../app/market/market.module.css";

export function Sparkline({ values, w = 64, h = 18 }: { values: number[]; w?: number; h?: number }) {
  const d = sparkPath(values, w, h, 3);
  const end = sparkEnd(values, w, h, 3);
  if (!d || !end) return <span className="muted">—</span>;
  const label = `${values.length} prices, ${fmtP(Math.min(...values))} to ${fmtP(Math.max(...values))}, last ${fmtP(values.at(-1))}`;
  return (
    <svg className={css.spark} viewBox={`0 0 ${w} ${h}`} width={w} height={h} role="img" aria-label={label}>
      <title>{label}</title>
      <path d={d} />
      <circle cx={end.x} cy={end.y} r={2.5} />
    </svg>
  );
}
