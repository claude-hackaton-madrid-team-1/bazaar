"use client";

import { FAMILIES, type Family, type Source } from "../../lib/views/debug.ts";
import css from "../../app/debug/debug.module.css";

export type FilterState = { source: Source; families: Family[]; query: string; unknownOnly: boolean };

const SOURCES: [Source, string][] = [["ours", "Ours"], ["market", "Market"], ["all", "All"]];

export function Filters({ value, counts, onChange }: {
  value: FilterState; counts: Partial<Record<Family, number>>; onChange: (next: FilterState) => void;
}) {
  const set = (patch: Partial<FilterState>) => onChange({ ...value, ...patch });
  const toggle = (f: Family) =>
    set({ families: value.families.includes(f) ? value.families.filter((x) => x !== f) : [...value.families, f] });
  return (
    <div className={css.bar}>
      <div className="seg" role="group" aria-label="Source">
        {SOURCES.map(([id, label]) => (
          <button key={id} type="button" className={value.source === id ? "on" : ""} aria-pressed={value.source === id} onClick={() => set({ source: id })}>
            {label}
          </button>
        ))}
      </div>
      <div className={css.chips} role="group" aria-label="Type family">
        {FAMILIES.map((f) => {
          const on = value.families.includes(f);
          return (
            <button key={f} type="button" aria-pressed={on} className={`${css.chip} ${css[f]}${on ? ` ${css.on}` : ""}`} onClick={() => toggle(f)}>
              {f}<b>{counts[f] ?? 0}</b>
            </button>
          );
        })}
        {value.families.length > 0 && <button type="button" onClick={() => set({ families: [] })}>clear</button>}
      </div>
      <input
        type="search" className={css.search} placeholder="search type, actor, ids, JSON…" aria-label="Search events"
        value={value.query} onChange={(e) => set({ query: e.target.value })}
      />
      <label className={css.toggle}>
        <input type="checkbox" checked={value.unknownOnly} onChange={(e) => set({ unknownOnly: e.target.checked })} />
        unknown types only
      </label>
    </div>
  );
}
