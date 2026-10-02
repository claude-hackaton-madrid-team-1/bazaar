import { BOOK, RARITY_COLOR, SETS, SLOT_RARITY, fmtP, type Rarity } from "../game.ts";
import type { State } from "../state.ts";

export type Held = { id: number; serial: number };

export type Slot = {
  ref: string;
  num: number;
  rarity: Rarity;
  color: string;
  count: number;
  held: Held[];
  book: number;
  value: number | null;
  last: number | null;
  spare: boolean;
  title: string;
};

export type AlbumRow = {
  set: string;
  name: string;
  color: string;
  have: number;
  of: number;
  missing: number;
  specialMissing: number;
  complete: boolean;
  master: boolean;
  slots: Slot[];
};

export type AlbumSort = "closest" | "set";

export type AlbumSummary = {
  pages: number;
  complete: number;
  master: number;
  missing: number;
  duplicates: { count: number; refs: { ref: string; spare: number }[] };
  cheapest: { ref: string; book: number; rarity: Rarity; page: string } | null;
};

export type ScoreBar = { key: string; label: string; points: number; pct: number };

export type ScoreView = { total: number; rank: number | null; deals: number | null; bars: ScoreBar[] };

const PAGE_SLOTS = 10;

const refFor = (set: string, i: number) => `${set}-${String(i + 1).padStart(2, "0")}`;

function slotOf(s: State, set: string, i: number): Slot {
  const ref = refFor(set, i);
  const held = s.owned[ref] ?? [];
  const rarity = SLOT_RARITY[i];
  const value = held.length ? s.values[ref] ?? null : null;
  const last = s.prices[ref]?.at(-1) ?? null;
  const title = [
    `${ref} · ${rarity}`,
    `book ${fmtP(BOOK[i])} · ours ${fmtP(value)} · market ${fmtP(last)}`,
    held.length ? `held ×${held.length}: ${held.map((h) => `#${h.id} s${h.serial}`).join(", ")}` : "missing",
  ].join("\n");
  return {
    ref, num: i + 1, rarity, color: RARITY_COLOR[rarity], count: held.length, held, book: BOOK[i],
    value, last, spare: held.length > 1, title,
  };
}

export function albumRows(s: State, { sort = "closest" }: { sort?: AlbumSort } = {}): AlbumRow[] {
  const rows = s.pages.map((page) => {
    const slots = SLOT_RARITY.map((_, i) => slotOf(s, page.set, i));
    const have = page.have ?? slots.slice(0, PAGE_SLOTS).filter((c) => c.count).length;
    const of = page.of ?? PAGE_SLOTS;
    const missing = Math.max(0, of - have);
    return {
      set: page.set,
      name: page.name ?? SETS[page.set]?.name ?? page.set,
      color: SETS[page.set]?.color ?? "var(--text-muted)",
      have, of, missing,
      specialMissing: slots.slice(PAGE_SLOTS).filter((c) => !c.count).length,
      complete: page.complete ?? missing === 0,
      master: Boolean(page.master),
      slots,
    };
  });
  if (sort === "set") return rows;
  return rows
    .map((row, i) => ({ row, i }))
    .sort((a, b) => a.row.missing - b.row.missing || a.row.specialMissing - b.row.specialMissing || a.i - b.i)
    .map(({ row }) => row);
}

export function albumSummary(s: State): AlbumSummary {
  const rows = albumRows(s);
  const refs = Object.entries(s.owned)
    .filter(([, held]) => held.length > 1)
    .map(([ref, held]) => ({ ref, spare: held.length - 1 }))
    .sort((a, b) => b.spare - a.spare || a.ref.localeCompare(b.ref));
  let cheapest: AlbumSummary["cheapest"] = null;
  for (const row of rows) {
    for (const c of row.slots) {
      if (c.count || (cheapest && cheapest.book <= c.book)) continue;
      cheapest = { ref: c.ref, book: c.book, rarity: c.rarity, page: row.name };
    }
  }
  return {
    pages: rows.length,
    complete: rows.filter((r) => r.complete).length,
    master: rows.filter((r) => r.master).length,
    missing: rows.reduce((n, r) => n + r.missing, 0),
    duplicates: { count: refs.reduce((n, r) => n + r.spare, 0), refs },
    cheapest,
  };
}

const PARTS = [
  ["duel_points", "Duels"],
  ["ladder_points", "Ladder"],
  ["neg_points", "Negotiation"],
  ["mm_points", "Market-making"],
] as const;

export function scoreBars(s: State): ScoreView {
  const sc = s.score ?? {};
  const points = PARTS.map(([key]) => Number(sc[key] ?? 0));
  const max = Math.max(10, ...points);
  return {
    total: sc.score ?? 0,
    rank: sc.rank ?? null,
    deals: sc.deals ?? null,
    bars: PARTS.map(([key, label], i) => ({
      key, label, points: points[i], pct: Math.min(100, Math.max(0, (points[i] / max) * 100)),
    })),
  };
}

export const series = (history: State["history"], key: "score" | "cash"): number[] => history.map((p) => p[key]);

export function sparkline(values: number[], { w, h }: { w: number; h: number }): { d: string; x: number; y: number } | null {
  if (values.length < 2) return null;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const x = (i: number) => (i / (values.length - 1)) * (w - 4) + 2;
  const y = (v: number) => h - 3 - (hi === lo ? 0.5 : (v - lo) / (hi - lo)) * (h - 6);
  const d = values.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join("");
  return { d, x: x(values.length - 1), y: y(values.at(-1)!) };
}
