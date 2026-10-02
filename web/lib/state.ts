export type Payload = Record<string, any>;

export type GameEvent = {
  id: number;
  tick?: number;
  t?: number;
  type: string;
  scope?: string;
  actor?: string;
  payload: Payload;
};

export type Phase = "observe" | "decide" | "act";

export type LogLine = { eventId: number; tick: number; kind: string; text: string };

export type ThreadOffer = {
  eventId: number;
  tick: number | undefined;
  messageId: number | null;
  offerId: number | null;
  side: "us" | "them";
  price: number | null;
  maker: string;
  to: string;
  createdTick: number | null;
  expiresTick: number | null;
  final: boolean;
  assets: number[];
};

export type Thread = {
  id: number;
  with: string;
  topic: string;
  side: "buy" | "sell";
  ourPrice: number | null;
  theirPrice: number | null;
  rounds: number;
  final: boolean;
  expiresTick: number | null;
  status: "open" | "closed";
  lastText: string | null;
  offers: ThreadOffer[];
};

export type Duel = {
  id: number;
  role: string;
  ourPrice: number | null;
  theirPrice: number | null;
  ourDays: number | null;
  theirDays: number | null;
  rounds: number;
  status: "open" | "deal" | "no deal";
  dealPrice: number | null;
  points: number | null;
  lastEventId: number | null;
};

export type Trade = {
  eventId: number;
  settlementId: number | null;
  tick: number | undefined;
  venue: string;
  seller: string;
  buyer: string;
  assetId: number | null;
  ref: string;
  serial: number | null;
  kind: string;
  name: string;
  price: number;
  fee: number;
  ours: boolean;
  gain: number | null;
};

export type Page = { set: string; name?: string; have?: number; of?: number; complete?: boolean; master?: boolean; [k: string]: any };

export type Score = { score?: number; rank?: number; duel_points?: number; ladder_points?: number; neg_points?: number; mm_points?: number; [k: string]: any };

export type State = {
  team: string;
  name: string;
  tick: number;
  day: string;
  tickSeconds: number;
  phase: Phase;
  goal: string;
  cash: number;
  score: Score;
  pages: Page[];
  owned: Record<string, { id: number; serial: number }[]>;
  values: Record<string, number>;
  log: LogLine[];
  threads: Record<number, Thread>;
  duels: Record<number, Duel>;
  tape: Trade[];
  prices: Record<string, number[]>;
  history: { tick: number; score: number; cash: number }[];
  ours: { trades: number; gain: number };
  events: GameEvent[];
  mine: GameEvent[];
  byId: Record<number, GameEvent>;
  meEventId?: number;
};

export const KNOWN_TYPES = new Set([
  "agent.hello", "clock", "agent.phase", "agent.thought", "agent.action", "agent.me",
  "thread.message", "thread.closed", "settlement", "duel.message", "duel.result",
]);

export const LIMITS = { log: 300, tape: 200, prices: 24, events: 500, mine: 1500, history: 400 };

export function createState(): State {
  return {
    team: "", name: "", tick: 0, day: "", tickSeconds: 60, phase: "observe", goal: "",
    cash: 0, score: {}, pages: [], owned: {}, values: {},
    log: [], threads: {}, duels: {}, tape: [], prices: {}, history: [], ours: { trades: 0, gain: 0 },
    events: [], mine: [], byId: {},
  };
}

const push = <T>(list: T[], item: T, max: number) => {
  list.push(item);
  if (list.length > max) list.splice(0, list.length - max);
};

const topicOf = (offer: Payload): string => {
  for (const side of ["give", "want"]) {
    const types: string[] = offer[side]?.types ?? [];
    if (types.length) return types[0].split(":").at(-1) ?? "?";
    const assets: Payload[] = offer[side]?.assets ?? [];
    if (assets.length) return assets[0].ref ?? "?";
  }
  return "?";
};

const priceOf = (offer: Payload): number | null => offer.want?.cash || offer.give?.cash || null;

export function isOurs(s: State, e: GameEvent): boolean {
  const p = e.payload ?? {};
  if (e.type.startsWith("agent.") || e.type === "clock" || e.type.startsWith("duel.")) return true;
  if (e.type === "thread.message") return p.team === s.team;
  if (e.type === "thread.closed") return p.thread in s.threads;
  if (e.type === "settlement") return (p.parties ?? []).includes(s.team);
  return false;
}

function threadMessage(s: State, e: GameEvent) {
  const p = e.payload;
  if (p.team !== s.team) return;
  const th = (s.threads[p.thread] ??= {
    id: p.thread, with: p.with ?? "?", topic: "?", side: "buy", ourPrice: null, theirPrice: null,
    rounds: 0, final: false, expiresTick: null, status: "open", lastText: null, offers: [],
  });
  const ours = p.sender === s.team;
  const offer = p.offer;
  if (offer) {
    th.topic = topicOf(offer);
    const price = priceOf(offer);
    if (ours) {
      th.side = offer.give?.cash ? "buy" : "sell";
      th.ourPrice = price;
    } else {
      th.side = offer.want?.cash ? "buy" : "sell";
      th.theirPrice = price;
      th.final = Boolean(offer.final);
    }
    th.expiresTick = offer.expires_tick ?? null;
    th.offers.push({
      eventId: e.id, tick: e.tick, messageId: p.message ?? null, offerId: offer.id ?? null,
      side: ours ? "us" : "them", price, maker: offer.maker, to: offer.to,
      createdTick: offer.created_tick ?? null, expiresTick: offer.expires_tick ?? null, final: Boolean(offer.final),
      assets: [...(offer.give?.assets ?? []), ...(offer.want?.assets ?? [])].map((a: Payload) => a.id),
    });
  }
  if (!ours && p.text) th.lastText = p.text;
  th.rounds += 1;
}

function settlement(s: State, e: GameEvent) {
  const p = e.payload;
  const parties: string[] = p.parties ?? [];
  const venue = p.venue || p.persona || "direct";
  const price: number = p.price ?? 0;
  for (const item of p.items ?? []) {
    const ref: string = item.ref ?? "?";
    const seller: string = item.frm ?? "?";
    const buyer: string = item.to ?? "?";
    const ours = parties.includes(s.team);
    const value: number | undefined = p.your_value ?? s.values[ref];
    let gain: number | null = null;
    if (ours && value != null) gain = buyer === s.team ? value - price : price - value;
    if (ours) {
      s.ours.trades += 1;
      s.ours.gain += gain ?? 0;
    }
    s.tape.unshift({
      eventId: e.id, settlementId: p.settlement ?? null, tick: e.tick, venue, seller, buyer,
      assetId: item.id ?? null, ref, serial: item.serial ?? null, kind: item.kind ?? "card",
      name: item.name ?? ref, price, fee: p.fee ?? 0, ours, gain,
    });
    if (s.tape.length > LIMITS.tape) s.tape.length = LIMITS.tape;
    push((s.prices[ref] ??= []), price, LIMITS.prices);
  }
}

function duelMessage(s: State, e: GameEvent) {
  const p = e.payload;
  const d = (s.duels[p.duel] ??= {
    id: p.duel, role: p.role ?? "?", ourPrice: null, theirPrice: null, ourDays: null, theirDays: null,
    rounds: 0, status: "open", dealPrice: null, points: null, lastEventId: null,
  });
  if (p.sender === s.team) [d.ourPrice, d.ourDays] = [p.price ?? null, p.days ?? null];
  else [d.theirPrice, d.theirDays] = [p.price ?? null, p.days ?? null];
  d.rounds += 1;
  d.lastEventId = e.id;
}

function duelResult(s: State, e: GameEvent) {
  const d = s.duels[e.payload.duel];
  if (!d) return;
  d.status = e.payload.deal ? "deal" : "no deal";
  d.dealPrice = e.payload.price ?? null;
  d.points = e.payload.points ?? null;
  d.lastEventId = e.id;
}

function me(s: State, e: GameEvent) {
  const p = e.payload;
  s.cash = p.cash ?? s.cash;
  if (p.score) s.score = p.score;
  if (p.album?.pages) s.pages = p.album.pages;
  const cards: Payload[] = (p.assets ?? []).filter((a: Payload) => a.kind === "card");
  if (cards.length) {
    s.owned = {};
    s.values = {};
    for (const a of cards) {
      (s.owned[a.ref] ??= []).push({ id: a.id, serial: a.serial });
      s.values[a.ref] = a.your_value ?? 0;
    }
  }
  s.meEventId = e.id;
  push(s.history, { tick: e.tick ?? s.tick, score: s.score.score ?? 0, cash: s.cash }, LIMITS.history);
}

export function apply(s: State, e: GameEvent): State {
  const p = (e.payload ??= {});
  const tick = e.tick ?? s.tick;
  s.events.push(e);
  s.byId[e.id] = e;
  while (s.events.length > LIMITS.events) {
    const old = s.events.shift()!;
    if (s.byId[old.id] === old && !s.mine.includes(old)) delete s.byId[old.id];
  }
  if (isOurs(s, e)) {
    s.mine.push(e);
    while (s.mine.length > LIMITS.mine) {
      const old = s.mine.shift()!;
      if (s.byId[old.id] === old && !s.events.includes(old)) delete s.byId[old.id];
    }
  }
  switch (e.type) {
    case "agent.hello":
      s.team = p.team ?? s.team;
      s.name = p.name ?? s.name;
      break;
    case "clock":
      s.tick = tick;
      s.day = p.day ?? s.day;
      s.tickSeconds = p.tick_seconds ?? s.tickSeconds;
      break;
    case "agent.phase":
      s.phase = p.phase ?? s.phase;
      s.goal = p.goal ?? s.goal;
      break;
    case "agent.thought":
      push(s.log, { eventId: e.id, tick, kind: "thought", text: p.text ?? "" }, LIMITS.log);
      break;
    case "agent.action":
      push(s.log, { eventId: e.id, tick, kind: p.kind ?? "act", text: p.summary ?? "" }, LIMITS.log);
      break;
    case "agent.me":
      me(s, e);
      break;
    case "thread.message":
      threadMessage(s, e);
      break;
    case "thread.closed":
      if (s.threads[p.thread]) s.threads[p.thread].status = "closed";
      break;
    case "settlement":
      settlement(s, e);
      break;
    case "duel.message":
      duelMessage(s, e);
      break;
    case "duel.result":
      duelResult(s, e);
      break;
  }
  return s;
}
