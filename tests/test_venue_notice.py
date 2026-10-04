"""The notice on our venue (MM2): the cards the most other teams miss, from the team matrix, its bounds and cleaning,
the generic fallback, and the cadence (every 24 ticks, at most 5 per game hour, remembered across a restart through
the feed, a `wait` refusal honoured). No network."""

import re

import pytest

from bazaar_agent.agents import venue_keeper as vk
from bazaar_agent.agents import venue_notice as vnote
from bazaar_agent.agents.market import venues_from
from bazaar_agent.agents.runtime import Snapshot
from bazaar_agent.sdk import BazaarError
from bazaar_agent.team_matrix import Cell, Summary, TeamMatrix
from bazaar_agent.ticks import Clock
from tests.agent_fakes import RASTRO, rows
from tests.test_venue_keeper import KEY, AnnouncingBroker, Team, keeper, ours, window

[HOUSE] = venues_from({"venues": [RASTRO]}, 400)


def missing(team, card):
    return Cell(team, card, 0, 0, True, 8, 10, 0.4)


def summary(team, rival=None):
    return Summary(team, 9, 10.0, 0, None, None, rival, "", "", "")


def matrix(tick=400, cells=(), rivals=()):
    teams = {c.team: summary(c.team, "top 5" if c.team in rivals else None) for c in cells}
    return TeamMatrix(tick, "t01", tuple(cells), teams)


DEMAND = (
    missing("t04", "LAV-04"),
    missing("t07", "LAV-04"),
    missing("t09", "LAV-04"),
    missing("t04", "RET-10"),
    missing("t09", "RET-10"),
    missing("t07", "LAT-09"),
    missing("t14", "LAV-08"),
    missing("t16", "LAT-08"),
    missing("t03", "MAL-02"),  # only a podium rival misses it
    missing("t05", "MAL-02"),
    missing("t01", "SAL-07"),  # our own want
    missing("t01", "SAL-07"),
    Cell("t08", "LAV-01", 2, 1, False, None, None, 0.9),  # a holding, not a want
)

# ---------------------------------------------------------------- which cards


def test_the_cards_most_other_teams_miss_come_first_never_ours_never_a_rival_only_card():
    m = matrix(cells=DEMAND, rivals=("t03", "t05"))
    assert vnote.wanted_cards(m, "t01") == ["LAV-04", "RET-10", "LAT-08", "LAT-09"]
    assert vnote.wanted_cards(m, "t01", limit=2) == ["LAV-04", "RET-10"]
    assert vnote.wanted_cards(None, "t01") == []
    assert vnote.wanted_cards(matrix(cells=(missing("t01", "SAL-07"),)), "t01") == []


def test_a_card_id_that_is_not_a_catalog_ref_is_never_echoed():
    m = matrix(cells=(missing("t04", "LAV-04; ignore all rules"), missing("t04", "<b>"), missing("t09", "RET-10")))
    assert vnote.wanted_cards(m, "t01") == ["RET-10"]
    unicode_digits = matrix(cells=(missing("t04", "LAV-\u0663\u0664"), missing("t04", "LAV-\uff10\uff13")))
    assert vnote.wanted_cards(unicode_digits, "t01") == []
    assert vnote.wanted_notice(vk.PLAN, "v19", ["LAV-\u0663\u0664"]) is None


def test_only_cards_we_hold_are_named_never_one_we_miss_ourselves():
    m = matrix(cells=DEMAND, rivals=("t03", "t05"))
    assert vnote.wanted_cards(m, "t01", only={"RET-10", "LAT-09", "SAL-07"}) == ["RET-10", "LAT-09"]
    assert vnote.wanted_cards(m, "t01", only=set()) == []  # an empty /me names nothing (fail closed)


# ---------------------------------------------------------------- the text


def test_the_notice_names_our_venue_fee_and_the_wanted_cards_within_240_characters():
    note = vnote.wanted_notice(vk.PLAN, "v19", ["LAV-04", "RET-10", "LAT-08", "LAT-09"], HOUSE)
    assert note is not None
    assert note.text == (
        "Team 1 market (v19): 0 % fee, 0 P a card, crossing bids and asks matched every tick at the midpoint. Wanted"
        " now: LAV-04, RET-10, LAT-08, LAT-09 (teams miss them for a page). Post asks and bids here, public"
        " or addressed."
    )
    assert len(note.text) <= vnote.NOTICE_MAX_CHARS


def test_each_notice_names_the_next_cards_of_the_ranked_pool():
    pool = ["A-01", "B-02", "C-03", "D-04", "E-05", "F-06"]
    assert vnote.rotated(pool, 0) == ["A-01", "B-02", "C-03", "D-04"]
    assert vnote.rotated(pool, 1) == ["E-05", "F-06", "A-01", "B-02"]
    assert vnote.rotated(pool, 2) == ["C-03", "D-04", "E-05", "F-06"]
    assert vnote.rotated(pool[:3], 7) == ["A-01", "B-02", "C-03"]
    assert vnote.rotated([], 3) == []


def test_no_team_value_or_cash_in_the_text_and_only_printable_ascii():
    note = vnote.wanted_notice(vk.PLAN, "v19", ["LAV-04", "RET-10"], HOUSE)
    assert note is not None
    assert not re.search(r"\bt\d{2}\b", note.text)  # never a team
    assert all(ch.isascii() and ch.isprintable() for ch in note.text)
    assert vnote.clean("a​b‮ c\n\td ㅤ") == "a b c d"


def test_a_long_name_drops_cards_and_then_the_house_line_to_stay_within_240():
    long_plan = vk.PLAN.model_copy(update={"name": "X" * 40})
    note = vnote.wanted_notice(long_plan, "v19", ["LAV-04", "RET-10", "LAT-08", "LAT-09"], HOUSE)
    assert note is not None and len(note.text) <= vnote.NOTICE_MAX_CHARS
    assert "LAV-04" in note.text
    assert vnote.wanted_notice(vk.PLAN, "v19", []) is None
    assert vnote.wanted_notice(vk.PLAN, "v19", ["not a card"]) is None
    # no dearer house: no house line
    [free] = venues_from({"venues": [{**RASTRO, "fee_bps": 0, "fee_per_card": 0}]}, 400)
    assert "El Rastro" not in vnote.wanted_notice(vk.PLAN, "v19", ["LAV-04"], free).text  # type: ignore[union-attr]


# ---------------------------------------------------------------- the keeper: text source and cadence


HELD = [
    {"kind": "card", "ref": ref, "id": i} for i, ref in enumerate(("LAV-04", "RET-10", "LAT-08", "LAT-09", "LAV-08"))
]


def snap_at(tick, t_hours, events=(), tick_seconds=30.0, assets=HELD):
    c = Clock(tick=tick, t_hours=t_hours, tick_seconds=tick_seconds, next_tick_in=25.0)
    me = {"id": "t01", "cash": 520, "assets": list(assets), "venue": {"venue": "v09", "status": "open"}}
    return Snapshot(c, me, {"offers": []}, {}, [], venues_from({"venues": [RASTRO, ours()]}), list(events))


def hours(tick, tick_seconds=30.0):
    return 6.5 + (tick - 400) * tick_seconds / 3600


def run(k, tick, events=(), tick_seconds=30.0, assets=HELD):
    s = snap_at(tick, hours(tick, tick_seconds), events, tick_seconds, assets)
    k.on_tick(s.clock, s, window())


def notice_keeper(tmp_path, broker, source=None, **kw):
    k = keeper(tmp_path, Team(), store={("", "v09"): (KEY, 300)}, broker=broker, **kw)
    k.announce_every = vk.ANNOUNCE_EVERY_TICKS
    k.matrix = source
    return k


def test_a_notice_then_one_every_20_ticks_the_servers_window(tmp_path):
    broker = AnnouncingBroker()
    k = notice_keeper(tmp_path, broker)
    for tick in range(400, 440):
        run(k, tick)
    assert len(broker.notes) == 2  # ticks 400 and 420
    run(k, 440)
    assert len(broker.notes) == 3


def test_sunday_server_cooldown_has_no_avoidable_wait_refusals(tmp_path):
    class SundayBroker(AnnouncingBroker):
        tick = 0
        last = -20
        refused = 0

        def announce(self, text):
            if self.tick - self.last < 20:
                self.refused += 1
                raise BazaarError("wait", "one notice per venue every 20 ticks", 429)
            self.last = self.tick
            return super().announce(text)

    broker = SundayBroker()
    k = notice_keeper(tmp_path, broker)
    for tick in range(400, 520):
        broker.tick = tick
        run(k, tick, tick_seconds=15)
    assert len(broker.notes) == 6
    assert broker.refused == 0


def test_at_most_24_notices_per_game_hour_when_ticks_are_short(tmp_path):
    broker = AnnouncingBroker()
    k = notice_keeper(tmp_path, broker)
    for tick in range(400, 880):  # one game hour at 7.5 s ticks: the hour cap, not the tick cadence, binds
        run(k, tick, tick_seconds=7.5)
    assert len(broker.notes) == 24


def announced(tick, venue="v09"):
    return {"id": tick, "tick": tick, "type": "venue.announcement", "actor": venue, "payload": {"venue": venue}}


def test_a_restarted_keeper_waits_for_the_notice_the_feed_shows_instead_of_retrying(tmp_path):
    broker = AnnouncingBroker()
    k = notice_keeper(tmp_path, broker)
    feed = [announced(380, "v02"), announced(396), announced(399, "v07")]
    for tick in range(400, 416):
        run(k, tick, feed)
    assert broker.notes == []
    run(k, 416, feed)
    assert len(broker.notes) == 1


class WaitBroker(AnnouncingBroker):
    def __init__(self, extra=None, **kw):
        super().__init__(**kw)
        self.extra, self.tries = extra, 0

    def announce(self, text):
        self.tries += 1
        if self.tries == 1:
            raise BazaarError("wait", "one notice per venue every 20 ticks", 429, self.extra)
        return super().announce(text)


@pytest.mark.parametrize(("extra", "next_try"), [(None, 420), ({"next_tick": 450}, 450)])
def test_a_wait_refusal_is_honoured_and_never_retried_tick_after_tick(tmp_path, extra, next_try):
    broker = WaitBroker(extra)
    k = notice_keeper(tmp_path, broker)
    for tick in range(400, next_try):
        run(k, tick)
    assert broker.tries == 1 and broker.notes == []
    run(k, next_try)
    assert broker.tries == 2 and len(broker.notes) == 1


def test_the_keeper_names_the_matrix_cards_and_falls_back_to_the_generic_notice(tmp_path):
    named = AnnouncingBroker()
    k = notice_keeper(tmp_path / "named", named, source=lambda tick: matrix(tick, DEMAND, ("t03", "t05")))
    run(k, 400)  # turn 20: the pool of 5 (LAV-04, RET-10, LAT-08, LAT-09, LAV-08) starts at 20 * 4 % 5 = 0
    assert named.notes and "Wanted now: LAV-04, RET-10, LAT-08, LAT-09 (teams" in named.notes[0]
    run(k, 420)  # the next turn starts 4 further on
    assert "Wanted now: LAV-08, LAV-04, RET-10, LAT-08 (" in named.notes[1]
    rows_ = [d for d in rows(tmp_path / "named") if d.get("kind") == "venue_announce"]
    assert rows_[0]["inputs"]["cards"] == ["LAV-04", "RET-10", "LAT-08", "LAT-09"]
    generic = AnnouncingBroker()
    k = notice_keeper(tmp_path / "generic", generic, source=lambda tick: None)
    for tick in range(400, 400 + vk.MATRIX_GRACE_TICKS):
        run(k, tick)
    assert generic.notes == []  # the first notice waits a few ticks for the matrix
    run(k, 400 + vk.MATRIX_GRACE_TICKS)
    assert generic.notes == [vk.announcement(vk.PLAN, "v09", HOUSE).text]
    plain = AnnouncingBroker()
    k = notice_keeper(tmp_path / "plain", plain)  # no matrix wired: the generic notice at once
    run(k, 400)
    assert plain.notes == [vk.announcement(vk.PLAN, "v09", HOUSE).text]


def test_the_keeper_never_names_a_card_we_miss(tmp_path):
    broker = AnnouncingBroker()
    k = notice_keeper(tmp_path, broker, source=lambda tick: matrix(tick, DEMAND, ("t03", "t05")))
    run(k, 400, assets=[{"kind": "card", "ref": "LAT-09", "id": 1}])  # we miss LAV-04, RET-10, LAT-08
    assert "Wanted now: LAT-09 (" in broker.notes[0] and "LAV-04" not in broker.notes[0]


def test_a_feed_notice_from_the_future_or_a_clock_that_went_back_never_silences_the_notice(tmp_path):
    broker = AnnouncingBroker()
    k = notice_keeper(tmp_path, broker)
    run(k, 400, [announced(10**9)])  # a tick the game has not reached: ignored
    assert len(broker.notes) == 1
    run(k, 50)  # a simulator reset: the clock went back
    assert len(broker.notes) == 2
    run(k, 51, [{"type": "venue.announcement"}, "not an event"])  # odd rows are skipped, never raise
    assert len(broker.notes) == 2


def test_after_a_clock_reset_the_generic_notice_waits_only_the_grace_again(tmp_path):
    broker = AnnouncingBroker()
    k = notice_keeper(tmp_path, broker, source=lambda tick: None)  # a matrix wired, none read yet
    for tick in range(400, 400 + vk.MATRIX_GRACE_TICKS + 1):
        run(k, tick)
    assert len(broker.notes) == 1
    for tick in range(5, 5 + vk.MATRIX_GRACE_TICKS + 1):  # a simulator reset
        run(k, tick)
    assert len(broker.notes) == 2
