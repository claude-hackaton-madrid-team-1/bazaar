"""B18 (bites X20, X6, X2): a refused accept gives the team's accept back, a lost race stops the accept loop,
and the team client never re-sends a refused call or a write, and never waits 15 s per attempt.

The flipped bite tests live in `tests/bites/test_c1_request_budget.py`; these pin each refusal code, both ledgers
and the duel loop.
"""

import io
import json
import types
import urllib.error
import urllib.request
from collections import Counter

import pytest
from typer.testing import CliRunner

from bazaar_agent import sdk  # isort: skip  (puts vendor/bazaar-kit on sys.path)
import bazaar_sdk  # noqa: E402,I001
from bazaar_agent.guardrails import Ledger
from bazaar_agent.ledger_pg import PgLedger
from bazaar_agent.sdk import BazaarError, TeamBazaar
from tests.agent_fakes import TICK, FakePublic, FakeTeam, ask, clock
from tests.bites.kit import make_taker
from tests.test_db import database_url, open_in, schema  # noqa: F401  (pytest fixtures)
from tests.test_jev_journal import duel_cli  # noqa: F401  (pytest fixture)

# ---------------------------------------------------------------- the ledgers


def test_a_released_accept_frees_its_slot_in_the_file_ledger(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    assert ledger.reserve_accept(100, 1.5, 10, "LAV-02", 1)
    assert not ledger.reserve_accept(100, 1.5, 12, "LAV-08", 1)
    ledger.release_accept(100, "LAV-02")
    assert ledger.accepts_in_tick(100) == 0 and ledger.accept_items(100) == []
    assert ledger.reserve_accept(100, 1.5, 12, "LAV-08", 1)
    assert ledger.accept_items(100) == ["LAV-08"]


def test_releasing_what_was_not_reserved_changes_nothing(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.release_accept(100, "LAV-02")  # nothing reserved: no row that could cancel a later reservation
    assert ledger.reserve_accept(100, 1.5, 10, "LAV-02", 1)
    ledger.release_accept(101, "LAV-02")  # another tick
    ledger.release_accept(100, "LAV-08")  # another item
    assert ledger.accept_items(100) == ["LAV-02"]
    assert ledger.spent_since(0) == 0  # a release is no spend


@pytest.mark.integration
def test_a_released_accept_frees_its_slot_in_postgres(database_url, schema):  # noqa: F811
    from bazaar_agent import db

    conn = open_in(database_url, schema)
    db.init_schema(conn)
    ledger = PgLedger(conn, "taker")
    assert ledger.reserve_accept(100, 1.5, 10, "LAV-02", 1)
    ledger.release_accept(100, "LAV-02")
    assert ledger.accepts_in_tick(100) == 0
    assert ledger.reserve_accept(100, 1.5, 12, "LAV-08", 1)  # the unique (tick, slot) index lets slot 1 again
    assert ledger.accept_items(100) == ["LAV-08"]
    conn.close()


# ---------------------------------------------------------------- the taker


class RefusingAccept(FakeTeam):
    def __init__(self, code, status, **kw):
        super().__init__(**kw)
        self.code, self.status, self.attempts = code, status, []

    def accept(self, offer_id, assets=None):
        self.attempts.append(offer_id)
        if len(self.attempts) == 1:
            raise BazaarError(self.code, "refused", self.status)
        return super().accept(offer_id, assets)


BOARD = FakePublic(boards={"rastro": [ask(1, "LAV-02", 10), ask(2, "LAV-08", 20, asset=901)]})


@pytest.mark.parametrize("code,status", [("offer_closed", 409), ("asset_locked", 409), ("not_found", 404)])
def test_a_refused_accept_gives_the_slot_to_the_next_candidate(tmp_path, code, status):
    team = RefusingAccept(code, status)
    t, _, ledger = make_taker(tmp_path, team, BOARD, threads=0)
    t.on_tick(clock())
    assert len(team.attempts) == 2  # the next candidate had the team's accept
    refs = {1: "LAV-02", 2: "LAV-08"}
    assert ledger.accept_items(TICK) == [refs[team.attempts[1]]] and ledger.spent_since(0) > 0


def test_after_a_rate_limited_accept_the_slot_is_free_but_no_more_accepts_are_tried(tmp_path):
    team = RefusingAccept("rate_limited", 429)
    t, lines, ledger = make_taker(tmp_path, team, BOARD, threads=0)
    t.on_tick(clock())
    assert len(team.attempts) == 1 and ledger.accepts_in_tick(TICK) == 0  # a duel or the next tick may use it
    assert any("no more accepts this tick" in line for line in lines)


@pytest.mark.parametrize("code,status", [("insufficient_cash", 400), ("cooloff", 403), ("persona_quota", 429)])
def test_a_refusal_every_candidate_would_meet_frees_the_slot_and_ends_the_ticks_accepts(tmp_path, code, status):
    team = RefusingAccept(code, status)
    t, lines, ledger = make_taker(tmp_path, team, BOARD, threads=0)
    t.on_tick(clock())
    assert len(team.attempts) == 1 and ledger.accepts_in_tick(TICK) == 0
    assert any("no more accepts this tick" in line for line in lines)


def test_at_most_two_refused_accepts_per_tick(tmp_path, monkeypatch):
    """Each refused try costs a keyed clock read and a POST on the key every process shares (review of #141)."""
    team = RefusingAccept("offer_closed", 409)
    team.accept = lambda offer_id, assets=None: (team.attempts.append(offer_id), _raise("offer_closed", 409))[1]
    refs = ["LAV-02", "LAV-08", "LAV-09", "LAV-11", "LAV-01"]
    board = FakePublic(boards={"rastro": [ask(i, ref, 5, asset=900 + i) for i, ref in enumerate(refs, 1)]})
    t, _, ledger = make_taker(tmp_path, team, board, threads=0, max_spend_per_game_hour=1000)
    t.on_tick(clock())
    assert len(team.attempts) == 2 and ledger.accepts_in_tick(TICK) == 0
    monkeypatch.setattr("bazaar_agent.agents.taker.MAX_REFUSED_ACCEPTS", 99)  # the cap is what stops it
    team.attempts.clear()
    t2, _, _ = make_taker(tmp_path / "uncapped", team, board, threads=0, max_spend_per_game_hour=1000)
    t2.on_tick(clock())
    assert len(team.attempts) > 2


def _raise(code, status):
    raise BazaarError(code, "refused", status)


@pytest.mark.parametrize(
    "code,status",
    [("wait_for_tick", 429), ("network", 0), ("bad_response", 200), ("http_502", 502), ("internal", 500)],
)
def test_an_accept_that_used_the_quota_or_may_have_landed_keeps_its_slot(tmp_path, code, status):
    """A 5xx may come after the game applied the accept: the slot is kept and the spend booked (fail safe)."""
    team = RefusingAccept(code, status)
    t, _, ledger = make_taker(tmp_path, team, BOARD, threads=0)
    t.on_tick(clock())
    assert len(team.attempts) == 1 and ledger.accepts_in_tick(TICK) == 1
    if status >= 500:
        assert ledger.spent_since(0) > 0


def test_a_refused_accept_books_no_spend(tmp_path):
    team = RefusingAccept("offer_closed", 409)
    team_public = FakePublic(boards={"rastro": [ask(1, "LAV-02", 10)]})
    t, _, ledger = make_taker(tmp_path, team, team_public, threads=0)
    t.on_tick(clock())
    assert ledger.spent_since(0) == 0 and ledger.accepts_in_tick(TICK) == 0


# ---------------------------------------------------------------- the duel loop


def _duel_ledger(tmp_path):
    return Ledger(tmp_path / "ledger.jsonl")


@pytest.mark.parametrize(
    "code,status,kept",
    [("rate_limited", 429, 0), ("duel_closed", 409, 0), ("network", 0, 1), ("http_503", 503, 1)],
)
def test_a_refused_duel_accept_gives_the_slot_back(duel_cli, code, status, kept):  # noqa: F811
    cli, client, _, tmp_path = duel_cli

    def refused(did):
        client.sent.append(("accept", did))
        raise BazaarError(code, "refused", status)

    client.duel_accept = refused
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert client.sent == [("accept", 95)]
    assert _duel_ledger(tmp_path).accepts_in_tick(134) == kept


# ---------------------------------------------------------------- the team client


def _fake_time(monkeypatch):
    now = {"t": 0.0}
    monkeypatch.setattr(bazaar_sdk, "time", types.SimpleNamespace(sleep=lambda s: now.__setitem__("t", now["t"] + s)))
    return now


def _client():
    settings = types.SimpleNamespace(bazaar_url="http://127.0.0.1:9", require_team_key=lambda: "tk-t-t")
    return sdk.team_client(settings, track=False)  # the holdings hook is tested in tests/test_holdings*


def test_the_team_client_is_a_team_bazaar_with_a_short_timeout():
    client = _client()
    assert isinstance(client, TeamBazaar) and isinstance(client, bazaar_sdk.Bazaar)
    assert client.timeout == sdk.TEAM_TIMEOUT_S <= 4.0 and client.wait_on_tick is False
    assert client._headers["X-Team-Key"] == "tk-t-t"


def _answers(monkeypatch, *answers):
    """urlopen that plays `answers` in order: an exception to raise, or a dict to answer with."""
    sent: list[str] = []
    queue = list(answers)

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(req, timeout=None):
        sent.append(f"{req.get_method()} {req.full_url.split('9', 1)[-1]}")
        answer = queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return Resp(json.dumps(answer).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return sent


def _http_429():
    body = io.BytesIO(json.dumps({"error": "rate_limited", "message": "slow down"}).encode())
    return urllib.error.HTTPError("http://x", 429, "Too Many Requests", {}, body)


def test_a_read_after_a_network_error_is_tried_again_on_slow_ticks(monkeypatch):
    now = _fake_time(monkeypatch)
    client = _client()
    sent = _answers(monkeypatch, {"tick": 5, "tick_seconds": 30}, TimeoutError("t"), {"cash": 1})
    client.clock()
    assert client.me() == {"cash": 1}
    assert sent == ["GET /api/clock", "GET /api/me", "GET /api/me"] and now["t"] == 0.5


def test_no_read_is_tried_again_once_ticks_are_15_s_or_faster(monkeypatch):
    _fake_time(monkeypatch)
    client = _client()
    sent = _answers(monkeypatch, {"tick": 5, "tick_seconds": 15}, TimeoutError("t"))
    client.clock()
    with pytest.raises(BazaarError) as e:
        client.me()
    assert e.value.code == "network" and sent == ["GET /api/clock", "GET /api/me"]


def test_a_write_and_a_429_are_never_sent_twice(monkeypatch):
    _fake_time(monkeypatch)
    client = _client()
    sent = _answers(monkeypatch, _http_429(), TimeoutError("t"), _http_429())
    for call in (lambda: client.accept(1), lambda: client.accept(2), client.me):
        with pytest.raises(BazaarError):
            call()
    assert Counter(s.split()[0] for s in sent) == Counter({"POST": 2, "GET": 1})


def test_the_team_client_still_tells_the_holdings_about_every_write_once(monkeypatch):
    """TeamBazaar is a TrackedBazaar (#105): a write bumps the holdings epoch before and after, even refused,
    and is still never re-sent; a read tells nothing."""
    _fake_time(monkeypatch)
    calls: list[tuple[str, str, str]] = []
    client = TeamBazaar("http://127.0.0.1:9", "tk-t-t", on_write=lambda m, path, phase: calls.append((m, path, phase)))
    sent = _answers(monkeypatch, _http_429(), {"ok": True})
    with pytest.raises(BazaarError):
        client.accept(1)
    client.me()
    assert [s.split()[0] for s in sent] == ["POST", "GET"]
    assert [c[2] for c in calls] == ["before", "after"] and calls[0][0] == "POST"


def test_the_public_client_keeps_the_sdk_retries():
    public = sdk.public_client(types.SimpleNamespace(bazaar_url="http://127.0.0.1:9"))
    assert public.retries == 2 and not isinstance(public, TeamBazaar)


@pytest.mark.parametrize("pace", [True, float("nan"), float("inf"), -1.0, 1e308, "30"])
def test_a_clock_pace_that_is_not_a_sane_number_is_ignored(pace):
    assert sdk._pace(pace) is None
    assert sdk._pace(30) == 30.0 and sdk._pace(15.0) == 15.0
