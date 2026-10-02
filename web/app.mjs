import { apply, createState } from "./state.mjs";

const SETS = {
  LAV: { name: "Lavapiés", color: "#E4572E" },
  MAL: { name: "Malasaña", color: "#9D4EDD" },
  LAT: { name: "La Latina", color: "#F4A259" },
  SAL: { name: "Salamanca", color: "#2E86AB" },
  RET: { name: "El Retiro", color: "#3BB273" },
  CHA: { name: "Chamberí", color: "#C1666B" },
};
const RARITY_COLOR = { common: "#9AA4B8", uncommon: "#3DDC97", rare: "#4C8DFF", epic: "#B061FF", legendary: "#FFC44D" };
const SLOT_RARITY = [...Array(5).fill("common"), ...Array(3).fill("uncommon"), "rare", "rare", "epic", "legendary"];
const BOOK = [10, 10, 10, 10, 10, 25, 25, 25, 70, 70, 180, 450];
const PHASES = ["observe", "decide", "act"];
const LOG_ICON = { thought: "·", say: "↗", accept: "✔", walk: "✖", open: "+", list: "≡", flag: "⚑" };
const SUSPICIOUS = /ignore (all |any )?previous|system:|instructions|transfer \d+/i;
const SVG_NS = "http://www.w3.org/2000/svg";

const $ = (id) => document.getElementById(id);

function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props ?? {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "tip") el.dataset.tip = v;
    else if (k === "eid") el.dataset.eid = v;
    else if (k === "style") el.style.cssText = v;
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid instanceof Node ? kid : String(kid));
  return el;
}

function s(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    if (k === "tip") el.dataset.tip = v;
    else if (k === "eid") el.dataset.eid = v;
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid instanceof Node ? kid : String(kid));
  return el;
}

const slotOf = (ref) => {
  const n = Number(String(ref).split("-")[1]);
  return Number.isFinite(n) ? n - 1 : null;
};
const bookOf = (ref) => BOOK[slotOf(ref)] ?? null;
const rarityOf = (ref) => SLOT_RARITY[slotOf(ref)] ?? null;
const fmtP = (v) => (v == null ? "—" : `${Math.round(v * 10) / 10} P`);
const signed = (v) => (v == null ? "" : `${v >= 0 ? "+" : "−"}${Math.abs(Math.round(v * 10) / 10)} P`);

const idChip = (label, eid, tip) => h("span", { class: eid != null ? "id link" : "id", eid, tip }, label);

function refChip(ref) {
  const set = SETS[String(ref).slice(0, 3)];
  return h("span", { class: "ref" }, h("i", { style: `background:${set?.color ?? "var(--text-muted)"}` }), ref);
}

const params = new URLSearchParams(location.search);
let wsUrl = params.get("ws") || `${location.protocol === "https:" ? "wss" : "ws"}://${location.host || "localhost:8777"}/events`;
let state = createState();
let sock = null;
let status = "connecting";
let backoff = 500;
let paused = false;
let dirty = true;
let selected = null;
let tapeMode = "all";
let tapeQuery = "";
let evQuery = "";
let hideAgent = false;
let lastTickAt = performance.now();
let tickGap = null;
let received = 0;
const names = {};

function connect() {
  status = "connecting";
  try {
    sock = new WebSocket(wsUrl);
  } catch {
    status = "bad url";
    dirty = true;
    return;
  }
  sock.onopen = () => {
    status = "live";
    backoff = 500;
    state = createState();
    received = 0;
    dirty = true;
  };
  sock.onmessage = (msg) => {
    let e;
    try {
      e = JSON.parse(msg.data);
    } catch {
      return;
    }
    if (e.type === "clock" && e.tick !== state.tick) {
      const now = performance.now();
      tickGap = now - lastTickAt;
      lastTickAt = now;
    }
    if (e.type === "settlement") for (const it of e.payload?.items ?? []) if (it.name) names[it.ref] = it.name;
    if (e.type === "agent.me") for (const a of e.payload?.assets ?? []) if (a.name) names[a.ref] = a.name;
    apply(state, e);
    received += 1;
    dirty = true;
  };
  sock.onclose = () => {
    status = "reconnecting";
    dirty = true;
    setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, 10000);
  };
}

function renderHeader() {
  $("team").replaceChildren(state.name || "—", h("span", { class: "id" }, state.team || ""));
  $("day").textContent = state.day || "—";
  $("tick").textContent = `tick ${state.tick}`;
  const dot = $("wsdot");
  dot.className = `dot ${status === "live" ? "live" : status === "connecting" ? "" : "down"}`;
  $("wsstatus").textContent = paused ? "paused" : status;
  if (document.activeElement !== $("wsurl")) $("wsurl").value = wsUrl;
}

function sparkline(values, { w = 160, hgt = 28 } = {}) {
  const svg = s("svg", { viewBox: `0 0 ${w} ${hgt}`, preserveAspectRatio: "none" });
  if (values.length < 2) return svg;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const x = (i) => (i / (values.length - 1)) * (w - 4) + 2;
  const y = (v) => hgt - 3 - (hi === lo ? 0.5 : (v - lo) / (hi - lo)) * (hgt - 6);
  svg.append(s("path", { class: "spark", d: values.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join("") }));
  svg.append(s("circle", { class: "spark-dot", cx: x(values.length - 1), cy: y(values.at(-1)), r: 3 }));
  return svg;
}

function tile(label, value, small, foot, spark, tip) {
  return h("div", { class: "tile", tip }, h("div", { class: "label" }, label),
    h("div", { class: "value" }, value, small ? h("small", {}, small) : null),
    foot ? h("div", { class: "foot" }, foot) : null, spark);
}

function renderTiles() {
  const { trades: ourTrades, gain: gained } = state.ours;
  const open =Object.values(state.threads).filter((t) => t.status === "open").length;
  const duelsOpen = Object.values(state.duels).filter((d) => d.status === "open").length;
  const sc = state.score ?? {};
  const hist = state.history;
  const meId = state.meEventId != null ? `agent.me e${state.meEventId}` : "waiting for agent.me";
  const rate = state.events.length > 1 ? `${received} events` : "no events yet";
  $("tiles").replaceChildren(
    tile("Cash", `${state.cash} P`, null, meId, sparkline(hist.map((p) => p.cash)), "cash after every agent.me snapshot"),
    tile("Score", (sc.score ?? 0).toFixed(1), `rank #${sc.rank ?? "—"}`, `deals ${sc.deals ?? 0}`, sparkline(hist.map((p) => p.score)), "score after every agent.me snapshot"),
    tile("Value created by us", h("span", { class: gained >= 0 ? "good" : "bad" }, signed(gained)), null, `${ourTrades} trades this session, at our private values`),
    tile("Open now", `${open}`, "threads", `${duelsOpen} duel${duelsOpen === 1 ? "" : "s"} live`),
    tile("Stream", `${state.events.length}`, "kept", `${rate} · last e${state.events.at(-1)?.id ?? "—"}`),
  );
}

function renderLoop() {
  const phase = $("phase");
  phase.replaceChildren(...PHASES.flatMap((p, i) => [h("span", { class: p === state.phase ? "on" : "" }, p.toUpperCase()), ...(i < 2 ? [h("i", {}, "→")] : [])]), h("i", {}, "↺"));
  $("goal").replaceChildren("goal ", h("b", {}, state.goal || "—"));
  const items = state.log.slice(-160).reverse().map((l) =>
    h("li", { class: `${l.kind}${selected === l.eventId ? " sel" : ""}`, eid: l.eventId },
      h("span", { class: "t" }, l.tick), h("span", { class: "id" }, `e${l.eventId}`),
      h("span", { class: "icon" }, LOG_ICON[l.kind] ?? "›"), h("span", { class: "txt" }, l.text)));
  $("log").replaceChildren(...items);
}

function priceChart(th) {
  const W = Math.max(240, ($("threads").clientWidth || 360) - 22);
  const H = 92;
  const pad = { l: 30, r: 34, t: 8, b: 14 };
  const pts = th.offers.filter((o) => o.price != null);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": `price history of thread ${th.id}` });
  if (!pts.length) return svg;
  const prices = pts.map((o) => o.price);
  const lo = Math.floor(Math.min(...prices) * 0.9);
  const hi = Math.ceil(Math.max(...prices) * 1.1) + 1;
  const n = Math.max(th.offers.length, 4);
  const x = (i) => pad.l + (i / (n - 1)) * (W - pad.l - pad.r);
  const y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b);
  for (const v of [lo, Math.round((lo + hi) / 2), hi]) {
    svg.append(s("line", { class: "gridline", x1: pad.l, x2: W - pad.r, y1: y(v), y2: y(v) }));
    svg.append(s("text", { x: pad.l - 4, y: y(v) + 3, "text-anchor": "end" }, v));
  }
  svg.append(s("text", { x: pad.l, y: H - 2 }, "offers →"));
  for (const side of ["them", "us"]) {
    const series = th.offers.map((o, i) => ({ ...o, i })).filter((o) => o.side === side && o.price != null);
    if (!series.length) continue;
    svg.append(s("path", { class: `line-${side}`, d: series.map((o, k) => `${k ? "L" : "M"}${x(o.i)},${y(o.price)}`).join("") }));
    for (const o of series) {
      svg.append(s("circle", { class: `dot-${side}`, cx: x(o.i), cy: y(o.price), r: o.final ? 5 : 4 }));
    }
    const last = series.at(-1);
    svg.append(s("text", { x: x(last.i) + 8, y: y(last.price) + 3 }, `${last.price}`));
    for (const o of series) {
      const tip = [`o${o.offerId} · m${o.messageId} · e${o.eventId}`, `${o.maker} → ${o.to}`, `${o.price} P${o.final ? " · FINAL" : ""}`,
        `created t${o.createdTick} · expires t${o.expiresTick}`, o.assets.length ? `assets ${o.assets.map((a) => `#${a}`).join(" ")}` : null]
        .filter(Boolean).join("\n");
      svg.append(s("circle", { class: "hit", cx: x(o.i), cy: y(o.price), r: 11, tip, eid: o.eventId }));
    }
  }
  return svg;
}

function threadCard(th) {
  const closed = th.status !== "open";
  const last = th.offers.at(-1);
  const left = th.expiresTick != null ? th.expiresTick - state.tick : null;
  const head = h("div", { class: "head" },
    idChip(`#${th.id}`, last?.eventId, `thread ${th.id}`),
    h("span", { class: "who" }, th.with === "abuela" ? "🧶 Abuela" : th.with),
    h("span", { class: `badge ${th.side}` }, th.side.toUpperCase()),
    refChip(th.topic),
    h("span", { class: "id" }, `r${th.rounds}`),
    th.final && !closed ? h("span", { class: "badge final" }, "FINAL") : null,
    closed ? h("span", { class: "badge" }, "closed") : left != null ? h("span", { class: `badge${left <= 1 ? " warn" : ""}` }, `⏱ ${left}t`) : null,
    th.ourPrice != null && th.theirPrice != null ? h("span", { class: "id" }, `gap ${Math.abs(th.theirPrice - th.ourPrice)} P`) : null);
  const ids = last ? h("div", { class: "ids" },
    idChip(`o${last.offerId}`, last.eventId, "last offer id"),
    idChip(`m${last.messageId}`, last.eventId, "last message id"),
    idChip(`e${last.eventId}`, last.eventId, "event id"),
    h("span", { class: "id" }, `${last.maker} → ${last.to}`),
    h("span", { class: "id" }, `t${last.createdTick} → exp t${last.expiresTick}`),
    last.assets.length ? h("span", { class: "id" }, `assets ${last.assets.map((a) => `#${a}`).join(" ")}`) : null) : null;
  const quote = th.lastText ? h("div", { class: "quote" },
    SUSPICIOUS.test(th.lastText) ? h("span", { class: "inj", tip: "Counterparty text carries instructions. Only the structured offer counts." }, "⚠ injection?") : null,
    `“${th.lastText}”`) : null;
  return h("div", { class: `thread${closed ? " closed" : ""}` }, head, ids, priceChart(th), quote);
}

function renderThreads() {
  const all = Object.values(state.threads);
  const open = all.filter((t) => t.status === "open").sort((a, b) => a.id - b.id);
  const recent = all.filter((t) => t.status !== "open").sort((a, b) => b.id - a.id).slice(0, 3);
  const kids = [];
  if (!open.length) kids.push(h("div", { class: "empty" }, "No open threads right now."));
  kids.push(...open.map(threadCard), ...recent.map(threadCard));
  $("threads").replaceChildren(...kids);

  const duels = Object.values(state.duels).sort((a, b) => b.id - a.id).slice(0, 6);
  if (!duels.length) {
    $("duels").replaceChildren(h("div", { class: "empty" }, "No duels yet."));
    return;
  }
  const table = h("table", { class: "table" },
    h("thead", {}, h("tr", {}, ...["duel", "role", "us", "rival", "rounds", "result", "event"].map((c) => h("th", {}, c)))),
    h("tbody", {}, ...duels.map((d) => h("tr", { eid: d.lastEventId, class: selected === d.lastEventId ? "sel" : "" },
      h("td", {}, `#${d.id}`), h("td", {}, d.role),
      h("td", { class: "party-us" }, d.ourPrice != null ? `${d.ourPrice} P${d.ourDays != null ? ` · ${d.ourDays}d` : ""}` : "·"),
      h("td", {}, d.theirPrice != null ? `${d.theirPrice} P${d.theirDays != null ? ` · ${d.theirDays}d` : ""}` : "·"),
      h("td", { class: "r" }, d.rounds),
      h("td", { class: d.status === "deal" ? "good" : d.status === "no deal" ? "bad" : "" },
        d.status === "deal" ? `✔ @${d.dealPrice} · +${(d.points ?? 0).toFixed(1)}` : d.status === "no deal" ? "✖ no deal" : "live"),
      h("td", {}, h("span", { class: "id" }, `e${d.lastEventId}`))))));
  $("duels").replaceChildren(table);
}

function renderAlbum() {
  const rows = state.pages.map((page) => {
    const code = page.set;
    const cells = [];
    for (let i = 0; i < 12; i++) {
      if (i === 10) cells.push(h("span", { class: "gap" }));
      const ref = `${code}-${String(i + 1).padStart(2, "0")}`;
      const held = state.owned[ref] ?? [];
      const rarity = SLOT_RARITY[i];
      const tip = [`${ref}${names[ref] ? ` · ${names[ref]}` : ""}`, `${rarity} · book ${BOOK[i]} P`,
        held.length ? `held ×${held.length}: ${held.map((a) => `#${a.id} s${a.serial}`).join(", ")}` : "missing",
        held.length ? `your_value ${fmtP(state.values[ref])}` : null].filter(Boolean).join("\n");
      cells.push(h("span", { class: `cell${held.length ? " have" : ""}`, tip, style: held.length ? `background:${RARITY_COLOR[rarity]}` : `border-color:color-mix(in srgb, ${RARITY_COLOR[rarity]} 45%, var(--border))` },
        String(i + 1), held.length > 1 ? h("span", { class: "n" }, held.length) : null));
    }
    return h("div", { class: "page-row" },
      h("span", { class: "name" }, h("span", { class: "ref" }, h("i", { style: `background:${SETS[code]?.color}` }), code), page.name),
      h("span", { class: `count${page.complete ? " done" : ""}` }, `${page.have}/${page.of}${page.complete ? " ✔" : ""}`),
      h("div", { class: "cells" }, cells));
  });
  $("album").replaceChildren(...(rows.length ? rows : [h("div", { class: "empty" }, "Waiting for agent.me")]));
  $("rarity-legend").replaceChildren(...Object.entries(RARITY_COLOR).map(([r, c]) => h("span", {}, h("i", { style: `background:${c}` }), r)));
}

function renderScore() {
  const sc = state.score ?? {};
  const parts = [["Duels", "duel_points"], ["Ladder", "ladder_points"], ["Trades", "neg_points"], ["Market", "mm_points"]];
  const max = Math.max(10, ...parts.map(([, k]) => sc[k] ?? 0));
  $("score").replaceChildren(
    h("div", { class: "total" }, "total ", h("b", {}, (sc.score ?? 0).toFixed(1)), `  ·  rank #${sc.rank ?? "—"}`),
    ...parts.map(([label, key]) => {
      const v = sc[key] ?? 0;
      return h("div", { class: "row", tip: `${key} = ${v}` }, h("span", { class: "lbl" }, label),
        h("span", { class: "track" }, h("span", { class: "fill", style: `width:${(v / max) * 100}%` })),
        h("span", { class: "num" }, v.toFixed(1)));
    }));
}

function trendSvg(ref) {
  const values = (state.prices[ref] ?? []).slice(-12);
  const svg = s("svg", { class: "trend", viewBox: "0 0 72 16", preserveAspectRatio: "none" });
  if (values.length < 2) return svg;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const x = (i) => (i / (values.length - 1)) * 70 + 1;
  const y = (v) => 14 - (hi === lo ? 0.5 : (v - lo) / (hi - lo)) * 12;
  svg.append(s("path", { class: "trend-line", d: values.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join("") }));
  return svg;
}

const matches = (q, ...fields) => !q || fields.some((f) => String(f ?? "").toLowerCase().includes(q));

function renderTape() {
  const q = tapeQuery.toLowerCase();
  const rows = state.tape
    .filter((t) => tapeMode === "all" || t.ours)
    .filter((t) => matches(q, t.seller, t.buyer, t.ref, t.name, t.venue, `s${t.settlementId}`, `#${t.assetId}`, `e${t.eventId}`))
    .slice(0, 120);
  const ours = state.tape.filter((t) => t.ours);
  $("tapestats").textContent = `${state.tape.length} trades · ${state.tape.reduce((a, t) => a + t.price, 0)} P volume · ours ${ours.length}`;
  const head = h("thead", {}, h("tr", {}, ...[["tick"], ["event"], ["settle"], ["venue"], ["seller → buyer"], ["asset"], ["card"], [""], ["price", "r"], ["fee", "r"], ["vs book", "r"], ["trend"], ["our gain", "r"]]
    .map(([c, cls]) => h("th", { class: cls }, c))));
  const body = h("tbody", {}, ...rows.map((t) => {
    const book = bookOf(t.ref);
    const delta = book ? (t.price - book) / book : null;
    const party = (p) => h("span", { class: p === state.team ? "party-us" : "" }, p);
    return h("tr", { eid: t.eventId, class: `${t.ours ? "ours" : ""}${selected === t.eventId ? " sel" : ""}` },
      h("td", { class: "r" }, t.tick),
      h("td", {}, h("span", { class: "id" }, `e${t.eventId}`)),
      h("td", {}, h("span", { class: "id" }, `s${t.settlementId}`)),
      h("td", {}, t.venue),
      h("td", {}, party(t.seller), " → ", party(t.buyer)),
      h("td", {}, h("span", { class: "id", tip: `asset #${t.assetId} · serial ${t.serial} · ${t.kind}` }, `#${t.assetId}`)),
      h("td", {}, refChip(t.ref), h("span", { class: "id" }, ` s${t.serial}`)),
      h("td", { class: "name" }, t.name),
      h("td", { class: "r" }, `${t.price}`),
      h("td", { class: "r" }, t.fee ? `${t.fee}` : "·"),
      h("td", { class: `r ${delta > 0 ? "up" : delta < 0 ? "down" : ""}` }, delta == null ? "" : `${delta > 0 ? "▲" : delta < 0 ? "▼" : "="} ${Math.abs(Math.round(delta * 100))}%`),
      h("td", {}, trendSvg(t.ref)),
      h("td", { class: `r ${t.gain == null ? "" : t.gain >= 0 ? "good" : "bad"}` }, signed(t.gain)));
  }));
  $("tape").replaceChildren(head, body);
}

function summary(e) {
  const p = e.payload ?? {};
  switch (e.type) {
    case "thread.message": return `#${p.thread} m${p.message} o${p.offer?.id} ${p.sender}→${p.offer?.to} ${p.offer?.want?.cash || p.offer?.give?.cash || ""} P${p.offer?.final ? " FINAL" : ""}`;
    case "settlement": return `s${p.settlement} ${(p.items ?? []).map((i) => `#${i.id} ${i.ref} ${i.frm}→${i.to}`).join(", ")} @${p.price}`;
    case "agent.thought": return p.text;
    case "agent.action": return `${p.kind}: ${p.summary}`;
    case "agent.phase": return `${p.phase}${p.goal ? ` · ${p.goal}` : ""}`;
    case "agent.me": return `${p.cash} P · score ${p.score?.score} · ${(p.assets ?? []).length} assets`;
    case "agent.hello": return `${p.team} ${p.name}`;
    case "clock": return `${p.day} · ${p.tick_seconds}s ticks`;
    case "duel.message": return `#${p.duel} ${p.sender} ${p.price} P${p.days != null ? ` ${p.days}d` : ""}`;
    case "duel.result": return `#${p.duel} ${p.deal ? `deal @${p.price}` : "no deal"}`;
    case "thread.closed": return `#${p.thread}`;
    default: return JSON.stringify(p).slice(0, 80);
  }
}

function renderEvents() {
  const q = evQuery.toLowerCase();
  const rows = [];
  for (let i = state.events.length - 1; i >= 0 && rows.length < 150; i--) {
    const e = state.events[i];
    if (hideAgent && (e.type.startsWith("agent.") || e.type === "clock")) continue;
    if (!matches(q, e.type, e.actor, `e${e.id}`, summary(e))) continue;
    rows.push(e);
  }
  $("evstats").textContent = `${state.events.length} kept · ${received} received`;
  const kind = (t) => (t.startsWith("agent.") || t === "clock" ? "agent" : t.startsWith("thread") ? "thread" : t.startsWith("duel") ? "duel" : t);
  $("events").replaceChildren(
    h("thead", {}, h("tr", {}, ...["id", "tick", "type", "actor", "scope", "summary"].map((c) => h("th", {}, c)))),
    h("tbody", {}, ...rows.map((e) => h("tr", { eid: e.id, class: selected === e.id ? "sel" : "" },
      h("td", {}, h("span", { class: "id" }, `e${e.id}`)), h("td", { class: "r" }, e.tick),
      h("td", {}, h("span", { class: `type ${kind(e.type)}` }, e.type)), h("td", {}, e.actor || "·"),
      h("td", {}, h("span", { class: "id" }, e.scope)), h("td", { class: "name" }, summary(e))))));
}

function renderInspector() {
  const box = $("inspector");
  const e = selected != null ? state.byId[selected] : null;
  if (!e) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  $("insp-title").textContent = `e${e.id} · ${e.type}`;
  const p = e.payload ?? {};
  const meta = [["tick", e.tick], ["t", e.t], ["scope", e.scope], ["actor", e.actor || "·"], ["thread", p.thread], ["message", p.message],
    ["offer", p.offer?.id], ["settlement", p.settlement], ["duel", p.duel], ["assets", (p.items ?? p.offer?.give?.assets ?? []).map((a) => `#${a.id}`).join(" ") || null]];
  $("insp-meta").replaceChildren(...meta.filter(([, v]) => v != null && v !== "").map(([k, v]) => h("span", { class: "id" }, `${k} ${v}`)));
  $("insp-json").textContent = JSON.stringify(e, null, 2);
}

function renderTickBar() {
  const period = tickGap ?? state.tickSeconds * 1000;
  const frac = Math.min((performance.now() - lastTickAt) / period, 1);
  $("tickfill").style.width = `${paused ? 100 : frac * 100}%`;
}

function render() {
  renderHeader();
  renderTiles();
  renderLoop();
  renderThreads();
  renderAlbum();
  renderScore();
  renderTape();
  renderEvents();
  renderInspector();
}

function select(id) {
  selected = selected === id ? null : id;
  history.replaceState(null, "", selected == null ? location.pathname + location.search : `#e${selected}`);
  render();
}

function setTheme() {
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : !matchMedia("(prefers-color-scheme: light)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  try {
    localStorage.setItem("bazaar-theme", root.dataset.theme);
  } catch {}
}

function wire() {
  try {
    const saved = localStorage.getItem("bazaar-theme");
    if (saved) document.documentElement.dataset.theme = saved;
  } catch {}

  document.addEventListener("click", (ev) => {
    const target = ev.target.closest("[data-eid]");
    if (target && !ev.target.closest("#inspector")) select(Number(target.dataset.eid));
  });
  const tip = $("tip");
  document.addEventListener("mousemove", (ev) => {
    const target = ev.target.closest?.("[data-tip]");
    if (!target) {
      tip.hidden = true;
      return;
    }
    tip.textContent = target.dataset.tip;
    tip.hidden = false;
    const r = tip.getBoundingClientRect();
    tip.style.left = `${Math.min(ev.clientX + 14, innerWidth - r.width - 8)}px`;
    tip.style.top = `${Math.min(ev.clientY + 14, innerHeight - r.height - 8)}px`;
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.target.matches("input")) return;
    if (ev.key === " ") {
      ev.preventDefault();
      togglePause();
    } else if (ev.key === "Escape") {
      selected = null;
      render();
    }
  });
  const togglePause = () => {
    paused = !paused;
    $("pause").classList.toggle("on", paused);
    $("pause").textContent = paused ? "Resume" : "Pause";
    dirty = true;
    renderHeader();
  };
  $("pause").onclick = togglePause;
  $("theme").onclick = setTheme;
  $("insp-close").onclick = () => select(selected);
  $("insp-copy").onclick = () => navigator.clipboard?.writeText($("insp-json").textContent);
  $("wsform").onsubmit = (ev) => {
    ev.preventDefault();
    wsUrl = $("wsurl").value.trim();
    const url = new URL(location.href);
    url.searchParams.set("ws", wsUrl);
    history.replaceState(null, "", url);
    $("wsurl").blur();
    sock?.close();
  };
  for (const b of document.querySelectorAll("[data-tape]")) {
    b.onclick = () => {
      tapeMode = b.dataset.tape;
      for (const o of document.querySelectorAll("[data-tape]")) o.classList.toggle("on", o === b);
      renderTape();
    };
  }
  $("tapeq").oninput = (ev) => {
    tapeQuery = ev.target.value;
    renderTape();
  };
  $("evq").oninput = (ev) => {
    evQuery = ev.target.value;
    renderEvents();
  };
  $("hideagent").onchange = (ev) => {
    hideAgent = ev.target.checked;
    renderEvents();
  };
}

const fromHash = /^#e(\d+)$/.exec(location.hash);
if (fromHash) selected = Number(fromHash[1]);
wire();
connect();
render();
setInterval(() => {
  if (dirty && !paused) {
    dirty = false;
    render();
  }
}, 300);
setInterval(renderTickBar, 100);
