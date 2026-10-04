"""The human-only approval tools (HA2): absent from every agent's tool set, served by the remote MCP server only
with the approver token, validated, and refused when an approval could only loosen a hard cap. No network, no DB."""

import json
import threading

import pytest
from starlette.testclient import TestClient

from bazaar_agent import approvals
from bazaar_agent.approvals import Approval
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.runtime import agents as ag
from bazaar_agent.runtime import desk as dk
from bazaar_agent.runtime import human_tools as ht
from bazaar_agent.runtime import mcp_server as ms
from bazaar_agent.runtime import tools as tl
from bazaar_agent.runtime.hooks import Guard
from tests.runtime_fakes import TOKEN, Public, Team, backend
from tests.test_runtime_hooks import denied, models_of, pre
from tests.test_runtime_mcp_server import HEADERS, MCP_TOKEN, Clock

APPROVER = "appr-0123456789abcdefghijklmnopqrstuv-XYZ"
HUMAN = {"approvals", "approve", "revoke", "operator_review", "operator_approve", "operator_execute"}
RULES = Guardrails(protect_page_sets="LAV,LAT,RET", max_price_rare=95, max_spend_per_game_hour=150)
TICK = 100  # tests.agent_fakes.clock()


class Store:
    """`ApprovalStore` in memory: approvals by (card, side), requests and denials as rows, records as a list."""

    def __init__(self, pending=(), denials=()):
        self.approvals: dict[tuple[str, str], Approval] = {}
        self.asked = list(pending)
        self.denied = list(denials)
        self.records: list[tuple[str, int, dict]] = []

    def active(self, tick):
        return [a for a in self.approvals.values() if a.until_tick > tick]

    def pending(self, since_tick):
        return [r for r in self.asked if r["tick"] >= since_tick]

    def denials(self, since_tick):
        return [d for d in self.denied if d[2] >= since_tick]

    def approve(self, card, side, price, tick, until_tick, by, reason):
        a = Approval(
            card, side, price if side == "buy" else None, price if side == "sell" else None, until_tick, by, reason
        )
        self.approvals[(card, side)] = a
        audit = {"card": card, "side": side, "price": price, "until_tick": until_tick, "by": by, "reason": reason}
        self.record("approval_granted", tick, audit)
        return a

    def revoke(self, card, side, tick, by, reason):
        was = self.approvals.pop((card, side), None) is not None
        kind = "approval_revoked" if was else "approval_denied"
        self.denied.append((card, side, tick))
        self.record(kind, tick, {"card": card, "side": side, "by": by, "reason": reason or kind[len("approval_") :]})
        return was

    def record(self, kind, tick, inputs):
        self.records.append((kind, tick, dict(inputs)))


class Valued(Team):
    """The team client with GET /api/me/value: our official value of one more copy, or a failed read."""

    def __init__(self, values=None, **kw):
        super().__init__(**kw)
        self.values = values or {}

    def value(self, card):
        self.reads.append(f"value {card}")
        if card not in self.values:
            raise RuntimeError("value route down")
        return {"card": card, "your_value": self.values[card]}


def human_backend(tmp_path, values=None, rules=RULES, team=None):
    return backend(tmp_path, team=team or Valued(values or {"LAV-09": 120.0, "LAV-10": 60.0}), rules=rules)


def client(b, store, approver=APPROVER, calls_per_minute=200, now=None):
    app = ms.build_app(
        b, MCP_TOKEN, calls_per_minute, host="0.0.0.0", now=now or Clock(), approver=approver, store=store
    )
    return TestClient(app)


def rpc(c, method, params=None, approver=None, rid=1):
    headers = {**HEADERS, "mcp-protocol-version": "2025-06-18", "authorization": f"Bearer {MCP_TOKEN}"}
    if approver is not None:
        headers["x-approver-token"] = approver
    body = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
    return c.post(ms.MCP_PATH, json=body, headers=headers)


def tool(c, name, arguments, approver=APPROVER, rid=1):
    reply = rpc(c, "tools/call", {"name": name, "arguments": arguments}, approver=approver, rid=rid)
    assert reply.status_code == 200, reply.text
    result = reply.json()["result"]
    text = result["content"][0]["text"]
    return (text if result["isError"] else json.loads(text)), result["isError"]


def listed(c, approver=None):
    return {t["name"] for t in rpc(c, "tools/list", approver=approver).json()["result"]["tools"]}


def run(spec_name, b, store, args):
    spec = {s.name: s for s in ht.human_specs(store)}[spec_name]
    text, failed = tl.call(spec, b, args)
    return (text if failed else json.loads(text)), failed


# ---------------------------------------------------------------- no agent can ever approve


def test_the_human_tools_are_in_no_agent_tool_set(tmp_path):
    names = {s.name for s in ht.human_specs(Store())}
    assert names == HUMAN
    assert not names & set(tl.BY_NAME) and not names & {s.name for s in tl.TOOLS}
    mcp_names = {f"mcp__{tl.SERVER}__{n}" for n in names}
    for agent, allowed in ag.allow_lists().items():
        assert not allowed & (names | mcp_names), agent
    b = human_backend(tmp_path)
    options = dk.desk_options(
        Guard(b, ag.allow_lists()), tl.sdk_server(b), TOKEN, dk.DeskConfig(8, 30.0), tmp_path, models_of("sonnet-5-5")
    )
    assert not set(options.allowed_tools) & (names | mcp_names)
    for definition in (options.agents or {}).values():
        assert not set(definition.tools or ()) & (names | mcp_names)


def test_the_desk_hook_denies_an_approval_tool_even_if_a_model_names_it(tmp_path):
    g = Guard(human_backend(tmp_path), ag.allow_lists(), log=lambda line: None)
    for agent in (None, "buyer", "seller", "strategist", "duelist"):
        refused, _ = denied(pre(g, f"mcp__{tl.SERVER}__approve", {"card": "LAV-09", "side": "buy", "price": 90}, agent))
        assert refused, agent


# ---------------------------------------------------------------- the approver token is the only way in


def test_the_bearer_token_alone_neither_lists_nor_runs_the_human_tools(tmp_path):
    store = Store()
    with client(human_backend(tmp_path), store) as c:
        assert not listed(c) & HUMAN
        text, failed = tool(c, "approve", {"card": "LAV-09", "side": "buy", "price": 90}, approver=None)
        assert failed and text == "unknown tool 'approve'"
        assert tool(c, "approvals", {}, approver=None, rid=2) == ("unknown tool 'approvals'", True)
        assert listed(c, approver=APPROVER) == HUMAN  # an approver connection never reads counterparty text
        assert tool(c, "thread", {"thread_id": 7}, rid=3) == ("unknown tool 'thread'", True)
        health = c.get(ms.HEALTH_PATH).json()
        assert health["tools"] == len(tl.TOOLS) and "approv" not in json.dumps(health)
    assert store.approvals == {} and store.records == []


def test_a_wrong_approver_token_is_forbidden_and_logged_but_never_locks_the_human_out(tmp_path):
    lines: list[str] = []
    b = human_backend(tmp_path)
    b.log = lines.append
    with client(b, Store()) as c:
        for wrong in ("", "nope", APPROVER[:-1], APPROVER + "x", MCP_TOKEN, "x", "y"):
            reply = rpc(c, "tools/list", approver=wrong)
            assert reply.status_code == 403 and reply.json() == {"error": "forbidden"}
        assert listed(c, approver=APPROVER) == HUMAN  # seven misses from this bearer: the right token still works
        assert rpc(c, "tools/list").status_code == 200
    assert len(lines) == 7 and all("X-Approver-Token refused (bearer " in line for line in lines)
    assert not any(APPROVER in line or MCP_TOKEN in line or "nope" in line for line in lines)


def test_without_an_approver_token_the_human_tools_do_not_exist(tmp_path):
    with client(human_backend(tmp_path), Store(), approver=None) as c:
        assert rpc(c, "tools/list", approver=APPROVER).status_code == 403
        assert rpc(c, "tools/list", approver="").status_code == 403
        assert not listed(c) & HUMAN


def test_the_approver_token_must_be_strong_and_not_the_bearer_token(tmp_path):
    assert ms.approver_token(None, MCP_TOKEN) is None and ms.approver_token("  ", MCP_TOKEN) is None
    for weak in ("short", "a" * 64):
        with pytest.raises(ms.TokenError, match=ht.APPROVER_VARIABLE):
            ms.approver_token(weak, MCP_TOKEN)
    with pytest.raises(ms.TokenError, match="must differ"):
        ms.approver_token(f" {MCP_TOKEN} ", MCP_TOKEN)
    with pytest.raises(ms.TokenError):
        client(human_backend(tmp_path), Store(), approver=MCP_TOKEN)


def test_the_serve_command_turns_the_human_tools_off_unless_the_token_is_sound(monkeypatch):
    from bazaar_agent.runtime.cli import _approver

    monkeypatch.delenv(ht.APPROVER_VARIABLE, raising=False)
    assert _approver(MCP_TOKEN) == (None, f"approver tools OFF ({ht.APPROVER_VARIABLE} unset)")
    monkeypatch.setenv(ht.APPROVER_VARIABLE, MCP_TOKEN)
    token, note = _approver(MCP_TOKEN)
    assert token is None and "must differ" in note and MCP_TOKEN not in note
    monkeypatch.setenv(ht.APPROVER_VARIABLE, APPROVER)
    assert _approver(MCP_TOKEN) == (APPROVER, "approver tools ON (X-Approver-Token required)")


def test_approval_writes_are_capped_per_minute(tmp_path):
    now = Clock()
    store = Store()

    def call(name, arguments, rid):
        now.t += 1.0  # past the per-token tool-call bucket (burst 5): only the approval cap may refuse
        return tool(c, name, arguments, rid=rid)

    with client(human_backend(tmp_path), store, now=now) as c:
        answers = [call("revoke", {"card": "LAV-09", "side": "buy"}, i) for i in range(ht.WRITES_PER_MINUTE)]
        assert all(not failed and a["status"] == "denied" for a, failed in answers)
        text, failed = call("approve", {"card": "LAV-09", "side": "buy", "price": 90}, 99)
        assert failed and text.startswith(f"rate limited: {ht.WRITES_PER_MINUTE} approvals per minute")
        assert not call("approvals", {}, 100)[1]  # reads are not approval writes
        now.t += 60.0
        ok, failed = call("approve", {"card": "LAV-09", "side": "buy", "price": 90}, 101)
    assert not failed and ok["status"] == "approved"


def test_no_answer_and_no_stored_reason_carries_a_token(tmp_path):
    store = Store()
    with client(human_backend(tmp_path), store) as c:
        ok, _ = tool(c, "approve", {"card": "LAV-09", "side": "buy", "price": 90, "reason": f"x {APPROVER}"})
        gone, _ = tool(c, "revoke", {"card": "LAV-09", "side": "buy", "reason": f"y {MCP_TOKEN}"}, rid=2)
        raw = rpc(c, "tools/call", {"name": "approvals", "arguments": {}}, approver=APPROVER, rid=3).text
    assert ok["status"] == "approved" and gone["status"] == "revoked"
    assert APPROVER not in raw and MCP_TOKEN not in raw
    stored = json.dumps([r[2] for r in store.records])
    assert APPROVER not in stored and MCP_TOKEN not in stored and "[redacted]" in stored


def test_a_revoke_never_waits_for_an_approve_still_checking_and_that_approve_is_refused(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class Slow(Valued):
        def value(self, card):
            entered.set()
            release.wait(5)
            return super().value(card)

    store = Store()
    b = human_backend(tmp_path, team=Slow({"LAV-09": 120.0}))
    answers: dict[str, dict] = {}
    approve = threading.Thread(
        target=lambda: answers.update(
            approve=run("approve", b, store, {"card": "LAV-09", "side": "buy", "price": 90})[0]
        )
    )
    approve.start()
    assert entered.wait(5)  # the approve is inside its /api/me/value read
    gone, _ = run("revoke", b, store, {"card": "LAV-09", "side": "buy"})  # answers at once, no game read waited for
    release.set()
    approve.join(5)
    assert gone["status"] == "denied"
    assert answers["approve"]["status"] == "refused"
    assert answers["approve"]["reasons"] == ["LAV-09 buy was revoked while this approval was checked: approve again"]
    assert store.approvals == {}
    assert [k for k, _, _ in store.records] == ["approval_denied", "approval_refused"]


def test_a_revoke_works_while_the_clock_is_unreadable(tmp_path):
    class NoClock(Public):
        def clock(self):
            raise RuntimeError("clock down")

    store = Store()
    b = human_backend(tmp_path)
    run("approve", b, store, {"card": "LAV-09", "side": "buy", "price": 90})
    b._public = NoClock()
    gone, failed = run("revoke", b, store, {"card": "LAV-09", "side": "buy"})
    assert not failed and gone["status"] == "revoked" and gone["tick"] is None and store.approvals == {}


def test_a_revoke_never_waits_long_for_a_hung_clock(tmp_path, monkeypatch):
    hung = threading.Event()

    class HungClock(Public):
        def clock(self):
            hung.wait(5)
            return super().clock()

    monkeypatch.setattr(ht, "REVOKE_CLOCK_BUDGET_S", 0.2)
    store = Store()
    b = human_backend(tmp_path)
    run("approve", b, store, {"card": "LAV-09", "side": "buy", "price": 90})
    b._public = HungClock()
    gone, failed = run("revoke", b, store, {"card": "LAV-09", "side": "buy"})
    hung.set()
    assert not failed and gone["status"] == "revoked" and gone["tick"] is None and store.approvals == {}


def test_a_bearer_holder_draining_the_shared_budget_never_keeps_the_human_from_a_revoke(tmp_path):
    now = Clock()
    store = Store()
    with client(human_backend(tmp_path), store, calls_per_minute=30, now=now) as c:
        ok, _ = tool(c, "approve", {"card": "LAV-09", "side": "buy", "price": 90})
        assert ok["status"] == "approved"
        for i in range(5):  # the bearer's tool-call burst
            rpc(c, "tools/call", {"name": "clock", "arguments": {}}, rid=10 + i)
        assert tool(c, "clock", {}, approver=None, rid=20)[0].startswith("rate limited")
        codes = [rpc(c, "tools/list", rid=30 + i).status_code for i in range(ms.HTTP_BURST + 2)]
        assert codes[-1] == 429  # the bearer's HTTP bucket is empty too
        gone, failed = tool(c, "revoke", {"card": "LAV-09", "side": "buy"}, rid=40)
    assert not failed and gone["status"] == "revoked" and store.approvals == {}


# ---------------------------------------------------------------- approve: what an approval may cover


@pytest.mark.parametrize(
    "args",
    [
        {"card": "LAV-09", "side": "buy", "price": 0},
        {"card": "LAV-09", "side": "buy", "price": 1001},
        {"card": "LAV-09", "side": "buy", "price": 90.5},
        {"card": "LAV-09", "side": "buy", "price": 90, "ttl_ticks": 0},
        {"card": "LAV-09", "side": "buy", "price": 90, "ttl_ticks": 481},
        {"card": "lav-09", "side": "buy", "price": 90},
        {"card": "LAV-09", "side": "hold", "price": 90},
        {"card": "LAV-09", "side": "buy", "price": 90, "reason": "x" * 301},
        {"card": "LAV-09", "side": "buy", "price": 90, "via": "Bazaar Live"},
        {"card": "LAV-09", "side": "buy", "price": 90, "by": "human:omar"},
        {"card": "LAV-09", "side": "buy"},
    ],
    ids=[
        "price-0",
        "price-1001",
        "price-float",
        "ttl-0",
        "ttl-481",
        "lowercase",
        "side",
        "reason",
        "via",
        "by",
        "no-price",
    ],
)
def test_every_input_is_validated_before_anything_is_read_or_written(tmp_path, args):
    team = Valued({"LAV-09": 120.0})
    store = Store()
    text, failed = run("approve", human_backend(tmp_path, team=team), store, args)
    assert failed and text.startswith("invalid arguments for approve")
    assert store.approvals == {} and store.records == [] and team.reads == []


def test_a_buy_inside_every_cap_is_approved_and_logged_with_who_approved(tmp_path):
    store = Store()
    b = human_backend(tmp_path)
    ok, failed = run(
        "approve", b, store, {"card": "LAV-09", "side": "buy", "price": 90, "ttl_ticks": 30, "via": "bazaar-live"}
    )
    assert not failed and ok["status"] == "approved"
    assert (ok["max_price"], ok["min_price"], ok["until_tick"], ok["by"]) == (90, None, TICK + 30, "human:bazaar-live")
    assert store.approvals[("LAV-09", "buy")].covers(90, TICK + 29) and ok["notes"] == []
    ((kind, tick, inputs),) = store.records
    assert (kind, tick, inputs["by"], inputs["until_tick"]) == (
        "approval_granted",
        TICK,
        "human:bazaar-live",
        TICK + 30,
    )
    assert run("approve", b, Store(), {"card": "LAV-09", "side": "buy", "price": 90})[0]["by"] == "human:mcp"


@pytest.mark.parametrize(
    ("card", "price", "rules", "reason"),
    [
        ("LAV-09", 96, RULES, "max_price_rare 95: an approval never lifts it"),
        ("LAV-10", 70, RULES, "official value 60 (official_value_margin 0): every buy is capped at it"),
        ("LAV-08", 20, RULES, "the official value of LAV-08 could not be read (fail closed)"),
        ("LAV-09", 90, RULES.model_copy(update={"max_spend_per_game_hour": 80}), "max_spend_per_game_hour 80"),
        ("LAV-11", 20, RULES, "no max_price for rarity epic: buying it is not allowed"),
        ("ZZZ-01", 20, RULES, "ZZZ-01 is not in the catalog"),
    ],
    ids=["rarity-cap", "official-value", "official-unread", "hourly-spend", "epic", "not-in-catalog"],
)
def test_a_buy_an_approval_could_only_loosen_a_hard_cap_for_is_refused(tmp_path, card, price, rules, reason):
    store = Store()
    answer, failed = run(
        "approve", human_backend(tmp_path, rules=rules), store, {"card": card, "side": "buy", "price": price}
    )
    assert not failed and answer["status"] == "refused"
    assert any(r.startswith(reason) for r in answer["reasons"]), answer["reasons"]
    assert store.approvals == {}
    ((kind, _, inputs),) = store.records
    assert kind == "approval_refused" and reason in inputs["reason"]


@pytest.mark.parametrize(
    ("card", "price", "rules", "reason"),
    [
        ("LAT-09", 300, RULES, "LAT-09 is our only copy of a page card (protect_page_sets): never sold"),
        ("LAT-03", 1, RULES, "sell_min_value_ratio 1 × our value 1.2: never sold below it"),
        ("LAV-09", 300, RULES, "we hold no copy of LAV-09"),
    ],
    ids=["last-copy", "below-value", "not-held"],
)
def test_a_sell_of_a_last_copy_or_below_our_value_is_refused(tmp_path, card, price, rules, reason):
    store = Store()
    answer, failed = run(
        "approve", human_backend(tmp_path, rules=rules), store, {"card": card, "side": "sell", "price": price}
    )
    assert not failed and answer["status"] == "refused" and reason in answer["reasons"]
    assert store.approvals == {} and store.records[0][0] == "approval_refused"


def test_a_sell_of_a_duplicate_above_our_value_is_approved(tmp_path):
    store = Store()
    ok, failed = run("approve", human_backend(tmp_path), store, {"card": "LAT-03", "side": "sell", "price": 300})
    assert not failed and ok["status"] == "approved" and (ok["max_price"], ok["min_price"]) == (None, 300)
    assert store.approvals[("LAT-03", "sell")].covers(310, TICK)


def test_an_unreadable_album_writes_nothing(tmp_path):
    class Down(Valued):
        def me(self):
            raise RuntimeError("me down")

    store = Store()
    text, failed = run(
        "approve", human_backend(tmp_path, team=Down()), store, {"card": "LAT-03", "side": "sell", "price": 9}
    )
    assert failed and text == "RuntimeError: the tool failed" and store.approvals == {} and store.records == []


def test_revoke_removes_an_approval_and_otherwise_records_a_denial(tmp_path):
    store = Store()
    b = human_backend(tmp_path)
    run("approve", b, store, {"card": "LAV-09", "side": "buy", "price": 90})
    gone, _ = run("revoke", b, store, {"card": "LAV-09", "side": "buy", "via": "bazaar-live"})
    assert gone == {"status": "revoked", "card": "LAV-09", "side": "buy", "tick": TICK, "by": "human:bazaar-live"}
    no, _ = run("revoke", b, store, {"card": "LAV-09", "side": "buy", "reason": "too dear"})
    assert no["status"] == "denied" and store.approvals == {}
    assert [(k, i["reason"]) for k, _, i in store.records[1:]] == [
        ("approval_revoked", "revoked"),
        ("approval_denied", "too dear"),
    ]


# ---------------------------------------------------------------- approvals: what a human needs to decide


def asked(card, side, price, tick, **extra):
    return {"card": card, "side": side, "price": price, "tick": tick, "hour": 1, **extra}


def test_the_requests_say_why_what_it_costs_the_album_and_where_each_stands(tmp_path):
    store = Store(
        pending=[
            asked(
                "LAT-09",
                "sell",
                260,
                95,
                counterparty="t05",
                kind="sell",
                our_value=35.0,
                reason="breaks LAT",
                score_impact=-4.3,
            ),
            asked("LAV-09", "buy", 260, 90, counterparty="chato", kind="accept_buy", official_value=120.0),
            asked("LAT-03", "sell", 255, 80, counterparty="abuela", kind="dealer_sell"),
            asked("LAV-10", "buy", 250, 70, counterparty="t02", kind="bid"),
        ],
        denials=[("LAT-03", "sell", 85)],
    )
    store.approve("LAV-10", "buy", 255, TICK, TICK + 10, "human:mcp", "")
    out = approvals_answer(human_backend(tmp_path, rules=RULES.model_copy(update={"human_approval_above": 250})), store)
    assert (out["tick"], out["threshold"], out["notes"]) == (TICK, 250, [])
    rows = {(r["card"], r["side"]): r for r in out["pending"]}
    assert [r["state"] for r in out["pending"]] == ["waiting", "waiting", "approved", "denied"]
    sell = rows[("LAT-09", "sell")]
    assert sell["album"] == {"set": "LAT", "held": 1, "page_card": True, "last_copy": True}
    assert sell["why"] == "score impact -4.3 (max_score_loss_per_move): breaks LAT" and sell["cap"] is None
    assert (sell["asked_by"], sell["counterparty"], sell["stale_after_tick"]) == (
        "sell",
        "t05",
        95 + approvals.PENDING_TICKS,
    )
    buy = rows[("LAV-09", "buy")]
    assert buy["why"] == "price 260 ≥ human_approval_above 250" and buy["cap"] == {
        "max_price": 95,
        "rule": "max_price_rare",
    }
    assert buy["album"]["held"] == 0 and buy["official_value"] == 120.0
    assert rows[("LAT-03", "sell")]["album"]["last_copy"] is False  # two copies: a duplicate
    assert out["active"][0]["card"] == "LAV-10" and out["limits"]["writes_per_minute"] == ht.WRITES_PER_MINUTE


def test_an_unreadable_album_still_lists_the_requests(tmp_path):
    class Down(Valued):
        def me(self):
            raise RuntimeError("me down")

    out = approvals_answer(human_backend(tmp_path, team=Down()), Store(pending=[asked("LAV-09", "buy", 260, 90)]))
    assert out["pending"][0]["album"] is None and out["notes"] == [
        "/me unreadable (RuntimeError): album impact unknown"
    ]


def approvals_answer(b, store):
    answer, failed = run("approvals", b, store, {})
    assert not failed, answer
    return answer


def test_the_postgres_store_writes_each_change_and_its_audit_row_on_one_connection():
    opened: list[list[str]] = []

    class Conn:
        def __enter__(self):
            opened.append([])
            return self

        def __exit__(self, *exc):
            pass

        def execute(self, sql, params=None):
            opened[-1].append(" ".join(sql.split()[:3]))
            return self

        def fetchall(self):
            return []

        def fetchone(self):
            return None

        def commit(self):
            pass

    store = ht.PgApprovalStore(lambda: Conn())
    assert store.denials(10) == [] and store.pending(10) == []
    store.approve("LAV-09", "buy", 90, TICK, TICK + 30, "human:mcp", "ok")
    assert store.revoke("LAV-09", "buy", TICK, "human:mcp", "") is False
    assert len(opened) == 4 and all(statements[0] == "set statement_timeout =" for statements in opened)
    assert "insert into human_approvals" in opened[2] and "insert into decisions" in opened[2]
    assert "delete from human_approvals" in opened[3] and "insert into decisions" in opened[3]


def test_only_a_cheap_buy_is_told_it_needed_no_approval(tmp_path):
    rules = RULES.model_copy(update={"human_approval_above": 500})
    buy, _ = run(
        "approve", human_backend(tmp_path, rules=rules), Store(), {"card": "LAV-09", "side": "buy", "price": 90}
    )
    assert buy["notes"] == ["a buy at 90 is below human_approval_above 500: it needed no approval"]
    sell, _ = run(
        "approve", human_backend(tmp_path, rules=rules), Store(), {"card": "LAT-03", "side": "sell", "price": 9}
    )
    assert sell["status"] == "approved" and sell["notes"] == []  # max_score_loss_per_move may hold any sale


def test_each_human_tool_publishes_a_strict_schema():
    for spec in ht.human_specs(Store()):
        schema = spec.schema()
        assert schema.get("additionalProperties") is False, spec.name
    approve = {s.name: s for s in ht.human_specs(Store())}["approve"].schema()["properties"]
    assert (approve["price"]["minimum"], approve["price"]["maximum"]) == (1, 1000)
    assert (approve["ttl_ticks"]["minimum"], approve["ttl_ticks"]["maximum"]) == (1, 480)
