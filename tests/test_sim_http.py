"""The simulator over real HTTP: every vendored-SDK method, the spec, auth, throttles, SSE, admin routes.

A real uvicorn server runs in this process on a free local port (`tests/simkit.py`). The SDK is the
organisers' file, unchanged. Responses are validated against docs/api/openapi.json exactly as
tests/test_api_responses.py validates the real game's, and compared with the captured fixtures.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import replace
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator

from bazaar_agent.sdk import Bazaar, BazaarError
from bazaar_sim.world import SimConfig
from tests import test_api_responses as spec
from tests.simkit import QUIET, running_sim

KEY = "sim-team1"
FAST = replace(QUIET, tick_seconds=0.4)


def wait_ticks(client: Any, n: int = 1) -> int:
    start = client.clock()["tick"]
    deadline = time.monotonic() + 30
    while client.clock()["tick"] < start + n and time.monotonic() < deadline:
        time.sleep(0.05)
    return int(client.clock()["tick"])


def raw(url: str, path: str, *, method: str = "GET", headers: dict | None = None, body: bytes | None = None):
    req = urllib.request.Request(url + path, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.headers.get_content_type(), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get_content_type(), e.read()


@pytest.fixture(scope="module")
def server():
    config = replace(FAST, rivals=2, duel_first_tick=2, duel_ticks=30, bench_first_tick=2, bench_ticks=40)
    with running_sim(config) as (url, sim):
        yield url, sim


# ---------------------------------------------------------------- every SDK method, unchanged


def test_every_public_sdk_method_works_against_the_simulator(server):
    url, sim = server
    b = Bazaar(url, KEY, wait_on_tick=False, retries=1)
    assert b.health()["ok"] is True
    clock = b.clock()
    assert clock["doors"] == "open" and clock["limits"]["accepts_per_team_per_tick"] == 1
    assert {s["id"] for s in b.catalog()["sets"]} >= {"LAV", "MAL", "LAT", "SAL"}
    assert "teams" in b.leaderboard()
    assert isinstance(b.feed(limit=20)["events"], list)
    assert "upcoming" in b.schedule()
    assert {d["id"] for d in b.dealers()["personas"]} == {"abuela", "chato", "pilar"}
    assert b.personas()["personas"] == b.dealers()["personas"]
    assert b.dealer("abuela")["menu"]["sells"][0]["pack"] == "sobre_barrio"
    assert b.levels()["levels"][0]["id"] == "chato"
    assert b.call("GET", "/api/clock")["tick"] >= clock["tick"]
    assert b.venues()["venues"][0]["venue"] == "rastro"
    assert "offers" in b.board("rastro")
    me = b.me()
    assert me["id"] == "t01" and me["cash"] == 400 and len(me["assets"]) == 15
    card = me["assets"][0]
    assert b.card(card["id"])["owner"] == "t01"
    assert b.value(card["ref"])["card"] == card["ref"]
    assert b.my_threads()["threads"] == [] and "offers" in b.my_offers()

    th = b.open_thread("abuela", topic={"buy": {"pack": "sobre_barrio"}})
    assert th["status"] == "open" and th["with"] == "abuela"
    assert b.say(th["id"], "¡Hola, señora!", price=15)["ok"] is True
    wait_ticks(b)
    ask = [o for o in b.thread(th["id"])["standing_offers"] if o["maker"] == "abuela"][-1]
    assert b.accept(ask["id"])["ok"] is True
    wait_ticks(b)
    assert b.thread(th["id"])["status"] == "deal"
    pack = next(a for a in b.me()["assets"] if a["kind"] == "pack")
    opened = b.open_pack(pack["id"])
    assert len(opened["cards"]) == 3 and "luck" in opened
    message = next(m for m in b.thread(th["id"])["messages"] if m["sender"] == "abuela")
    assert b.flag(message["message"], "test")["ok"] is True
    second = b.open_thread("t02")
    assert b.close_thread(second["id"])["status"] == "closed"

    listing = b.list_offer({"assets": [card["id"]]}, {"cash": 90}, venue="rastro", expires_in_ticks=5)
    assert listing["status"] == "open" and listing["maker"] == "t01"
    assert b.cancel(listing["id"])["ok"] is True

    with sim.world.lock:
        sim.world.team("t01").unlocked.append("chato")
    venue = b.open_venue("Mercado Uno", fee_bps=150, rules={"mechanism": "board"})
    assert venue["broker_key"].startswith("simbk-")
    assert b.set_fee(venue["venue"], 100)["ok"] is True
    brk = b.broker(venue["broker_key"])
    book = brk.book()
    assert book["venue"] == venue["venue"] and isinstance(book["bench_offers"], list)
    assert brk.clock()["tick"] >= 1 and brk.announce("Abierto")["ok"] is True
    with pytest.raises(BazaarError) as e:
        brk.match(1, 2, 10)
    assert e.value.code == "invalid"
    assert b.close_venue(venue["venue"])["status"] == "closing"

    live = b.duels()["duels"]
    assert live, "the session starts at tick 2"
    duel = live[0]
    assert b.duel_say(duel["duel"], "Propongo esto", price=duel["your_limit"])["ok"] is True
    other = next(d for d in live if d["duel"] != duel["duel"] and d["rival_offer"])
    assert b.duel_accept(other["duel"])["ok"] is True
    assert isinstance(b.duels(done=True)["duels"], list)
    before = b.clock()["tick"]
    assert b.wait_tick()["tick"] > before


def test_the_sdk_waits_for_the_tick_on_its_own_when_told_to(server):
    url, _ = server
    b = Bazaar(url, KEY.replace("1", "3"), wait_on_tick=True, retries=2)
    th = b.open_thread("abuela", topic={"buy": {"card": "SAL-02"}})
    b.say(th["id"], "uno", price=5)
    b.say(th["id"], "dos", price=6)  # a wait_for_tick 429 the SDK sleeps through
    assert [m["sender"] for m in b.thread(th["id"])["messages"]].count("t03") == 2


def test_wait_for_tick_carries_next_tick(server):
    url, _ = server
    b = Bazaar(url, "sim-team4", wait_on_tick=False, retries=0)
    th = b.open_thread("abuela", topic={"buy": {"card": "SAL-04"}})
    b.say(th["id"], "uno", price=5)
    with pytest.raises(BazaarError) as e:
        b.say(th["id"], "dos", price=6)
    assert e.value.code == "wait_for_tick" and e.value.status == 429
    assert e.value.extra["next_tick"] >= b.clock()["tick"]


# ---------------------------------------------------------------- the spec and the captured fixtures


def _known_spec_gap(error: Any) -> bool:
    """The spec types `MenuBuy.sets` as a string ("released"), but the real /api/dealers entry for Doña Pilar
    (verified keyless 2026-10-03) lists sets: `{"rarity": "rare", "sets": ["SAL", "RET"]}`. Tolerate exactly that."""
    where = list(error.absolute_path)
    return (
        len(where) >= 2
        and where[-1] == "sets"
        and "buys" in where
        and isinstance(error.instance, list)
        and all(isinstance(x, str) for x in error.instance)
    )


def validate_against_spec(method: str, path: str, status: int, ctype: str, body: bytes) -> Any:
    response, schema = spec.schema_of(method, path, status, ctype)
    assert response is not None, f"{method.upper()} {path} returned {status}, undocumented"
    if ctype != "application/json":
        return None
    payload = json.loads(body)
    validator = Draft202012Validator({**schema, "components": spec.SPEC["components"]})
    errors = [
        f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message[:120]}"
        for e in validator.iter_errors(payload)
        if not _known_spec_gap(e)
    ]
    assert errors == [], f"{method.upper()} {path} does not match its schema"
    declared = spec.resolve(schema).get("properties")
    if declared and isinstance(payload, dict):
        assert sorted(set(payload) - set(declared)) == [], f"{method.upper()} {path} returns undocumented fields"
    return payload


def same_shape(sim: Any, real: Any, where: str) -> None:
    """Every field the real response carries, the simulator's carries too (first list items compared).

    Feed events and schedule items differ by type, so they are compared type against type."""
    for field, tag in (("events", "type"), ("upcoming", "action")):
        if isinstance(real, dict) and isinstance(sim, dict) and isinstance(real.get(field), list):
            by_tag = {e[tag]: e for e in sim[field]}
            first_real: dict[str, Any] = {}
            for item in real[field]:
                first_real.setdefault(item[tag], item)  # the first of each kind: later ones add optional fields
            for kind, item in first_real.items():
                if kind in by_tag:
                    same_shape(by_tag[kind], item, f"{where}.{field}[{kind}]")
            return
    if isinstance(real, dict) and isinstance(sim, dict):
        missing = sorted(set(real) - set(sim))
        assert missing == [], f"{where}: the simulator lacks {missing}"
        for k, v in real.items():
            same_shape(sim[k], v, f"{where}.{k}")
    elif isinstance(real, list) and isinstance(sim, list) and real and sim:
        same_shape(sim[0], real[0], f"{where}[0]")


def test_every_fixture_get_replays_against_the_simulator_and_matches_the_spec(server):
    url, sim = server
    b = Bazaar(url, KEY, wait_on_tick=False)
    me = b.me()
    tid = b.open_thread("t05")["id"]
    ids = {"pid": "abuela", "vid": "rastro", "asset_id": me["assets"][0]["id"], "card": "SAL-03", "tid": tid}
    reads = [(p, t, q, False) for p, (t, q) in spec.PUBLIC_READS.items()]
    reads += [(p, t, q, True) for p, (t, q) in spec.TEAM_READS.items()]
    checked = 0
    for path, template, query, keyed in reads:
        concrete = template.format(**ids)
        q = {k: v.format(**ids) if isinstance(v, str) else v for k, v in query.items()}
        qs = "?" + "&".join(f"{k}={v}" for k, v in q.items()) if q else ""
        status, ctype, body = raw(url, concrete + qs, headers={"X-Team-Key": KEY} if keyed else {})
        assert status == 200, (path, body[:200])
        payload = validate_against_spec("get", path, status, ctype, body)
        fixture = spec.fixture_path("get", concrete if path != "/api/cards/{asset_id}" else "/api/cards/1", q, keyed)
        if fixture.exists() and payload is not None:
            saved = json.loads(fixture.read_text())
            assert saved["status"] == status
            same_shape(payload, saved["body"], path)
        checked += 1
    assert checked == len(reads)
    for scheme, (method, path) in spec.REFUSAL_PROBES.items():
        status, ctype, body = raw(url, path)
        payload = validate_against_spec(method, path, status, ctype, body)
        assert status == 401 and payload["error"] == spec.REFUSED_CODES[scheme]


def test_the_writes_answer_in_their_documented_shapes(server):
    url, _ = server
    b = Bazaar(url, "sim-team6", wait_on_tick=False)
    me = b.me()
    offer = b.list_offer({"assets": [me["assets"][0]["id"]]}, {"cash": 80})
    th = b.open_thread("abuela", topic={"buy": {"card": "LAT-01"}})
    for path, body in (("/api/offers", offer), ("/api/threads", th)):
        response, schema = spec.schema_of("post", path, 200, "application/json")
        errors = list(Draft202012Validator({**schema, "components": spec.SPEC["components"]}).iter_errors(body))
        assert errors == [], path


# ---------------------------------------------------------------- keys, throttles, bodies


def test_only_simulator_keys_open_the_door_and_a_presented_key_is_never_echoed_or_logged(server, caplog):
    url, _ = server
    caplog.set_level(logging.DEBUG)
    real_looking = "tk-abcd-efgh"
    for key in (real_looking, "sim-team99", "sim-", ""):
        status, _, body = raw(url, "/api/me", headers={"X-Team-Key": key} if key else {})
        assert status == 401 and json.loads(body)["error"] == "bad_key"
        assert real_looking.encode() not in body
    assert real_looking not in caplog.text
    status, _, body = raw(url, "/api/broker/book", headers={"X-Broker-Key": "bk_realkey123"})
    assert status == 401 and json.loads(body)["error"] == "bad_key"
    assert raw(url, "/api/me", headers={"X-Team-Key": "sim-team2"})[0] == 200


def test_wrong_keys_trip_too_many_failures_per_address():
    with running_sim(FAST) as (url, _):
        codes = [json.loads(raw(url, "/api/me", headers={"X-Team-Key": "nope"})[2])["error"] for _ in range(24)]
        assert codes[:20] == ["bad_key"] * 20 and "too_many_failures" in codes[20:]
        assert raw(url, "/api/me", headers={"X-Team-Key": KEY})[0] == 200  # a valid key is never slowed


def test_five_requests_per_second_per_key_with_bursts_of_twenty():
    with running_sim(FAST, rate=5.0, burst=20.0) as (url, _):
        codes = [raw(url, "/api/me", headers={"X-Team-Key": KEY})[0] for _ in range(30)]
        assert codes[:20] == [200] * 20 and 429 in codes[20:]
        status, _, body = raw(url, "/api/me", headers={"X-Team-Key": KEY})
        assert status == 429 and json.loads(body)["error"] == "rate_limited"


@pytest.mark.parametrize(
    ("body", "status"),
    [
        (b'{"with": "abuela", "topic": {"buy": {"pack": "sobre_barrio"}}, "x": NaN}', 400),
        (json.dumps({"with": "abuela", "a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": 1}}}}}}}}).encode(), 400),
        (json.dumps({"with": "abuela", "big": 10**12}).encode(), 400),
        (b"[1, 2]", 400),
        (b"x" * (64 * 1024 + 1), 413),
    ],
    ids=["nan", "nine_levels", "number_too_big", "not_an_object", "over_64kb"],  # a 64 KB id breaks CI logs
)
def test_bodies_are_strict_json(server, body, status):
    url, _ = server
    got, _, raw_body = raw(url, "/api/threads", method="POST", headers={"X-Team-Key": "sim-team7"}, body=body)
    assert got == status, raw_body[:200]
    assert "error" in json.loads(raw_body)


def test_text_is_cleaned_not_refused(server):
    url, sim = server
    b = Bazaar(url, "sim-team8", wait_on_tick=False)
    th = b.open_thread("t02")
    b.say(th["id"], "hola‮​" + "x" * 2000)
    text = b.thread(th["id"])["messages"][-1]["text"]
    assert "‮" not in text and "​" not in text and len(text) == 1200


def test_unknown_routes_and_bad_queries(server):
    url, _ = server
    status, _, body = raw(url, "/api/nope")
    assert status == 404 and json.loads(body)["error"] == "not_found"
    status, _, body = raw(url, "/api/feed?limit=abc")
    assert status == 422 and "detail" in json.loads(body)
    status, ctype, _ = raw(url, "/")
    assert status == 200 and ctype == "text/html"


# ---------------------------------------------------------------- admin routes


def test_reset_and_tick_need_the_admin_token_and_state_is_a_public_summary():
    with running_sim(FAST, run_clock=False) as (url, sim):
        assert raw(url, "/sim/reset", method="POST")[0] == 401
        assert (
            json.loads(raw(url, "/sim/tick", method="POST", headers={"X-Admin-Token": "wrong"})[2])["error"]
            == "bad_token"
        )
        token = {"X-Admin-Token": "test-admin-token", "Content-Type": "application/json"}
        assert json.loads(raw(url, "/sim/tick", method="POST", headers=token)[2])["tick"] == 1
        summary = json.loads(raw(url, "/sim/state")[2])
        assert summary["simulator"] is True and summary["tick"] == 1 and "affinity" not in json.dumps(summary)
        assert raw(url, "/sim/state?full=1")[0] == 401
        assert json.loads(raw(url, "/sim/state?full=1", headers=token)[2])["clock"]["tick"] == 1
        reset = json.loads(raw(url, "/sim/reset", method="POST", headers=token, body=b'{"seed": 11}')[2])
        assert reset == {"ok": True, "tick": 0, "seed": 11, "teams": sim.world.config.player_teams}
        assert sim.store.load() is not None


def test_admin_routes_are_off_without_a_token():
    with running_sim(FAST, admin_token=None, run_clock=False) as (url, _):
        status, _, body = raw(url, "/sim/reset", method="POST", headers={"X-Admin-Token": ""})
        assert status == 401 and json.loads(body)["error"] == "bad_token"


# ---------------------------------------------------------------- the live stream


def test_the_stream_sends_hello_typed_events_and_keepalives():
    config = replace(FAST, tick_seconds=0.3)
    with running_sim(config, keepalive_s=0.5) as (url, sim):
        seen: list[str] = []
        comments = 0
        with (
            httpx.Client(base_url=url, timeout=10) as http,
            http.stream("GET", "/api/events/stream", params={"scope": "team"}, headers={"X-Team-Key": KEY}) as reply,
        ):
            assert reply.status_code == 200 and reply.headers["content-type"].startswith("text/event-stream")
            started = time.monotonic()
            for line in reply.iter_lines():
                if line.startswith("event: "):
                    seen.append(line.removeprefix("event: "))
                elif line.startswith("data: ") and seen[-1] == "hello":
                    assert json.loads(line.removeprefix("data: "))["scope"] == "team:t01"
                elif line.startswith(":"):
                    comments += 1
                if ("tick" in seen and comments) or time.monotonic() - started > 8:
                    break
        assert seen[0] == "hello" and "tick" in seen and comments >= 1


def test_six_streams_per_key():
    with running_sim(FAST, keepalive_s=5) as (url, sim), httpx.Client(base_url=url, timeout=5) as http:
        streams = [http.stream("GET", "/api/events/stream", headers={"X-Team-Key": KEY}) for _ in range(7)]
        replies = [s.__enter__() for s in streams[:6]]
        try:
            assert all(r.status_code == 200 for r in replies)
            with http.stream("GET", "/api/events/stream", headers={"X-Team-Key": KEY}) as extra:
                assert extra.status_code == 429
                assert json.loads(extra.read())["error"] == "too_many_streams"
        finally:
            for s in streams[:6]:
                s.__exit__(None, None, None)


def test_our_event_stream_client_reads_the_simulator():
    from bazaar_agent.stream import EventStream

    got: list[Any] = []
    with running_sim(replace(FAST, tick_seconds=0.3)) as (url, _):
        stream = EventStream(url, KEY, got.append)
        stream.start()
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and not any(isinstance(g, dict) and g.get("type") == "tick" for g in got):
                time.sleep(0.1)
        finally:
            stream.stop()
    assert any(isinstance(g, dict) and g.get("type") == "tick" for g in got)


def test_simconfig_from_env_clamps_values(monkeypatch):
    monkeypatch.setenv("SIM_TICK_SECONDS", "0.01")
    monkeypatch.setenv("SIM_PLAYER_TEAMS", "40")
    cfg = SimConfig.from_env()
    assert cfg.tick_seconds == 0.2 and cfg.player_teams == 12


def test_a_client_sent_forwarded_address_cannot_dodge_the_wrong_key_lockout():
    with running_sim(FAST) as (url, _):
        codes = [
            json.loads(raw(url, "/api/me", headers={"X-Team-Key": "nope", "X-Forwarded-For": f"10.0.0.{i}"})[2])[
                "error"
            ]
            for i in range(24)
        ]
        assert "too_many_failures" in codes[20:]


def test_the_trusted_proxy_header_names_the_client(monkeypatch):
    monkeypatch.setenv("SIM_CLIENT_IP_HEADER", "x-real-ip")
    with running_sim(FAST) as (url, _):
        for i in range(24):  # each request from its own (proxy-reported) address: no lockout
            body = raw(url, "/api/me", headers={"X-Team-Key": "nope", "X-Real-IP": f"10.0.1.{i}"})[2]
            assert json.loads(body)["error"] == "bad_key"


def test_a_reset_keeps_the_world_object_and_an_older_save_never_lands_after_it():
    from bazaar_sim.app import Sim
    from bazaar_sim.auth import Gate
    from bazaar_sim.store import MemoryStore
    from bazaar_sim.world import World

    sim = Sim(world=World.create(QUIET), store=MemoryStore(), gate=Gate.from_rates(0, 20, 0), admin_token="t")
    world = sim.world
    world.advance()
    stale = sim.snapshot()  # taken before the reset, written after it
    assert sim.reset(None) is world and world.tick == 0
    with sim.save_lock:
        assert stale[2] <= sim.saved
    sim.persist()
    assert '"tick":0' in (sim.store.load() or "")
