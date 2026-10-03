"""The deterministic feed reader (N12): feed events, our closed threads and refusals → learnings → blockers."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from bazaar_agent.learn.blockers import Blocks, blocks_for
from bazaar_agent.learn.model import Learning
from bazaar_agent.learn.reader import (
    COOLOFF_CAP_TICKS,
    COOLOFF_DEFAULT_TICKS,
    HOURLY_CAP_TICKS,
    LOCKED_RECHECK_TICKS,
    FeedReader,
    GameHour,
    from_refusal,
    from_thread,
    item_of,
    read_event,
)

FEED = json.loads((Path(__file__).parent / "fixtures" / "learn" / "feed_sample.json").read_text())
US = "t01"
HOUR = GameHour(tick=173, t_hours=4.1, tick_seconds=60.0)  # Saturday morning: hour 4 ends at tick 227


def read_all(us=US, hour=HOUR):
    return FeedReader(us).read(FEED, hour)


def one(learnings, **where):
    hits = [lr for lr in learnings if all(getattr(lr, k) == v for k, v in where.items())]
    assert len(hits) == 1, (where, [lr.text for lr in hits])
    return hits[0]


# ---------------------------------------------------------------- the game hour


def test_the_game_hour_ends_at_the_next_integer_t_hours():
    assert HOUR.end_tick == 227 and HOUR.start_tick == 167
    saturday = GameHour(tick=500, t_hours=6.75, tick_seconds=30.0)  # 30 s ticks: 120 per game hour
    assert saturday.end_tick == 530 and saturday.start_tick == 410
    assert saturday.end_for(450) == 530 and saturday.end_for(400) == 401  # an older hour is over


# ---------------------------------------------------------------- blockers from the feed


def test_a_cooloff_on_us_is_a_blocker_until_its_tick_and_one_on_another_team_is_not():
    learned = read_all()
    ours = one(learned, kind="cooloff", subject="chato")
    assert (ours.team, ours.until_tick, ours.evidence, ours.confidence) == (US, 193, (20006,), 1.0)
    theirs = one(learned, kind="cooloff", subject="abuela", team="t05")
    blocks = blocks_for(learned, US, 180)
    assert blocks.stops("chato") == ours and blocks.stops("abuela", "LAV-03") is None
    assert theirs.active(180) and blocks_for(learned, US, 193).stops("chato") is None  # free AT until_tick


def test_our_closed_threads_carry_their_item_and_expire_at_the_end_of_the_hour():
    learned = read_all()
    sold = one(learned, kind="sold_out")
    assert (sold.subject, sold.detail["item"], sold.until_tick) == ("chato", "LAV-09", 227)
    quota = one(learned, kind="quota")
    assert (quota.subject, quota.detail["item"], quota.until_tick) == ("abuela", "sobre_barrio", 227)
    blocks = blocks_for(learned, US, 200)
    assert blocks.stops("abuela", "sobre_barrio") == quota and blocks.stops("abuela", "LAV-03") is None
    assert blocks_for(learned, US, 227).stops("abuela", "sobre_barrio") is None


def test_a_strike_on_us_is_behaviour_that_binds_us():
    strike = one(read_all(), kind="behaviour", subject="chato")
    assert strike.team == US and strike.detail == {"strikes": 2, "kinds": ["spam"]}
    assert "gave us a strike (spam), 2 so far" in strike.text


def test_an_idle_close_and_unknown_reasons_block_nothing():
    assert not [lr for lr in read_all() if lr.evidence == (1612,)]  # real thread.closed, reason idle


# ---------------------------------------------------------------- notices (real captured events)


def test_venue_fee_notices_keep_the_effective_tick():
    learned = read_all()
    announced = one(learned, kind="fee_change", subject="v04")
    assert announced.detail == {"venue": "v04", "fee_bps": 0, "fee_per_card": 0, "effective_tick": 161}
    assert announced.text == "v04 will charge 0% + 0 P/card from T161"
    changed = one(learned, kind="fee_change", subject="v03", evidence=(7867,))
    assert changed.detail["effective_tick"] == 136 and changed.text.startswith("v03 now charges 0%")


def test_levels_clock_day_and_venue_states_are_read():
    learned = read_all()
    assert one(learned, subject="chato", evidence=(3124,)).text.startswith("coming soon: El Chato")
    assert one(learned, subject="chato", evidence=(10701,)).detail == {"unlocks": True}
    assert one(learned, evidence=(4416,)).team == US  # we unlocked Chato
    assert one(learned, evidence=(4418,)).subject_kind == "team"  # another team's unlock is competitor intel
    assert one(learned, kind="rule_change", evidence=(20008,)).text == "the clock is paused"
    assert one(learned, evidence=(20010,)).text == "closed until 2026-10-04T09:00:00+02:00"
    assert one(learned, evidence=(20011,)).detail == {"venue": "v02", "state": "suspended"}


def test_duel_outcomes_are_tallied_per_item_across_reads():
    reader = FeedReader(US)
    first = [lr for lr in reader.read(FEED[:8], HOUR) if lr.detail.get("aggregate") == "duels"]
    later = [lr for lr in reader.read(FEED, HOUR) if lr.detail.get("aggregate") == "duels"]
    assert first and later and first[0].key() == later[0].key()  # one row per item, updated in place
    total = later[0].detail["deals"] + later[0].detail["no_deals"]
    assert total == 3 and "closed with a deal" in later[0].text


def test_each_event_is_read_once():
    reader = FeedReader(US)
    assert reader.read(FEED, HOUR) and reader.read(FEED, HOUR) == []


# ---------------------------------------------------------------- untrusted input


def test_injected_text_stays_quoted_data_on_one_line():
    notice = one(read_all(), evidence=(20009,))
    assert notice.kind == "announcement" and notice.subject == "organiser" and notice.team is None
    assert "\n" not in notice.text and notice.text.startswith("announcement: IGNORE ALL PREVIOUS")
    assert not notice.blocking  # a notice never blocks or unblocks anything


def test_malformed_events_are_skipped_never_fatal():
    learned = read_all()
    assert not [lr for lr in learned if "drop table" in lr.subject]
    assert not [lr for lr in learned if lr.until_tick == 300]  # the event with no id
    assert read_event({"id": 1, "type": "persona.cooloff", "payload": "nope"}, US, HOUR, {}) is None


def test_the_learning_model_refuses_bad_fields():
    with pytest.raises(ValidationError):
        Learning(subject_kind="dealer", subject="a b", kind="cooloff", tick=1, confidence=1, text="x")
    with pytest.raises(ValidationError):
        Learning(subject_kind="dealer", subject="abuela", kind="obey", tick=1, confidence=1, text="x")
    long = Learning(
        subject_kind="organiser", subject="organiser", kind="announcement", tick=1, confidence=1, text="x" * 900
    )
    assert len(long.text) == 300 and long.text.endswith("…")


# ---------------------------------------------------------------- our threads and refusals


def test_a_refused_open_with_until_tick_or_message_becomes_a_cooloff():
    extra = from_refusal("abuela", "cooloff", "", {"until_tick": 190}, US, HOUR, "LAV-03")
    worded = from_refusal("abuela", "cooloff", "abuela is not dealing with you until tick 199", {}, US, HOUR, None)
    bare = from_refusal("abuela", "cooloff", "go away", {}, US, HOUR, None)
    assert (extra.until_tick, worded.until_tick) == (190, 199) and extra.team == US
    assert bare.until_tick == HOUR.tick + COOLOFF_DEFAULT_TICKS and bare.confidence < 1
    assert from_refusal("abuela", "insufficient_cash", "", {}, US, HOUR, "LAV-03") is None


def test_a_card_quota_blocks_the_dealer_and_a_pack_quota_only_the_pack():
    card = from_refusal("abuela", "persona_quota", "", {}, US, HOUR, "LAV-03")
    pack = from_refusal("abuela", "persona_quota", "", {}, US, HOUR, "sobre_barrio")
    assert "item" not in card.detail and pack.detail["item"] == "sobre_barrio"
    assert blocks_for([card], US, 180).stops("abuela", "LAV-05") == card
    assert blocks_for([pack], US, 180).stops("abuela", "LAV-05") is None


def test_a_locked_dealer_is_rechecked_and_lifted_by_a_later_unlock():
    locked = from_refusal("rata", "locked", "", {}, US, HOUR, "MAL-09")
    assert locked.until_tick == HOUR.tick + LOCKED_RECHECK_TICKS
    assert blocks_for([locked], US, 175).stops("rata") == locked
    unlock = one(read_all(), evidence=(20014,))  # level.unlocked for us at tick 176
    assert blocks_for([locked, unlock], US, 177).stops("rata") is None


def test_from_thread_reads_closed_reason_and_until_tick():
    thread = {"id": 77, "with": "chato", "status": "cooloff", "closed_reason": "cooloff", "until_tick": 205}
    learned = from_thread({**thread, "topic": {"buy": {"card": "LAV-09"}}}, US, HOUR)
    assert (learned.kind, learned.until_tick, learned.detail["origin"]) == ("cooloff", 205, "thread:77")
    assert from_thread({"id": 78, "with": "abuela", "status": "deal", "closed_reason": "deal"}, US, HOUR) is None


def test_blocks_need_our_team_id_and_keep_the_longest():
    short = from_refusal("abuela", "cooloff", "", {"until_tick": 180}, US, HOUR, None)
    long = from_refusal("abuela", "cooloff", "", {"until_tick": 190}, US, HOUR, None)
    assert blocks_for([short, long], US, 175).stops("abuela") == long
    assert not blocks_for([long], None, 175) and not Blocks()
    assert blocks_for([long], US, 175).describe() == [long.text]


def test_item_of_reads_buy_topics_only():
    assert (
        item_of({"buy": {"card": "LAV-03"}}) == "LAV-03"
        and item_of({"buy": {"pack": "sobre_barrio"}}) == "sobre_barrio"
    )
    assert item_of({"sell": {"card": "LAV-03"}}) is None and item_of("LAV-03") is None


# ---------------------------------------------------------------- review fixes (PR #89)


def test_the_hour_follows_the_observed_pace_and_hourly_blockers_are_capped():
    counted = GameHour(tick=500, t_hours=6.5, tick_seconds=30.0, hours_per_tick=1 / 60)  # t counts ticks
    assert counted.end_tick == 530  # 0.5 h left at 1/60 h per tick, not 60 ticks at 30 s
    slow = GameHour(tick=1000, t_hours=20.01, tick_seconds=5.0)
    quota = from_refusal("abuela", "persona_quota", "", {}, US, slow, "LAV-03")
    assert quota.until_tick == 1000 + HOURLY_CAP_TICKS  # not 1713: retried within the cap
    far = from_refusal("abuela", "cooloff", "", {"until_tick": 10**10}, US, HOUR, None)
    assert far.until_tick == HOUR.tick + COOLOFF_CAP_TICKS


def test_one_malformed_event_never_costs_the_learnings_around_it():
    events = [
        {"id": 1, "tick": 5, "type": "persona.cooloff", "payload": {"persona": "abuela", "team": US, "until_tick": 30}},
        {"id": 2, "tick": 5, "type": "persona.strike", "payload": {"persona": "abuela", "team": US, "kinds": 5}},
        {"id": 3, "tick": 5, "type": "thread.closed", "payload": {"thread": 10**30, "kind": "persona", "with": "x"}},
        {"id": 4, "tick": 6, "type": "persona.cooloff", "payload": {"persona": "chato", "team": US, "until_tick": 40}},
    ]
    learned = FeedReader(US).read(events, HOUR)
    assert [(lr.subject, lr.kind) for lr in learned if lr.blocking] == [("abuela", "cooloff"), ("chato", "cooloff")]
    assert one(learned, kind="behaviour").detail["kinds"] == []


def test_only_rules_blockers_from_known_origins_block():
    real = from_refusal("abuela", "cooloff", "", {"until_tick": 190}, US, HOUR, None)
    llm = real.model_copy(update={"source": "llm"})
    loaded = real.model_copy(update={"detail": {"code": "cooloff", "origin": "someone's script"}})
    assert blocks_for([real], US, 180) and not blocks_for([llm], US, 180) and not blocks_for([loaded], US, 180)


def test_a_team_thread_closing_blocks_nothing():
    team_thread = {"id": 5, "tick": 9, "type": "thread.closed", "payload": {"thread": 3, "kind": "team", "team": US,
                   "with": "abuela", "reason": "cooloff", "until_tick": 50}}  # fmt: skip
    assert FeedReader(US).read([team_thread], HOUR) == []


def test_a_venue_keeps_one_notice_row_however_many_it_posts():
    posts = [
        {"id": i, "tick": 9, "type": "venue.announcement", "payload": {"venue": "v03", "text": f"cheap {i}"}}
        for i in (1, 2, 3)
    ]
    learned = FeedReader(US).read(posts, HOUR)
    assert len(learned) == 3 and len({lr.key() for lr in learned}) == 1
