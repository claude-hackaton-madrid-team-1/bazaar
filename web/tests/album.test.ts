import { test } from "node:test";
import assert from "node:assert/strict";
import { apply, createState, type GameEvent } from "../lib/state.ts";
import { albumRows, albumSummary, scoreBars, series, sparkline } from "../lib/views/album.ts";

let nextId = 1;
const ev = (type: string, payload: Record<string, any> = {}, tick = 10): GameEvent => ({ id: nextId++, tick, t: 0.1, type, scope: "public", actor: "", payload });

const card = (id: number, ref: string, serial: number, your_value: number) => ({ id, kind: "card", ref, serial, your_value });

const ME = {
  cash: 412,
  score: { score: 18.4, rank: 9, duel_points: 4, ladder_points: 6.5, neg_points: 7.9, mm_points: 0, deals: 3 },
  album: {
    pages: [
      { set: "LAT", name: "La Latina", have: 2, of: 10, complete: false, master: false },
      { set: "RET", name: "El Retiro", have: 10, of: 10, complete: true, master: false },
      { set: "MAL", name: "Malasaña", have: 9, of: 10, complete: false, master: false },
    ],
  },
  assets: [
    card(1, "LAT-03", 4, 6),
    card(2, "LAT-09", 1, 40),
    card(3, "LAT-09", 2, 40),
    card(4, "LAT-09", 3, 40),
    ...Array.from({ length: 10 }, (_, i) => card(10 + i, `RET-${String(i + 1).padStart(2, "0")}`, 1, 12)),
    card(30, "RET-11", 1, 200),
    ...Array.from({ length: 9 }, (_, i) => card(40 + i, `MAL-${String(i + 1).padStart(2, "0")}`, 1, 15)),
    card(50, "MAL-02", 2, 15),
    { id: 99, kind: "pack", ref: "sobre_barrio" },
  ],
};

const fresh = () => {
  const s = createState();
  apply(s, ev("agent.hello", { team: "t01", name: "Team 1" }));
  return s;
};

const loaded = () => {
  const s = fresh();
  apply(s, ev("agent.me", ME, 5));
  apply(s, ev("settlement", { settlement: 1, parties: ["t02", "t03"], price: 33, items: [{ ref: "LAT-09", frm: "t02", to: "t03" }] }, 6));
  apply(s, ev("settlement", { settlement: 2, parties: ["t02", "t03"], price: 31, items: [{ ref: "LAT-09", frm: "t03", to: "t02" }] }, 7));
  return s;
};

test("no agent.me yet means no rows and an empty summary", () => {
  const s = fresh();
  assert.deepEqual(albumRows(s), []);
  assert.deepEqual(albumSummary(s), { pages: 0, complete: 0, master: 0, missing: 0, duplicates: { count: 0, refs: [] }, cheapest: null });
});

test("each page row has twelve slots with rarity, copies held and prices", () => {
  const s = loaded();
  const lat = albumRows(s, { sort: "set" })[0];
  assert.deepEqual([lat.set, lat.name, lat.color, lat.have, lat.of, lat.missing, lat.complete, lat.master],
    ["LAT", "La Latina", "#F4A259", 2, 10, 8, false, false]);
  assert.equal(lat.slots.length, 12);
  assert.deepEqual(lat.slots.map((c) => c.ref).slice(9), ["LAT-10", "LAT-11", "LAT-12"]);
  assert.deepEqual(lat.slots.map((c) => c.rarity).slice(7, 12), ["uncommon", "rare", "rare", "epic", "legendary"]);
  assert.deepEqual(lat.slots.map((c) => c.count), [0, 0, 1, 0, 0, 0, 0, 0, 3, 0, 0, 0]);
  const rare = lat.slots[8];
  assert.deepEqual([rare.num, rare.color, rare.book, rare.value, rare.last, rare.spare],
    [9, "#4C8DFF", 70, 40, 31, true]);
  assert.deepEqual(rare.held, [{ id: 2, serial: 1 }, { id: 3, serial: 2 }, { id: 4, serial: 3 }]);
  assert.equal(rare.title, "LAT-09 · rare\nbook 70 P · ours 40 P · market 31 P\nheld ×3: #2 s1, #3 s2, #4 s3");
  const gap = lat.slots[0];
  assert.deepEqual([gap.count, gap.value, gap.last, gap.spare], [0, null, null, false]);
  assert.equal(gap.title, "LAT-01 · common\nbook 10 P · ours — · market —\nmissing");
});

test("rows sort closest to completion first, or keep set order", () => {
  const s = loaded();
  assert.deepEqual(albumRows(s).map((r) => [r.set, r.missing]), [["RET", 0], ["MAL", 1], ["LAT", 8]]);
  assert.deepEqual(albumRows(s, { sort: "set" }).map((r) => r.set), ["LAT", "RET", "MAL"]);
});

test("ties on missing slots break on the special slots, then set order", () => {
  const s = fresh();
  apply(s, ev("agent.me", {
    album: { pages: [{ set: "LAV", have: 0, of: 10 }, { set: "SAL", have: 0, of: 10 }, { set: "CHA", have: 0, of: 10 }] },
    assets: [card(1, "CHA-12", 1, 300)],
  }));
  assert.deepEqual(albumRows(s).map((r) => r.set), ["CHA", "LAV", "SAL"]);
  assert.equal(albumRows(s)[1].name, "Lavapiés");
});

test("a page without have counts the page slots we hold", () => {
  const s = fresh();
  apply(s, ev("agent.me", { album: { pages: [{ set: "SAL" }] }, assets: [card(1, "SAL-01", 1, 9), card(2, "SAL-01", 2, 9), card(3, "SAL-11", 1, 90)] }));
  const [row] = albumRows(s);
  assert.deepEqual([row.name, row.have, row.of, row.missing, row.complete], ["Salamanca", 1, 10, 9, false]);
});

test("summary counts complete pages, missing slots, spare copies and the cheapest missing card", () => {
  const sum = albumSummary(loaded());
  assert.deepEqual([sum.pages, sum.complete, sum.master, sum.missing], [3, 1, 0, 9]);
  assert.deepEqual(sum.duplicates, { count: 3, refs: [{ ref: "LAT-09", spare: 2 }, { ref: "MAL-02", spare: 1 }] });
  assert.deepEqual(sum.cheapest, { ref: "LAT-01", book: 10, rarity: "common", page: "La Latina" });
});

test("cheapest missing prefers the page closest to completion on a book tie", () => {
  const s = fresh();
  apply(s, ev("agent.me", {
    album: { pages: [{ set: "LAV", have: 0, of: 10 }, { set: "SAL", have: 1, of: 10 }] },
    assets: [card(1, "SAL-01", 1, 9)],
  }));
  assert.deepEqual(albumSummary(s).cheapest, { ref: "SAL-02", book: 10, rarity: "common", page: "Salamanca" });
});

test("score bars break the total into its four sources", () => {
  const sc = scoreBars(loaded());
  assert.deepEqual([sc.total, sc.rank, sc.deals], [18.4, 9, 3]);
  assert.deepEqual(sc.bars.map((b) => [b.key, b.label, b.points]), [
    ["duel_points", "Duels", 4], ["ladder_points", "Ladder", 6.5], ["neg_points", "Negotiation", 7.9], ["mm_points", "Market-making", 0],
  ]);
  assert.deepEqual(sc.bars.map((b) => Math.round(b.pct)), [40, 65, 79, 0]);
});

test("score bars scale to the largest part and never go negative", () => {
  const s = fresh();
  apply(s, ev("agent.me", { score: { score: 22, duel_points: 30, ladder_points: -8, neg_points: 15 } }));
  const sc = scoreBars(s);
  assert.deepEqual([sc.total, sc.rank, sc.deals], [22, null, null]);
  assert.deepEqual(sc.bars.map((b) => [b.points, b.pct]), [[30, 100], [-8, 0], [15, 50], [0, 0]]);
  assert.deepEqual(scoreBars(fresh()).bars.map((b) => b.pct), [0, 0, 0, 0]);
});

test("series reads score or cash per snapshot, and a sparkline draws it", () => {
  const s = fresh();
  apply(s, ev("agent.me", { cash: 100, score: { score: 1 } }, 1));
  apply(s, ev("agent.me", { cash: 80, score: { score: 3 } }, 2));
  apply(s, ev("agent.me", { cash: 120, score: { score: 2 } }, 3));
  assert.deepEqual(series(s.history, "cash"), [100, 80, 120]);
  assert.deepEqual(series(s.history, "score"), [1, 3, 2]);
  const line = sparkline([100, 80, 120], { w: 104, h: 26 });
  assert.deepEqual(line, { d: "M2,13L52,23L102,3", x: 102, y: 3 });
  assert.equal(sparkline([5], { w: 104, h: 26 }), null);
  assert.deepEqual(sparkline([5, 5], { w: 104, h: 26 }), { d: "M2,13L102,13", x: 102, y: 13 });
});
