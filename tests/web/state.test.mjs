import { test } from "node:test";
import assert from "node:assert/strict";
import { apply, createState, KNOWN_TYPES } from "../../web/state.mjs";

let nextId = 1;
const ev = (type, payload = {}, tick = 10, actor = "") => ({ id: nextId++, tick, t: 0.1, type, scope: "public", actor, payload });

const offer = ({ id = 7, maker, to, giveCash = 0, wantCash = 0, giveTypes = [], wantTypes = [], giveAssets = [], final = false, expires = 12, created = 10 }) => ({
  id, maker, to, venue: null, thread: 61, status: "open",
  give: { cash: giveCash, assets: giveAssets, types: giveTypes },
  want: { cash: wantCash, assets: [], types: wantTypes },
  expires_tick: expires, created_tick: created, final,
});

const message = (sender, off, { team = "t01", text = null, message = 295 } = {}) =>
  ev("thread.message", { thread: 61, kind: "persona", message, sender, text, team, with: "abuela", offer: off }, 10, sender);

const ME = {
  cash: 412,
  score: { score: 18.4, rank: 9, duel_points: 4, ladder_points: 6.5, neg_points: 7.9, mm_points: 0 },
  album: { pages: [{ set: "LAT", name: "La Latina", have: 2, of: 10, complete: false, master: false }] },
  assets: [
    { id: 1, kind: "card", ref: "LAT-03", serial: 4, your_value: 6 },
    { id: 2, kind: "card", ref: "LAT-09", serial: 1, your_value: 40 },
    { id: 3, kind: "card", ref: "LAT-09", serial: 2, your_value: 4 },
    { id: 4, kind: "pack", ref: "sobre_barrio" },
  ],
};

const fresh = () => {
  const s = createState();
  apply(s, ev("agent.hello", { team: "t01", name: "Team 1" }));
  return s;
};

test("hello, clock and phase", () => {
  const s = fresh();
  apply(s, ev("clock", { day: "sat", tick_seconds: 30 }, 144));
  apply(s, ev("agent.phase", { phase: "decide", goal: "complete La Latina" }));
  assert.deepEqual([s.team, s.name, s.tick, s.day, s.tickSeconds, s.phase, s.goal],
    ["t01", "Team 1", 144, "sat", 30, "decide", "complete La Latina"]);
});

test("log lines keep their event id", () => {
  const s = fresh();
  const e = ev("agent.thought", { text: "Abuela is soft today" }, 3);
  apply(s, e);
  apply(s, ev("agent.action", { kind: "say", summary: "bid 18 P" }, 4));
  assert.deepEqual(s.log.map((l) => [l.eventId === e.id || l.kind === "say", l.tick, l.kind, l.text]),
    [[true, 3, "thought", "Abuela is soft today"], [true, 4, "say", "bid 18 P"]]);
});

test("dealer ask and our bid keep thread, offer and message ids", () => {
  const s = fresh();
  apply(s, message("abuela", offer({ id: 334, maker: "abuela", to: "t01", giveTypes: ["pack:sobre_barrio"], wantCash: 30, created: 31, expires: 33 }), { text: "30 P, cariño", message: 301 }));
  apply(s, message("t01", offer({ id: 335, maker: "t01", to: "abuela", giveCash: 18, wantTypes: ["pack:sobre_barrio"] }), { message: 302 }));
  const th = s.threads[61];
  assert.deepEqual([th.with, th.topic, th.side, th.theirPrice, th.ourPrice, th.rounds, th.lastText, th.final],
    ["abuela", "sobre_barrio", "buy", 30, 18, 2, "30 P, cariño", false]);
  assert.deepEqual(th.offers.map((o) => [o.offerId, o.messageId, o.side, o.price, o.maker, o.to]),
    [[334, 301, "them", 30, "abuela", "t01"], [335, 302, "us", 18, "t01", "abuela"]]);
  assert.equal(th.offers[0].createdTick, 31);
  assert.equal(th.offers[0].expiresTick, 33);
  assert.ok(th.offers[0].eventId > 0);
});

test("final offer and topic from types", () => {
  const s = fresh();
  apply(s, message("abuela", offer({ maker: "abuela", to: "t01", giveTypes: ["card:LAT-08"], wantCash: 22, final: true, expires: 33 })));
  assert.deepEqual([s.threads[61].final, s.threads[61].expiresTick, s.threads[61].topic], [true, 33, "LAT-08"]);
});

test("our sell offer names the asset id", () => {
  const s = fresh();
  apply(s, message("t01", offer({ maker: "t01", to: "t05", wantCash: 14, giveAssets: [{ id: 284, kind: "card", ref: "MAL-02", serial: 17 }] })));
  const th = s.threads[61];
  assert.deepEqual([th.side, th.topic, th.offers[0].assets], ["sell", "MAL-02", [284]]);
});

test("other teams' threads are not ours, closed is marked", () => {
  const s = fresh();
  apply(s, message("abuela", offer({ maker: "abuela", to: "t07", wantCash: 27 }), { team: "t07" }));
  assert.deepEqual(s.threads, {});
  apply(s, message("abuela", offer({ maker: "abuela", to: "t01", wantCash: 30 })));
  apply(s, ev("thread.closed", { thread: 61 }));
  assert.equal(s.threads[61].status, "closed");
});

test("agent.me sets cash, score, album, held assets with ids", () => {
  const s = fresh();
  apply(s, ev("agent.me", ME));
  assert.equal(s.cash, 412);
  assert.equal(s.score.rank, 9);
  assert.equal(s.pages[0].set, "LAT");
  assert.deepEqual(s.owned["LAT-09"], [{ id: 2, serial: 1 }, { id: 3, serial: 2 }]);
  assert.equal(s.values["LAT-09"], 4);
  assert.equal(s.owned.sobre_barrio, undefined);
  assert.deepEqual(s.history.at(-1), { tick: 10, score: 18.4, cash: 412 });
});

test("settlements keep settlement, asset and serial ids, and our gain", () => {
  const s = fresh();
  apply(s, ev("agent.me", ME));
  apply(s, ev("settlement", {
    settlement: 9, parties: ["t04", "t11"], venue: "rastro", price: 14, fee: 1,
    items: [{ id: 501, kind: "card", ref: "MAL-02", serial: 33, name: "Plaza del Dos de Mayo", frm: "t04", to: "t11" }],
  }, 50));
  apply(s, ev("settlement", {
    settlement: 10, parties: ["abuela", "t01"], venue: null, persona: "abuela", price: 22,
    items: [{ id: 502, kind: "card", ref: "LAT-09", serial: 3, frm: "abuela", to: "t01" }],
  }, 51));
  apply(s, ev("settlement", {
    settlement: 11, parties: ["abuela", "t01"], price: 9, your_value: 16,
    items: [{ id: 503, kind: "card", ref: "MAL-05", serial: 2, frm: "abuela", to: "t01" }],
  }, 52));
  const [c, b, a] = s.tape;
  assert.deepEqual([a.settlementId, a.assetId, a.serial, a.venue, a.seller, a.buyer, a.price, a.fee, a.ours, a.gain],
    [9, 501, 33, "rastro", "t04", "t11", 14, 1, false, null]);
  assert.deepEqual([b.venue, b.ours, b.gain], ["abuela", true, 4 - 22]);
  assert.equal(c.gain, 7);
  assert.ok(a.eventId > 0);
  assert.deepEqual(s.prices["MAL-02"], [14]);
});

test("tape is newest first and bounded", () => {
  const s = fresh();
  for (let i = 0; i < 300; i++) {
    apply(s, ev("settlement", { settlement: i, parties: ["t02", "t03"], price: i, items: [{ id: i, ref: "LAV-01", frm: "t02", to: "t03" }] }, i));
  }
  assert.equal(s.tape[0].price, 299);
  assert.ok(s.tape.length <= 200);
});

test("our value created adds up over the session, not just the tape window", () => {
  const s = fresh();
  apply(s, ev("settlement", { settlement: 1, parties: ["abuela", "t01"], price: 9, your_value: 16, items: [{ id: 1, ref: "MAL-05", frm: "abuela", to: "t01" }] }));
  for (let i = 0; i < 300; i++) {
    apply(s, ev("settlement", { settlement: i + 2, parties: ["t02", "t03"], price: 5, items: [{ id: i + 2, ref: "LAV-01", frm: "t02", to: "t03" }] }));
  }
  assert.equal(s.tape.some((t) => t.ours), false);
  assert.deepEqual(s.ours, { trades: 1, gain: 7 });
});

test("duels track both sides and the result", () => {
  const s = fresh();
  apply(s, ev("duel.message", { duel: 3, role: "seller", sender: "t01", price: 60, days: 4 }));
  apply(s, ev("duel.message", { duel: 3, role: "seller", sender: "rival", price: 41, days: 7 }));
  apply(s, ev("duel.result", { duel: 3, deal: true, price: 47, points: 1.2 }));
  const d = s.duels[3];
  assert.deepEqual([d.role, d.ourPrice, d.theirPrice, d.ourDays, d.theirDays, d.rounds, d.status, d.dealPrice, d.points],
    ["seller", 60, 41, 4, 7, 2, "deal", 47, 1.2]);
});

test("every event lands in the bounded stream and the id index, unknown types change nothing else", () => {
  const s = fresh();
  const before = JSON.stringify({ ...s, events: [], byId: {} });
  const e = ev("egg.found", { x: 1 });
  apply(s, e);
  assert.equal(JSON.stringify({ ...s, events: [], byId: {} }), before);
  assert.equal(s.events.at(-1), e);
  assert.equal(s.byId[e.id], e);
  for (let i = 0; i < 700; i++) apply(s, ev("egg.found"));
  assert.ok(s.events.length <= 500);
  assert.ok(Object.keys(s.byId).length <= 500);
  assert.ok(KNOWN_TYPES.has("settlement") && !KNOWN_TYPES.has("egg.found"));
});
