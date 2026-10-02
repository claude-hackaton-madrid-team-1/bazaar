import { test } from "node:test";
import assert from "node:assert/strict";
import { apply, createState, type GameEvent } from "../lib/state.ts";
import { cardStats, deltaText, deltaTone, marketTape, median, sparkEnd, sparkPath, teamStats } from "../lib/views/market.ts";

let nextId = 1;
let nextSettlement = 900;
let nextAsset = 5000;
const ev = (type: string, payload: Record<string, any> = {}, tick = 10): GameEvent => ({ id: nextId++, tick, t: 0.1, type, scope: "public", actor: "", payload });

const trade = (seller: string, buyer: string, ref: string, price: number, { tick = 10, venue = "rastro" as string | null, fee = 0, name = `Card ${ref}` } = {}) => {
  const persona = seller === "abuela" ? "abuela" : null;
  return ev("settlement", {
    settlement: nextSettlement++, kind: "trade", parties: [seller, buyer], venue: persona ? null : venue, persona, fee, price,
    items: [{ id: nextAsset++, ref, serial: 1, kind: "card", name, frm: seller, to: buyer }],
  }, tick);
};

const fresh = () => {
  const s = createState();
  apply(s, ev("agent.hello", { team: "t01", name: "Team 1" }));
  return s;
};

test("the tape shows only other teams' trades by default, ours when asked", () => {
  const s = fresh();
  apply(s, trade("t02", "t03", "LAT-01", 12, { tick: 5 }));
  apply(s, trade("t01", "t04", "MAL-06", 30, { tick: 6 }));
  apply(s, trade("t05", "t02", "SAL-02", 9, { tick: 7 }));
  assert.deepEqual(marketTape(s).map((r) => r.ref), ["SAL-02", "LAT-01"]);
  assert.deepEqual(marketTape(s, { include: "all" }).map((r) => [r.ref, r.ours]), [["SAL-02", false], ["MAL-06", true], ["LAT-01", false]]);
});

test("the tape search matches team, card, venue and ids", () => {
  const s = fresh();
  apply(s, trade("t02", "t03", "LAT-01", 12, { venue: "rastro", name: "El Rastro" }));
  apply(s, trade("abuela", "t05", "RET-03", 8));
  apply(s, trade("t06", "t07", "MAL-12", 400, { venue: "subasta" }));
  const [last] = s.tape;
  const refs = (query: string) => marketTape(s, { query }).map((r) => r.ref);
  assert.deepEqual(refs("T05"), ["RET-03"]);
  assert.deepEqual(refs("abuela"), ["RET-03"]);
  assert.deepEqual(refs("mal-"), ["MAL-12"]);
  assert.deepEqual(refs("el rastro"), ["LAT-01"]);
  assert.deepEqual(refs("subasta"), ["MAL-12"]);
  assert.deepEqual(refs(`s${last.settlementId}`), ["MAL-12"]);
  assert.deepEqual(refs(`#${last.assetId}`), ["MAL-12"]);
  assert.deepEqual(refs(`e${last.eventId}`), ["MAL-12"]);
  assert.deepEqual(refs("  "), ["MAL-12", "RET-03", "LAT-01"]);
  assert.deepEqual(refs("nobody"), []);
});

test("each tape row carries its book price and the delta against it", () => {
  const s = fresh();
  apply(s, trade("t02", "t03", "LAT-01", 15));
  apply(s, trade("t02", "t03", "LAT-06", 20));
  apply(s, trade("t02", "t03", "LAT-09", 70));
  apply(s, trade("t02", "t03", "sobre_barrio", 30));
  assert.deepEqual(marketTape(s).map((r) => [r.ref, r.book, r.delta]),
    [["sobre_barrio", null, null], ["LAT-09", 70, 0], ["LAT-06", 25, -0.2], ["LAT-01", 10, 0.5]]);
});

test("median of odd and even counts", () => {
  assert.equal(median([5, 1, 3]), 3);
  assert.equal(median([4, 1, 3, 10]), 3.5);
  assert.equal(median([]), null);
});

test("card prices per ref: count, last, median, min, max, book, trend, sorted by trades", () => {
  const s = fresh();
  apply(s, trade("t02", "t03", "LAT-01", 8, { tick: 1 }));
  apply(s, trade("t04", "t05", "MAL-06", 30, { tick: 2 }));
  apply(s, trade("t02", "t06", "LAT-01", 14, { tick: 3 }));
  apply(s, trade("t01", "t03", "LAT-01", 99, { tick: 4 }));
  apply(s, trade("t03", "t02", "LAT-01", 11, { tick: 5 }));
  apply(s, trade("t07", "t08", "CHA-02", 10, { tick: 6 }));
  const stats = cardStats(s);
  assert.deepEqual(stats.map((c) => [c.ref, c.trades]), [["LAT-01", 3], ["CHA-02", 1], ["MAL-06", 1]]);
  const lat = stats[0];
  assert.deepEqual([lat.name, lat.last, lat.median, lat.min, lat.max, lat.book, lat.lastTick], ["Card LAT-01", 11, 11, 8, 14, 10, 5]);
  assert.deepEqual(lat.trend, [8, 14, 99, 11]);
  assert.deepEqual(cardStats(s, "all")[0].trades, 4);
  assert.equal(cardStats(s, "all")[0].max, 99);
});

test("teams: trades, volume, buyer vs seller, last tick, never us, sorted by trades", () => {
  const s = fresh();
  apply(s, trade("t02", "t03", "LAT-01", 10, { tick: 1 }));
  apply(s, trade("abuela", "t02", "RET-03", 8, { tick: 4 }));
  apply(s, trade("t03", "t02", "MAL-06", 25, { tick: 2 }));
  apply(s, trade("t02", "t01", "SAL-09", 70, { tick: 9 }));
  const teams = teamStats(s);
  assert.deepEqual(teams.map((t) => [t.team, t.trades, t.volume, t.asBuyer, t.asSeller, t.lastTick]),
    [["t02", 3, 43, 2, 1, 4], ["t03", 2, 35, 1, 1, 2], ["abuela", 1, 8, 0, 1, 4]]);
  const all = teamStats(s, "all");
  assert.equal(all.find((t) => t.team === "t01"), undefined);
  assert.deepEqual(all.map((t) => [t.team, t.trades, t.volume, t.lastTick])[0], ["t02", 4, 113, 9]);
});

test("sparkline path spans the box, flat and short series are safe", () => {
  assert.equal(sparkPath([], 60, 16), "");
  assert.equal(sparkPath([5], 60, 16), "");
  assert.equal(sparkPath([0, 10], 60, 16), "M1,15L59,1");
  assert.equal(sparkPath([4, 4, 4], 60, 16), "M1,8L30,8L59,8");
});

test("sparkline end dot sits on the last point", () => {
  assert.deepEqual(sparkEnd([0, 10], 60, 16), { x: 59, y: 1 });
  assert.equal(sparkEnd([3], 60, 16), null);
});

test("book delta reads as an arrow and percent, above book in red, below in green", () => {
  assert.deepEqual([deltaText(0.5), deltaText(-0.204), deltaText(0), deltaText(null)], ["▲ 50%", "▼ 20%", "= 0%", ""]);
  assert.deepEqual([deltaTone(0.5), deltaTone(-0.2), deltaTone(0), deltaTone(0.003), deltaTone(null)], ["bad", "good", "", "", ""]);
});
