export const KNOWN_TYPES = new Set([
  "agent.hello", "clock", "agent.phase", "agent.thought", "agent.action", "agent.me",
  "thread.message", "thread.closed", "settlement", "duel.message", "duel.result",
]);

export const LIMITS = { log: 300, tape: 200, prices: 24, events: 500, history: 400 };

export function createState() {
  return {
    team: "", name: "", tick: 0, day: "", tickSeconds: 60, phase: "observe", goal: "",
    cash: 0, score: {}, pages: [], owned: {}, values: {},
    log: [], threads: {}, duels: {}, tape: [], prices: {}, history: [], ours: { trades: 0, gain: 0 },
    events: [], byId: {},
  };
}

const push = (list, item, max) => {
  list.push(item);
  if (list.length > max) list.splice(0, list.length - max);
};

const topicOf = (offer) => {
  for (const side of ["give", "want"]) {
    const types = offer[side]?.types ?? [];
    if (types.length) return types[0].split(":").at(-1);
    const assets = offer[side]?.assets ?? [];
    if (assets.length) return assets[0].ref ?? "?";
  }
  return "?";
};

const priceOf = (offer) => offer.want?.cash || offer.give?.cash || null;

function threadMessage(s, e) {
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
      assets: [...(offer.give?.assets ?? []), ...(offer.want?.assets ?? [])].map((a) => a.id),
    });
  }
  if (!ours && p.text) th.lastText = p.text;
  th.rounds += 1;
}

function settlement(s, e) {
  const p = e.payload;
  const parties = p.parties ?? [];
  const venue = p.venue || p.persona || "direct";
  const price = p.price ?? 0;
  for (const item of p.items ?? []) {
    const ref = item.ref ?? "?";
    const seller = item.frm ?? "?";
    const buyer = item.to ?? "?";
    const ours = parties.includes(s.team);
    const value = p.your_value ?? s.values[ref];
    let gain = null;
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

function duelMessage(s, e) {
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

function duelResult(s, e) {
  const d = s.duels[e.payload.duel];
  if (!d) return;
  d.status = e.payload.deal ? "deal" : "no deal";
  d.dealPrice = e.payload.price ?? null;
  d.points = e.payload.points ?? null;
  d.lastEventId = e.id;
}

function me(s, e) {
  const p = e.payload;
  s.cash = p.cash ?? s.cash;
  if (p.score) s.score = p.score;
  if (p.album?.pages) s.pages = p.album.pages;
  const cards = (p.assets ?? []).filter((a) => a.kind === "card");
  if (cards.length) {
    s.owned = {};
    s.values = {};
    for (const a of cards) {
      (s.owned[a.ref] ??= []).push({ id: a.id, serial: a.serial });
      s.values[a.ref] = a.your_value ?? 0;
    }
  }
  s.meEventId = e.id;
  push(s.history, { tick: e.tick, score: s.score.score ?? 0, cash: s.cash }, LIMITS.history);
}

export function apply(s, e) {
  const p = (e.payload ??= {});
  const tick = e.tick ?? s.tick;
  s.events.push(e);
  s.byId[e.id] = e;
  while (s.events.length > LIMITS.events) {
    const old = s.events.shift();
    if (s.byId[old.id] === old) delete s.byId[old.id];
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
