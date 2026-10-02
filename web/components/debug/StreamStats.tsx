"use client";

import type { StreamStats as Stats } from "../../lib/views/debug.ts";
import css from "../../app/debug/debug.module.css";

export function StreamStats({ stats }: { stats: Stats }) {
  const peak = Math.max(1, ...stats.perTick.map((p) => p.count));
  const total = stats.perTick.reduce((n, p) => n + p.count, 0);
  const avg = stats.perTick.length ? total / stats.perTick.length : 0;
  return (
    <dl className={css.stats}>
      <div><dt>window</dt><dd>{stats.window}</dd></div>
      <div><dt>ours kept</dt><dd>{stats.mine}</dd></div>
      <div><dt>last</dt><dd>{stats.lastId == null ? "—" : `#${stats.lastId}`}</dd></div>
      <div>
        <dt>per tick</dt>
        <dd title={stats.perTick.map((p) => `t${p.tick}: ${p.count}`).join("\n")}>
          <span className={css.spark} aria-hidden="true">
            {stats.perTick.map((p) => <i key={p.tick} style={{ height: `${Math.round((p.count / peak) * 100)}%` }} />)}
          </span>
          {avg.toFixed(1)}/tick · last {stats.perTick.at(-1)?.count ?? 0}
        </dd>
      </div>
      <div><dt>unknown</dt><dd className={stats.unknown ? css.warn : undefined}>{stats.unknown}</dd></div>
      <div className={css.types}>
        <dt>types</dt>
        {stats.byType.map(([type, n]) => <dd key={type}>{type} {n}</dd>)}
      </div>
    </dl>
  );
}
