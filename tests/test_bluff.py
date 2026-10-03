"""The bluff chooser (N16): per-counterparty, deterministic, learned from tactic lessons, killable."""

from __future__ import annotations

import random

import pytest

from bazaar_agent.agents.bluff import (
    ENV,
    MUTE_AFTER,
    NO_GAIN_TRIES,
    PENALTY,
    Choice,
    Counterparty,
    TacticBook,
    enabled,
)
from bazaar_agent.agents.tactics import BY_ID, eligible, numbers_in
from bazaar_agent.agents.words import WordsRequest
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.learn.store import LearningStore

US = "t01"
CHATO = Counterparty.dealer("chato")
ABUELA = Counterparty.dealer("abuela")
RIVAL = Counterparty.rival("Rival Plata")


def book(**kw):
    kw.setdefault("env", {})
    kw.setdefault("us", US)
    return TacticBook(**kw)


def plain(request: WordsRequest) -> str:
    return f"plain {request.price}"


def play(b: TacticBook, cp: Counterparty, conv: str, step: int, before: int, after: int, tick: int) -> Choice:
    """One tactic message, then the counterparty's answer (a new offer at `after`)."""
    c = b.choose(cp, "buy", conv, step, 30)
    b.sent(c, their_price=before, their_offer=tick * 10, tick=tick)
    b.observe(conv, their_price=after, their_offer=tick * 10 + 1, tick=tick + 1)
    return c


def test_default_on_a_dealer_gets_a_bluff_and_a_rival_too():
    b = book()
    c = b.choose(CHATO, "buy", "thread:1", 0, 30)
    assert c.tactic in eligible("dealer", "chato", "buy") and not BY_ID[c.tactic].kindness
    s = b.choose(RIVAL, "sell", "duel:9", 0, 90)
    assert s.tactic in eligible("rival", "rival_plata", "sell")


def test_the_choice_is_deterministic_for_a_seed_and_history():
    picks = [book(seed=7).choose(CHATO, "buy", "thread:1", 2, 30).tactic for _ in range(5)]
    assert len(set(picks)) == 1
    many = {book(seed=s).choose(CHATO, "buy", "thread:1", 0, 30).tactic for s in range(40)}
    assert len(many) > 1  # the seed (not a fixed order) breaks ties between untried tactics


def test_untried_tactics_rotate_then_the_best_learned_one_wins():
    b = book(seed=3)
    used = []
    for step in range(4):  # four bluff tactics for a buy: each is tried once
        c = play(b, CHATO, "thread:1", step, 33, 33, 100 + 2 * step)
        used.append(c.tactic)
    assert sorted(used) == sorted(eligible("dealer", "chato", "buy"))
    # one tactic moved Chato every time, the others never did
    winner = used[1]
    for step in range(4, 10):
        c = b.choose(CHATO, "buy", "thread:1", step, 30)
        moved = c.tactic == winner
        b.sent(c, their_price=33, their_offer=1000 + step, tick=200 + step)
        b.observe("thread:1", their_price=31 if moved else 33, their_offer=2000 + step, tick=201 + step)
    later = [b.choose(CHATO, "buy", f"thread:{n}", 0, 30).tactic for n in range(2, 6)]
    assert later.count(winner) >= 3, (winner, later)


def test_an_unanswered_untried_tactic_is_not_repeated_on_the_next_message():
    for seed in range(40):  # ties are seeded: a repeat would show up for some seed
        b = book(seed=seed)
        picks = []
        for step in range(4):  # the rival never answers: every message is still waiting when the next one goes
            c = b.choose(RIVAL, "buy", "duel:2", step, 40 + step)
            b.sent(c, their_price=100, their_offer=1, tick=100 + step)
            picks.append(c.tactic)
        assert all(x != y for x, y in zip(picks, picks[1:], strict=False)), (seed, picks)


def test_abuela_never_gets_a_non_kindness_tactic_whatever_the_history():
    rnd = random.Random(16)
    b = book(seed=1)
    for step in range(60):
        c = b.choose(ABUELA, rnd.choice(("buy", "sell")), "thread:5", step, rnd.randint(1, 60))
        assert c.tactic is None or BY_ID[c.tactic].kindness, c
        b.sent(c, their_price=rnd.randint(1, 60), their_offer=step, tick=step)
        b.observe("thread:5", their_price=rnd.randint(1, 60), their_offer=step + 1000, tick=step)


def test_a_cooloff_disables_that_tactic_for_that_dealer_for_the_rest_of_the_day():
    b = book()
    b.begin_tick(100, day=2)
    c = b.choose(CHATO, "buy", "thread:1", 0, 30)
    b.sent(c, their_price=33, their_offer=1, tick=100)
    b.ended("thread:1", status="closed", closed_reason="cooloff", tick=101)
    arms = b.arms(CHATO)
    assert arms[c.tactic].penalties_today == 1 and arms[c.tactic].total == PENALTY
    for n in range(20):
        assert b.choose(CHATO, "buy", f"thread:{n + 2}", 0, 30).tactic != c.tactic
    other = Counterparty.dealer("mercader")  # only for chato: another dealer still gets it, tried last
    tried = [play(b, other, "thread:40", step, 33, 33, 300 + 2 * step).tactic for step in range(4)]
    assert tried[-1] == c.tactic and sorted(tried) == sorted(eligible("dealer", "mercader", "buy"))
    b.begin_tick(500, day=3)  # a new game day: the tactic may be tried again (its mean stays low)
    assert b.arms(CHATO)[c.tactic].off_today() is None


def test_a_cooloff_event_for_us_right_after_a_tactic_is_the_same_one_penalty():
    b = book()
    c = b.choose(CHATO, "buy", "thread:1", 0, 30)
    b.sent(c, their_price=33, their_offer=1, tick=100)
    event = {"id": 77, "type": "persona.cooloff", "tick": 101, "payload": {"persona": "chato", "team": US}}
    b.events([event, event], US, 101)  # the feed window repeats events: counted once
    b.ended("thread:1", status="closed", closed_reason="cooloff", tick=101)  # and the thread says it too
    assert b.arms(CHATO)[c.tactic].penalties_today == 1  # one incident, one lesson (same key)
    other = {"id": 78, "type": "persona.cooloff", "tick": 101, "payload": {"persona": "chato", "team": "t09"}}
    b.events([other], US, 101)
    assert b.arms(CHATO)[c.tactic].penalties_today == 1


def test_a_strike_long_after_the_tactic_is_not_blamed_on_it():
    b = book()
    c = b.choose(CHATO, "buy", "thread:1", 0, 30)
    b.sent(c, their_price=33, their_offer=1, tick=100)
    b.events([{"id": 5, "type": "persona.strike", "payload": {"persona": "chato", "team": US}}], US, 120)
    assert b.arms(CHATO) == {}


def test_a_flag_on_one_of_our_tactic_messages_disables_it_for_that_counterparty():
    b = book()
    c = b.choose(RIVAL, "sell", "duel:9", 0, 90)
    b.sent(c, their_price=70, their_offer=1, tick=100, message=4242)
    b.events([{"id": 9, "type": "flag.raised", "payload": {"team": "t05", "message": 4242}}], US, 102)
    assert b.arms(RIVAL)[c.tactic].off_today() is not None
    assert all(b.choose(RIVAL, "sell", "duel:9", s, 90).tactic != c.tactic for s in range(1, 10))


def test_two_penalties_in_a_day_mute_every_tactic_to_that_counterparty():
    b = book()
    for n in range(MUTE_AFTER):
        c = b.choose(CHATO, "buy", f"thread:{n}", 0, 30)
        b.sent(c, their_price=33, their_offer=n, tick=100 + n)
        b.ended(f"thread:{n}", status="closed", closed_reason="cooloff", tick=101 + n)
    muted = b.choose(CHATO, "buy", "thread:99", 0, 30)
    assert muted.tactic is None and "muted" in muted.reason
    assert muted.words(plain)(WordsRequest("chato", 30)) == "plain 30"


def test_no_gain_turns_a_tactic_off_for_the_day():
    b = book(seed=11)
    target = b.choose(CHATO, "buy", "thread:1", 0, 30).tactic
    for step in range(NO_GAIN_TRIES):
        c = Choice(CHATO, "buy", "thread:1", step, 30, target, "forced")
        b.sent(c, their_price=33, their_offer=10 + step, tick=100 + step)
        b.observe("thread:1", their_price=34, their_offer=20 + step, tick=100 + step)  # moved away each time
    assert b.arms(CHATO)[target].off_today() == f"no gain in {NO_GAIN_TRIES} tries today"
    assert all(b.choose(CHATO, "buy", f"thread:{n}", 0, 30).tactic != target for n in range(2, 12))


def test_rewards_toward_held_away_deal_and_walked():
    b = book()
    for conv, after in (("thread:1", 31), ("thread:2", 33), ("thread:3", 35)):
        c = b.choose(CHATO, "buy", conv, 0, 30)
        b.sent(c, their_price=33, their_offer=1, tick=100)
        b.observe(conv, their_price=after, their_offer=2, tick=101)
    results = sorted((lr.detail["result"], lr.detail["reward"]) for lr in b.lessons.values())
    assert results == [("away", -0.5), ("held", 0.0), ("toward", 1.0)]
    seller = book()
    c = seller.choose(RIVAL, "sell", "duel:1", 0, 90)
    seller.sent(c, their_price=70, their_offer=1, tick=100)
    seller.observe("duel:1", their_price=75, their_offer=2, tick=101)  # a seller wants the bid to rise
    seller.ended("duel:1", status="deal", closed_reason=None, tick=102)
    seller.ended("duel:2", status="deal", closed_reason=None, tick=102)  # no tactic there: nothing
    got = sorted((lr.detail["result"], lr.detail["reward"]) for lr in seller.lessons.values())
    assert got == [("deal", 1.5), ("toward", 1.0)]
    walked = book()
    c = walked.choose(CHATO, "buy", "thread:7", 0, 30)
    walked.sent(c, their_price=33, their_offer=1, tick=100)
    walked.ended("thread:7", status="closed", closed_reason="walked", tick=103)
    assert [lr.detail["reward"] for lr in walked.lessons.values()] == [-1.0]


def test_an_unanswered_message_is_scored_held_when_we_send_the_next_one():
    b = book()
    first = b.choose(CHATO, "buy", "thread:1", 0, 30)
    b.sent(first, their_price=33, their_offer=1, tick=100)
    b.observe("thread:1", their_price=33, their_offer=1, tick=101)  # same offer: no answer yet
    assert b.lessons == {}
    b.sent(b.choose(CHATO, "buy", "thread:1", 1, 31), their_price=33, their_offer=1, tick=102)
    assert [lr.detail["result"] for lr in b.lessons.values()] == ["held"]


def test_neutral_closes_and_our_own_walk_teach_nothing():
    b = book()
    for n, reason in enumerate(("persona_quota", "sold_out", "idle", None)):
        c = b.choose(CHATO, "buy", f"thread:{n}", 0, 30)
        b.sent(c, their_price=33, their_offer=1, tick=100)
        b.ended(f"thread:{n}", status="closed", closed_reason=reason, tick=101)
    c = b.choose(CHATO, "buy", "thread:9", 0, 30)
    b.sent(c, their_price=33, their_offer=1, tick=100)
    b.dropped("thread:9")
    b.ended("thread:9", status="closed", closed_reason="cooloff", tick=101)  # after our walk: not ours to blame
    assert b.lessons == {}


@pytest.mark.parametrize(
    ("rules", "env", "why"),
    [
        (Guardrails(bluff_enabled=False), {}, "bluff_enabled = false"),
        (Guardrails(), {ENV: "0"}, f"{ENV}=0"),
        (Guardrails(), {ENV: " off "}, f"{ENV}=0"),
    ],
)
def test_the_kill_switches_turn_every_tactic_off(rules, env, why):
    b = book(rules=rules, env=env)
    for cp, side in ((CHATO, "buy"), (ABUELA, "buy"), (RIVAL, "sell")):
        c = b.choose(cp, side, "thread:1", 0, 30)
        assert c.tactic is None and why in c.reason
        assert c.words(plain)(WordsRequest(cp.id, 30)) == "plain 30"
    assert enabled(Guardrails(), {ENV: "1"}) == (True, "on") and enabled(None, {}) == (True, "on")


def test_the_env_switch_is_read_at_every_choice(monkeypatch):
    b = TacticBook(us=US)
    monkeypatch.setenv(ENV, "1")
    assert b.choose(CHATO, "buy", "thread:1", 0, 30).tactic is not None
    monkeypatch.setenv(ENV, "0")
    assert b.choose(CHATO, "buy", "thread:1", 0, 30).tactic is None


def test_the_words_carry_the_structured_price_and_never_a_private_number():
    b = book()
    for step in range(30):
        c = b.choose(CHATO, "buy", "thread:1", step, 50, avoid={44, 61})
        text = c.words(plain)(WordsRequest("chato", 50, step, "LAV-08", language="en"))
        assert 50 in numbers_in(text) and not ({44, 61} & numbers_in(text)), text
    c = b.choose(CHATO, "buy", "thread:1", 0, 50)
    assert c.words(plain)(WordsRequest("chato", 49)) == "plain 49"  # another price: today's words


def test_choice_inputs_name_the_tactic_and_the_counterparty():
    c = book().choose(RIVAL, "buy", "duel:3", 0, 60)
    assert c.inputs() == {"tactic": c.tactic, "tactic_counterparty": "rival:rival_plata", "tactic_why": c.reason}
    assert Counterparty.rival(None, 12).id == "duel_12" and Counterparty.rival("  ").id == "unknown"
    assert Counterparty.dealer("bad id!").id == "bad_id"


def test_lessons_round_trip_through_the_n3_store_and_another_process_reads_them():
    store = LearningStore()
    first = book(store=store)
    c = first.choose(CHATO, "buy", "thread:1", 0, 30)
    first.sent(c, their_price=33, their_offer=1, tick=100)
    first.ended("thread:1", status="closed", closed_reason="cooloff", tick=101)
    assert first.flush() == 1 and first.unwritten == []
    (row,) = store.recall(None, {"tactic"}, None, team=US)
    assert row.kind == "tactic" and row.source == "outcome" and row.subject == "chato"
    assert row.detail["tactic"] == c.tactic and row.detail["reward"] == PENALTY
    second = book(store=store)
    assert second.load() == 1
    assert all(second.choose(CHATO, "buy", f"thread:{n}", 0, 30).tactic != c.tactic for n in range(2, 12))


def test_tactic_lessons_never_reach_the_default_recall_of_jev_or_the_words():
    from bazaar_agent.learn.recall import HybridRecall, Query

    store = LearningStore()
    b = book(store=store)
    c = b.choose(CHATO, "buy", "thread:1", 0, 30)
    b.sent(c, their_price=33, their_offer=1, tick=100)
    b.observe("thread:1", their_price=31, their_offer=2, tick=101)
    b.flush()
    assert len(store.recall(None, {"tactic"}, None, team=US)) == 1  # the row is there...
    for query in (  # ...and neither the words context (subjects) nor Jev's (no subject) ever sees it
        Query(f"bid to chato for LAV-08 {c.tactic}", subjects=("chato",), team=US),
        Query(f"duel as buyer {c.tactic}", subject_kind="rival", team=US),
        Query(f"accept LAV-08 from chato {c.tactic}", team=US),
    ):
        found = HybridRecall(store, models=None).search(query)  # type: ignore[arg-type]
        assert found.status == "no_candidates" and found.candidates == 0, query


def test_a_broken_store_never_raises_and_tactics_go_on():
    class Broken(LearningStore):
        def record(self, learnings):  # type: ignore[no-untyped-def]
            raise RuntimeError("down")

        def recall(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("down")

    lines: list[str] = []
    b = book(store=Broken(), log=lines.append)
    c = b.choose(CHATO, "buy", "thread:1", 0, 30)
    b.sent(c, their_price=33, their_offer=1, tick=100)
    b.observe("thread:1", their_price=31, their_offer=2, tick=101)
    assert b.flush() == 0 and b.load() == 0
    assert any("bluff: write failed" in line for line in lines)
    assert b.choose(CHATO, "buy", "thread:2", 0, 30).tactic is not None


def test_running_sums_match_a_full_recount_after_replacements():
    from bazaar_agent.agents.bluff import PENALTY_RESULTS

    rnd = random.Random(7)
    b = book(seed=2)
    for n in range(300):
        cp = rnd.choice((CHATO, RIVAL, Counterparty.dealer("mercader")))
        conv = f"thread:{rnd.randint(1, 30)}"
        side = "sell" if cp is RIVAL and rnd.random() < 0.5 else "buy"
        c = b.choose(cp, side, conv, rnd.randint(0, 5), rnd.randint(5, 90))
        b.sent(c, their_price=50, their_offer=n, tick=100 + n)
        b.observe(conv, their_price=rnd.randint(45, 55), their_offer=n + 10_000, tick=100 + n)
        if rnd.random() < 0.1:
            b.ended(conv, status="closed", closed_reason=rnd.choice(("cooloff", "walked", "deal")), tick=100 + n)
    for (label, tactic), agg in b._per.items():
        rows = [
            lr
            for lr in b.lessons.values()
            if f"{lr.subject_kind}:{lr.subject}" == label and lr.detail["tactic"] == tactic
        ]
        assert agg.n == len(rows) and agg.total == pytest.approx(sum(lr.detail["reward"] for lr in rows))
        assert agg.penalties == sum(lr.detail["result"] in PENALTY_RESULTS for lr in rows)


def test_flush_reads_the_others_lessons_every_few_ticks_only():
    from bazaar_agent.agents.bluff import LOAD_EVERY

    class Counting(LearningStore):
        loads = 0

        def recall(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            Counting.loads += 1
            return super().recall(*args, **kwargs)

    b = book(store=Counting())
    for tick in range(100, 100 + 2 * LOAD_EVERY):
        b.begin_tick(tick, 1)
        b.flush()
    assert Counting.loads == 2
    b.begin_tick(50, 1)  # the clock went back (a simulator reset): read again at once
    b.flush()
    assert Counting.loads == 3


def test_the_seen_events_set_stays_bounded():
    from bazaar_agent.agents.bluff import SEEN_EVENTS_MAX

    b = book()
    for start in range(0, SEEN_EVENTS_MAX + 2000, 500):
        b.events([{"id": i, "type": "offer.listed", "payload": {}} for i in range(start, start + 500)], US, 1)
    assert len(b._seen_events) <= SEEN_EVENTS_MAX + 500
