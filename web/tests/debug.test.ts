import { test } from "node:test";
import assert from "node:assert/strict";
import { apply, createState, LIMITS, type GameEvent } from "../lib/state.ts";
import { debugRows, familyOf, streamStats, summarize } from "../lib/views/debug.ts";

let nextId = 1;
const ev = (type: string, payload: Record<string, any> = {}, tick = 10, actor = ""): GameEvent => ({ id: nextId++, tick, t: 0.1, type, scope: "public", actor, payload });

const fresh = () => {
  const s = createState();
  apply(s, ev("agent.hello", { team: "t01", name: "Team 1" }, 1, "t01"));
  return s;
};

const ask = (thread = 61) => ev("thread.message", {
  thread, team: "t01", with: "abuela", sender: "abuela", message: 301, text: "30 P, cariño",
  offer: { id: 334, maker: "abuela", to: "t01", give: { cash: 0, types: ["pack:sobre_barrio"] }, want: { cash: 30 }, final: true },
}, 10, "abuela");

const marketTrade = (settlement = 9, tick = 10) => ev("settlement", {
  settlement, parties: ["t04", "t11"], venue: "ladder", price: 14,
  items: [{ id: 88, ref: "MAL-02", serial: 3, frm: "t04", to: "t11" }],
}, tick, "t04");

test("familyOf groups types by prefix", () => {
  assert.deepEqual(
    ["agent.thought", "agent.me", "thread.message", "thread.closed", "settlement", "duel.result", "clock", "market.quote", "settlementx"].map(familyOf),
    ["agent", "agent", "thread", "thread", "settlement", "duel", "clock", "unknown", "unknown"],
  );
});

test("summarize reads one line out of each payload", () => {
  assert.equal(summarize(ask()), "thread 61 · abuela → t01 · 30 P final · “30 P, cariño”");
  assert.equal(summarize(marketTrade()), "settlement 9 · MAL-02 t04→t11 14 P");
  assert.equal(summarize(ev("thread.closed", { thread: 61 })), "thread 61 closed");
  assert.equal(summarize(ev("agent.action", { kind: "say", summary: "bid 18 P" })), "say · bid 18 P");
  assert.equal(summarize(ev("agent.phase", { phase: "decide", goal: "complete La Latina" })), "decide · complete La Latina");
  assert.equal(summarize(ev("clock", { day: "sat", tick_seconds: 30 }, 144)), "tick 144 · sat · 30s");
  assert.equal(summarize(ev("duel.message", { duel: 4, sender: "t07", price: 20, days: 2 })), "duel 4 · t07 · 20 P · 2d");
  assert.equal(summarize(ev("duel.result", { duel: 4, deal: true, price: 21, points: 3.5 })), "duel 4 · deal @ 21 P · +3.5");
  assert.equal(summarize(ev("duel.result", { duel: 4, deal: false })), "duel 4 · no deal");
  assert.equal(summarize(ev("agent.me", { cash: 412, score: { score: 18.4, rank: 9 }, assets: [{}, {}] })), "412 P · score 18.4 · rank 9 · 2 assets");
});

test("summarize truncates thoughts and unknown payloads and survives junk", () => {
  const long = "x".repeat(300);
  const line = summarize(ev("agent.thought", { text: long }));
  assert.ok(line.length <= 100 && line.endsWith("…"));
  assert.ok(summarize(ev("market.quote", { blob: long })).length <= 100);
  assert.equal(summarize(ev("thread.message", { thread: 5, sender: "x", text: { evil: 1 } })), "thread 5 · x → ? · “[object Object]”");
  assert.equal(summarize({ id: 1, type: "settlement", payload: {} }), "settlement ? · 0 P");
});

test("rows come newest first and mark ours vs market", () => {
  const s = fresh();
  const a = ask();
  const m = marketTrade();
  apply(s, a);
  apply(s, m);
  const { rows } = debugRows(s, { source: "all" });
  assert.deepEqual(rows.map((r) => [r.id, r.family, r.ours]), [[m.id, "settlement", false], [a.id, "thread", true], [rows[2].id, "agent", true]]);
  assert.equal(rows[0].summary, "settlement 9 · MAL-02 t04→t11 14 P");
  assert.deepEqual([rows[0].tick, rows[0].actor, rows[0].scope, rows[0].type, rows[0].known], [10, "t04", "public", "settlement", true]);
});

test("source ours reads the mine list beyond the 500 window, market drops ours", () => {
  const s = fresh();
  const old = ask();
  apply(s, old);
  for (let i = 0; i < LIMITS.events + 10; i++) apply(s, marketTrade(100 + i));
  assert.ok(!s.events.includes(old));
  const ours = debugRows(s, { source: "ours" });
  assert.ok(ours.rows.some((r) => r.id === old.id));
  assert.ok(ours.rows.every((r) => r.ours));
  const market = debugRows(s, { source: "market", limit: 1000 });
  assert.equal(market.matched, LIMITS.events);
  assert.ok(market.rows.every((r) => !r.ours));
  assert.equal(debugRows(s, { source: "all", limit: 50 }).rows.length, 50);
});

test("family, query and unknown filters combine, family counts ignore the family filter", () => {
  const s = fresh();
  apply(s, ask());
  apply(s, marketTrade(9));
  apply(s, marketTrade(10));
  const weird = ev("market.quote", { ref: "LAT-09", bid: 3 }, 10, "t22");
  apply(s, weird);
  const bySettlement = debugRows(s, { source: "all", families: ["settlement"] });
  assert.deepEqual(bySettlement.rows.map((r) => r.family), ["settlement", "settlement"]);
  assert.deepEqual(bySettlement.families, { agent: 1, thread: 1, settlement: 2, unknown: 1 });
  assert.deepEqual(debugRows(s, { source: "all", families: ["thread", "unknown"] }).rows.map((r) => r.type), ["market.quote", "thread.message"]);
  assert.deepEqual(debugRows(s, { source: "all", query: "LAT-09" }).rows.map((r) => r.id), [weird.id]);
  assert.deepEqual(debugRows(s, { source: "all", query: "t22" }).rows.map((r) => r.id), [weird.id]);
  assert.deepEqual(debugRows(s, { source: "all", query: `#${weird.id}` }).rows.map((r) => r.id), [weird.id]);
  assert.equal(debugRows(s, { source: "all", query: "MAL-02 \"settlement\":10" }).rows.length, 1);
  assert.equal(debugRows(s, { source: "all", query: "ABUELA" }).rows.length, 1);
  const unknown = debugRows(s, { source: "all", unknownOnly: true });
  assert.deepEqual(unknown.rows.map((r) => [r.type, r.known]), [["market.quote", false]]);
  assert.deepEqual(unknown.families, { unknown: 1 });
});

test("stream stats count the window, types, unknowns and the last 10 ticks", () => {
  const s = fresh();
  for (let tick = 1; tick <= 14; tick++) apply(s, ev("clock", { day: "sat" }, tick));
  apply(s, marketTrade(1, 14));
  apply(s, marketTrade(2, 14));
  apply(s, ev("market.quote", {}, 13));
  const st = streamStats(s);
  assert.equal(st.window, 18);
  assert.equal(st.mine, 15);
  assert.equal(st.unknown, 1);
  assert.deepEqual(st.byType, [["clock", 14], ["settlement", 2], ["agent.hello", 1], ["market.quote", 1]]);
  assert.deepEqual(st.perTick.map((p) => p.tick), [5, 6, 7, 8, 9, 10, 11, 12, 13, 14]);
  assert.deepEqual(st.perTick.map((p) => p.count), [1, 1, 1, 1, 1, 1, 1, 1, 2, 3]);
  assert.equal(st.lastId, s.events.at(-1)!.id);
});

test("stream stats on an empty state", () => {
  const st = streamStats(createState());
  assert.deepEqual([st.window, st.unknown, st.byType, st.lastId], [0, 0, [], null]);
  assert.ok(st.perTick.length > 0 && st.perTick.every((p) => p.tick >= 0 && p.count === 0));
});
