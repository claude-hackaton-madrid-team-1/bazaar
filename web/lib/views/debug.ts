import { KNOWN_TYPES, type GameEvent, type Payload, type State } from "../state.ts";

export type Family = "agent" | "thread" | "settlement" | "duel" | "clock" | "unknown";
export type Source = "ours" | "market" | "all";

export const FAMILIES: Family[] = ["agent", "thread", "settlement", "duel", "clock", "unknown"];

export type DebugRow = {
  id: number;
  tick: number | undefined;
  type: string;
  family: Family;
  actor: string;
  scope: string;
  ours: boolean;
  known: boolean;
  summary: string;
};

export type DebugQuery = {
  source?: Source;
  families?: Family[];
  query?: string;
  unknownOnly?: boolean;
  limit?: number;
};

export type DebugResult = {
  rows: DebugRow[];
  matched: number;
  scanned: number;
  families: Partial<Record<Family, number>>;
};

export type StreamStats = {
  window: number;
  mine: number;
  unknown: number;
  byType: [string, number][];
  perTick: { tick: number; count: number }[];
  lastId: number | null;
};

const MAX = 96;

const text = (value: unknown): string => (value == null ? "" : String(value));

const cut = (value: unknown, max = MAX): string => {
  const s = text(value).replace(/\s+/g, " ").trim();
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
};

const or = (value: unknown, fallback = "?"): string => (value == null || value === "" ? fallback : text(value));

const join = (parts: (string | null | false | undefined)[]): string => parts.filter(Boolean).join(" · ");

export function familyOf(type: string): Family {
  if (type.startsWith("agent.")) return "agent";
  if (type.startsWith("thread.")) return "thread";
  if (type.startsWith("duel.")) return "duel";
  if (type === "settlement") return "settlement";
  if (type === "clock") return "clock";
  return "unknown";
}

function threadMessage(p: Payload): string {
  const offer: Payload = p.offer && typeof p.offer === "object" ? p.offer : {};
  const price = offer.want?.cash || offer.give?.cash || null;
  return join([
    `thread ${or(p.thread)}`,
    `${or(p.sender ?? offer.maker)} → ${or(offer.to ?? p.with)}`,
    price != null && `${text(price)} P${offer.final ? " final" : ""}`,
    p.text != null && p.text !== "" && `“${cut(p.text, 60)}”`,
  ]);
}

function settlement(p: Payload): string {
  const items: Payload[] = Array.isArray(p.items) ? p.items : [];
  return join([
    `settlement ${or(p.settlement)}`,
    [items.map((i) => `${or(i?.ref)} ${or(i?.frm)}→${or(i?.to)}`).join(", "), `${text(p.price ?? 0)} P`].filter(Boolean).join(" "),
  ]);
}

export function summarize(e: GameEvent): string {
  const p: Payload = e.payload && typeof e.payload === "object" ? e.payload : {};
  let line: string;
  switch (e.type) {
    case "thread.message": line = threadMessage(p); break;
    case "thread.closed": line = `thread ${or(p.thread)} closed`; break;
    case "settlement": line = settlement(p); break;
    case "agent.thought": line = text(p.text); break;
    case "agent.action": line = join([text(p.kind), text(p.summary)]); break;
    case "agent.phase": line = join([text(p.phase), text(p.goal)]); break;
    case "agent.hello": line = join([text(p.team), text(p.name)]); break;
    case "agent.me": line = join([
      `${or(p.cash)} P`,
      `score ${or(p.score?.score)}`,
      p.score?.rank != null && `rank ${text(p.score.rank)}`,
      `${Array.isArray(p.assets) ? p.assets.length : 0} assets`,
    ]); break;
    case "clock": line = join([`tick ${or(e.tick)}`, text(p.day), p.tick_seconds != null && `${text(p.tick_seconds)}s`]); break;
    case "duel.message": line = join([
      `duel ${or(p.duel)}`, text(p.sender), p.price != null && `${text(p.price)} P`, p.days != null && `${text(p.days)}d`,
    ]); break;
    case "duel.result": line = join([
      `duel ${or(p.duel)}`,
      p.deal ? `deal @ ${or(p.price)} P` : "no deal",
      p.deal && p.points != null && `+${text(p.points)}`,
    ]); break;
    default: line = JSON.stringify(p) ?? "";
  }
  return cut(line);
}

const haystack = (e: GameEvent): string =>
  [e.type, e.actor, e.scope, e.tick, JSON.stringify(e.payload ?? {})].map(text).join(" ").toLowerCase();

function matcher(query: string | undefined): (e: GameEvent) => boolean {
  const terms = (query ?? "").toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return () => true;
  return (e) => {
    const hay = haystack(e);
    return terms.every((t) => (/^#\d+$/.test(t) ? `#${e.id}` === t : hay.includes(t) || text(e.id) === t));
  };
}

export function debugRows(s: State, q: DebugQuery = {}): DebugResult {
  const source = q.source ?? "all";
  const limit = q.limit ?? 300;
  const wanted = q.families?.length ? new Set(q.families) : null;
  const matches = matcher(q.query);
  const mine = new Set(s.mine);
  const list = source === "ours" ? s.mine : s.events;
  const rows: DebugRow[] = [];
  const families: Partial<Record<Family, number>> = {};
  let matched = 0;
  for (let i = list.length - 1; i >= 0; i--) {
    const e = list[i];
    const ours = mine.has(e);
    if (source === "market" && ours) continue;
    const known = KNOWN_TYPES.has(e.type);
    if (q.unknownOnly && known) continue;
    if (!matches(e)) continue;
    const family = familyOf(e.type);
    families[family] = (families[family] ?? 0) + 1;
    if (wanted && !wanted.has(family)) continue;
    matched += 1;
    if (rows.length >= limit) continue;
    rows.push({
      id: e.id, tick: e.tick, type: e.type, family, actor: e.actor ?? "", scope: e.scope ?? "",
      ours, known, summary: summarize(e),
    });
  }
  return { rows, matched, scanned: list.length, families };
}

const tickOf = (e: GameEvent): number | null => (typeof e.tick === "number" && Number.isInteger(e.tick) ? e.tick : null);

export function streamStats(s: State, span = 10): StreamStats {
  const counts = new Map<string, number>();
  let unknown = 0;
  let latest = s.tick;
  for (const e of s.events) {
    counts.set(e.type, (counts.get(e.type) ?? 0) + 1);
    if (!KNOWN_TYPES.has(e.type)) unknown += 1;
    const tick = tickOf(e);
    if (tick != null && tick > latest) latest = tick;
  }
  const first = Math.max(0, latest - span + 1);
  const perTick = Array.from({ length: latest - first + 1 }, (_, i) => ({ tick: first + i, count: 0 }));
  for (const e of s.events) {
    const tick = tickOf(e);
    if (tick != null && tick >= first && tick <= latest) perTick[tick - first].count += 1;
  }
  const byType = [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  return { window: s.events.length, mine: s.mine.length, unknown, byType, perTick, lastId: s.events.at(-1)?.id ?? null };
}
