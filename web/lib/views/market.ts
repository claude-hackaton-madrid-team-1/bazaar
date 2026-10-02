import { bookOf } from "../game.ts";
import type { State, Trade } from "../state.ts";

export type Include = "others" | "all";

export type TapeRow = Trade & { book: number | null; delta: number | null };

export type CardStat = {
  ref: string;
  name: string;
  trades: number;
  last: number;
  lastTick: number | undefined;
  median: number | null;
  min: number;
  max: number;
  book: number | null;
  trend: number[];
};

export type TeamStat = {
  team: string;
  trades: number;
  volume: number;
  asBuyer: number;
  asSeller: number;
  lastTick: number | undefined;
};

const visible = (s: State, include: Include): Trade[] => (include === "all" ? s.tape : s.tape.filter((t) => !t.ours));

const haystack = (t: Trade): string =>
  [t.seller, t.buyer, t.ref, t.name, t.venue, `s${t.settlementId}`, `#${t.assetId}`, `e${t.eventId}`].join(" ").toLowerCase();

export function median(values: number[]): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

const later = (a: number | undefined, b: number | undefined) => (a == null ? b : b == null ? a : Math.max(a, b));

export function marketTape(s: State, { include = "others", query = "" }: { include?: Include; query?: string } = {}): TapeRow[] {
  const q = query.trim().toLowerCase();
  return visible(s, include)
    .filter((t) => !q || haystack(t).includes(q))
    .map((t) => {
      const book = bookOf(t.ref);
      return { ...t, book, delta: book ? (t.price - book) / book : null };
    });
}

export function cardStats(s: State, include: Include = "others"): CardStat[] {
  const byRef = new Map<string, Trade[]>();
  for (const t of visible(s, include)) {
    const list = byRef.get(t.ref);
    if (list) list.push(t);
    else byRef.set(t.ref, [t]);
  }
  return [...byRef].map(([ref, trades]) => {
    const prices = trades.map((t) => t.price);
    return {
      ref, name: trades[0].name, trades: trades.length, last: trades[0].price, lastTick: trades[0].tick,
      median: median(prices), min: Math.min(...prices), max: Math.max(...prices), book: bookOf(ref),
      trend: s.prices[ref] ?? [],
    };
  }).sort((a, b) => b.trades - a.trades || a.ref.localeCompare(b.ref));
}

export function teamStats(s: State, include: Include = "others"): TeamStat[] {
  const teams = new Map<string, TeamStat>();
  const touch = (team: string, t: Trade, role: "asBuyer" | "asSeller") => {
    if (!team || team === s.team) return;
    let st = teams.get(team);
    if (!st) teams.set(team, (st = { team, trades: 0, volume: 0, asBuyer: 0, asSeller: 0, lastTick: undefined }));
    st.trades += 1;
    st.volume += t.price;
    st[role] += 1;
    st.lastTick = later(st.lastTick, t.tick);
  };
  for (const t of visible(s, include)) {
    touch(t.seller, t, "asSeller");
    touch(t.buyer, t, "asBuyer");
  }
  return [...teams.values()].sort((a, b) => b.trades - a.trades || b.volume - a.volume || a.team.localeCompare(b.team));
}

const r1 = (v: number) => Math.round(v * 10) / 10;

export function sparkPath(values: number[], w: number, h: number, pad = 1): string {
  if (values.length < 2) return "";
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const x = (i: number) => r1(pad + (i * (w - 2 * pad)) / (values.length - 1));
  const y = (v: number) => r1(hi === lo ? h / 2 : pad + ((hi - v) / (hi - lo)) * (h - 2 * pad));
  return values.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join("");
}

export function sparkEnd(values: number[], w: number, h: number, pad = 1): { x: number; y: number } | null {
  const d = sparkPath(values, w, h, pad);
  const last = d.split(/[ML]/).at(-1);
  if (!last) return null;
  const [x, y] = last.split(",").map(Number);
  return { x, y };
}

export const deltaText = (delta: number | null): string =>
  delta == null ? "" : `${delta > 0 ? "▲" : delta < 0 ? "▼" : "="} ${Math.abs(Math.round(delta * 100))}%`;

export const deltaTone = (delta: number | null): string => {
  const pct = delta == null ? 0 : Math.round(delta * 100);
  return pct > 0 ? "bad" : pct < 0 ? "good" : "";
};
