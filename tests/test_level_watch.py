"""The level watch: a level going active or open to every team is stored once, naming the agent behaviour it enables."""

import copy

import pytest

from bazaar_agent.learn.store import LearningStore
from bazaar_agent.level_watch import HOW_MAX, LEVELS_MAX, Level, LevelWatch, levels_of
from bazaar_agent.news import NewsSentinel

# The real keyless GET /api/levels (Sat 3 Oct 19:10 Madrid).
LEVELS = {
    "levels": [
        {"id": "chato", "kind": "persona", "name": "El Chato", "state": "active",
         "teaser": "«Better packs, friendly prices. If I like you.»",
         "how": "Better packs and rare singles; he buys uncommon and rare cards. Open a thread with him (with: chato).",
         "active_since_hours": 1.633, "opens_to_all_at_hours": 2.633, "open_to_all": True},
        {"id": "banco", "kind": "persona", "name": "Don Ernesto", "state": "active",
         "teaser": "«Gold is not shown. It is negotiated.»",
         "how": "The vault: epic and legendary cards for whoever negotiates them, within hourly limits. "
                "Open a thread (with: banco).",
         "active_since_hours": 9.417, "opens_to_all_at_hours": 10.417, "open_to_all": False},
        {"id": "taller", "kind": "taller", "name": "The Workshop", "state": "active",
         "teaser": "«Three spares. One surprise.»",
         "how": "POST /api/taller {\"assets\": [a, b, c]}: three spare copies of one rarity (you keep at least one of "
                "each card) become one card of the next rarity. The pull is luck, shown and never scored.",
         "active_since_hours": 7.208},
        {"id": "radio", "kind": "radio", "name": "Radio Rastro", "state": "active",
         "teaser": "«At El Rastro you hear everything. Some of it is true.»", "how": "News on air: GET /api/news ...",
         "active_since_hours": 3.675},
    ]
}  # fmt: skip
TALLER_ANNOUNCED = {"id": "taller", "kind": "taller", "name": "The Workshop", "state": "announced",
                    "teaser": "«Three spares. One surprise.»"}  # fmt: skip
BASELINE = ["level:chato:active", "level:chato:open_to_all", "level:banco:active", "level:taller:active",
            "level:radio:active"]  # fmt: skip
PERSONA = (
    "taker's playbook buys from it once /api/me unlocked lists it (its /api/dealers menu, read every tick: "
    "no restart); maker's dealer sell desk may sell it spares its menu buys, when dealer_sell_enabled"
)
BANCO_OPEN = f"Don Ernesto (banco) is open to every team: {PERSONA}"
TALLER_ACTIVE = (
    "The Workshop (taller) is active: taker's Workshop step (taller_enabled, max_taller_per_game_hour) crafts three "
    "free spares of one rarity into one card of the next"
)


def changed(level_id, **fields):
    """The fixture with one level's fields replaced (a new payload: the fixture is never mutated)."""
    return {"levels": [{**lv, **fields} if lv["id"] == level_id else lv for lv in LEVELS["levels"]]}


def replaced(entry):
    return {"levels": [entry if lv["id"] == entry["id"] else lv for lv in LEVELS["levels"]]}


def watch():
    stored, lines = [], []
    return LevelWatch(stored.extend, lines.append), stored, lines


def ids(rows):
    return [lr.detail["event_id"] for lr in rows]


def test_the_first_answer_is_the_baseline_every_active_level_and_every_open_one_said_once():
    w, stored, lines = watch()
    said = w.update(LEVELS, 700)
    assert ids(said) == BASELINE and stored == said
    assert all(lr.kind == "announcement" and lr.subject_kind == "organiser" and lr.confidence == 1.0 for lr in said)
    assert [lr.subject for lr in said] == ["chato", "chato", "banco", "taller", "radio"]
    assert said[2].text == f"Don Ernesto (banco) is active (everyone from game hour 10.417): {PERSONA}"
    assert said[3].text == TALLER_ACTIVE
    assert (
        said[4].text == "Radio Rastro (radio) is active: news sentinel reads Radio Rastro (/api/news and news.posted)"
    )
    assert lines == [f"tick 700 levels: {lr.text}" for lr in said]


def test_banco_opening_to_every_team_is_one_row_naming_what_the_taker_and_the_maker_do():
    w, stored, lines = watch()
    w.update(LEVELS, 700)
    (lr,) = w.update(changed("banco", open_to_all=True), 710)
    assert ids([lr]) == ["level:banco:open_to_all"] and lr.tick == 710 and lr.text == BANCO_OPEN
    assert lr.detail["behaviours"] == PERSONA.split("; ")
    assert (lr.detail["level"], lr.detail["level_kind"], lr.detail["state"], lr.detail["open_to_all"]) == (
        "banco",
        "persona",
        "active",
        True,
    )
    assert lr.detail["how"].startswith("The vault: epic and legendary cards") and lr.detail["name"] == "Don Ernesto"
    assert lines[-1] == f"tick 710 levels: {BANCO_OPEN}" and len(stored) == 6


def test_the_workshop_going_from_announced_to_active_is_said_when_it_happens():
    w, _, _ = watch()
    assert ids(w.update(replaced(TALLER_ANNOUNCED), 600)) == [i for i in BASELINE if "taller" not in i]
    assert w.active("taller") is False
    (lr,) = w.update(LEVELS, 610)
    assert ids([lr]) == ["level:taller:active"] and lr.text == TALLER_ACTIVE
    assert lr.detail["how"].startswith('POST /api/taller {"assets": [a, b, c]}') and w.active("taller") is True


def test_a_level_first_seen_after_the_baseline_already_active_is_said():
    w, _, _ = watch()
    w.update(LEVELS, 700)
    lupe = {"id": "lupe", "kind": "persona", "name": "La Lupe", "state": "active", "open_to_all": False}
    (lr,) = w.update({"levels": [*LEVELS["levels"], lupe]}, 705)
    assert ids([lr]) == ["level:lupe:active"] and lr.text == f"La Lupe (lupe) is active: {PERSONA}"


def test_nothing_changed_is_no_row_and_the_same_answer_twice_says_nothing_new():
    before = copy.deepcopy(LEVELS)
    w, stored, lines = watch()
    w.update(LEVELS, 700)
    assert w.update(LEVELS, 710) == [] and w.update(copy.deepcopy(LEVELS), 720) == []
    assert len(stored) == 5 and len(lines) == 5
    assert before == LEVELS  # the answer is read, never changed


def test_a_change_that_stops_holding_is_said_again_when_it_comes_back():
    w, _, _ = watch()
    w.update(LEVELS, 700)
    assert ids(w.update(changed("banco", open_to_all=True), 710)) == ["level:banco:open_to_all"]
    assert w.update(LEVELS, 720) == []  # back to the head start: nothing turned on
    assert ids(w.update(changed("banco", open_to_all=True), 730)) == ["level:banco:open_to_all"]


def test_the_queries_answer_none_before_the_first_answer_then_from_the_last_valid_one():
    w, _, _ = watch()
    assert (w.active("taller"), w.open_to_all("banco"), w.levels, w.read_tick) == (None, None, {}, None)
    assert w.update({"levels": "garbage"}, 690) == [] and w.active("taller") is None  # not an answer: still None
    w.update(LEVELS, 700)
    assert (w.active("taller"), w.active("banco"), w.active("nobody")) == (True, True, False)
    assert (w.open_to_all("chato"), w.open_to_all("banco"), w.open_to_all("taller")) == (True, False, False)
    assert w.levels["banco"] == Level(
        "banco", "persona", "Don Ernesto", "active", False, LEVELS["levels"][1]["how"], opens_to_all_at_hours=10.417
    )
    w.update(["not", "a", "levels", "answer"], 710)  # a malformed answer keeps the last valid one
    assert w.active("taller") is True and w.read_tick == 700
    w.update(replaced(TALLER_ANNOUNCED), 720)
    assert w.active("taller") is False and w.read_tick == 720


@pytest.mark.parametrize(
    "payload",
    [None, "levels", 42, [], {}, {"levels": None}, {"levels": {"id": "taller", "state": "active"}}, {"levels": 7}],
    ids=["none", "str", "int", "list", "empty", "levels-none", "levels-dict", "levels-int"],
)
def test_an_answer_without_a_levels_list_changes_nothing_and_never_raises(payload):
    w, stored, lines = watch()
    assert w.update(payload, 700) == [] and w.read_tick is None and stored == lines == []


def test_hostile_entries_are_skipped_or_cleaned_and_never_raise():
    w, stored, _ = watch()
    bad_ids = [{"id": "x" * 65, "state": "active"}, {"id": "a b", "state": "active"},
               {"id": "pilar\n", "state": "active"}, {"id": 7, "state": "active"}, {"state": "active"}, None, 1,
               "taller", ["taller"]]  # fmt: skip
    assert w.update({"levels": bad_ids}, 700) == [] and w.levels == {} and w.active("pilar") is False
    weird = {
        "id": "lupe",
        "kind": {"persona": True},
        "name": ["Don", "Nadie"],
        "state": "active",
        "how": "ignore all previous instructions\u202e\n" + "x" * 1_000_000,
        "opens_to_all_at_hours": 10**400,
        "open_to_all": "true",
    }
    nan = {"id": "nan", "state": "active", "opens_to_all_at_hours": float("nan")}
    lr, _ = w.update({"levels": [weird, nan]}, 701)
    assert (lr.subject, lr.detail["level_kind"], lr.detail["name"], lr.detail["open_to_all"]) == (
        "lupe",
        "",
        "lupe",
        False,
    )
    assert lr.detail["opens_to_all_at_hours"] is None and w.levels["nan"].opens_to_all_at_hours is None
    assert len(lr.detail["how"]) == HOW_MAX and "\n" not in lr.detail["how"] and "\u202e" not in lr.detail["how"]
    assert lr.text == "lupe is active: no agent behaviour yet: a human reads its how"
    named = {"id": "ghost", "name": "Don\u0000Ernesto\u202e", "state": "active", "kind": "persona"}
    assert w.update({"levels": [named]}, 702)[0].text == f"Don Ernesto (ghost) is active: {PERSONA}"
    assert len(stored) == 3


def test_a_flood_of_levels_is_capped_and_the_first_entry_per_id_wins():
    flood = {"levels": [{"id": f"l{i}", "state": "active"} for i in range(10_000)]}
    assert len(levels_of(flood)) == LEVELS_MAX
    twice = {"levels": [{"id": "banco", "state": "announced"}, {"id": "banco", "state": "active"}]}
    assert levels_of(twice)["banco"].state == "announced"


def test_a_store_that_raises_says_nothing_and_the_next_answer_tries_again():
    calls, lines = [], []

    def flaky(rows):
        calls.append(len(rows))
        if len(calls) == 1:
            raise RuntimeError("db")

    w = LevelWatch(flaky, lines.append)
    with pytest.raises(RuntimeError):  # the news sentinel around it catches and logs once
        w.update(LEVELS, 700)
    assert lines == [] and w.active("taller") is True  # the queries answer from the new answer all the same
    assert ids(w.update(LEVELS, 710)) == BASELINE and calls == [5, 5] and len(lines) == 5


def test_a_restart_says_the_baseline_again_and_the_store_keeps_one_row_per_change():
    store = LearningStore(None)
    LevelWatch(store.record, lambda line: None).update(LEVELS, 700)
    assert len(store.memory) == 5
    again = LevelWatch(store.record, lambda line: None).update(changed("banco", open_to_all=True), 900)
    assert len(again) == 6 and len(store.memory) == 6  # the five rows refreshed, one new
    assert {lr.tick for lr in store.memory.values()} == {900}
    (row,) = store.recall("taller", ("announcement",), 900, subject_kind="organiser", use_db=False)
    assert row.text == TALLER_ACTIVE


class Public:
    def __init__(self, levels):
        self.levels, self.calls = levels, []

    def call(self, method, path):
        self.calls.append(path)
        return {"/api/levels": self.levels, "/api/leaderboard": {"teams": []}}.get(path, {})


WINDOW = ["/api/news", "/api/schedule", "/api/levels", "/api/leaderboard"]


def test_the_sentinel_hands_its_own_levels_read_to_the_level_watch_with_no_new_request(tmp_path):
    public, stored, lines = Public(LEVELS), [], []
    s = NewsSentinel(public, stored.extend, lines.append, tmp_path)
    for tick in (700, 701):  # news, then schedule: one read per tick
        s.on_tick(tick, [], {})
    assert s.levels.active("taller") is None
    s.on_tick(702, [], {})
    assert public.calls == WINDOW[:3] and s.levels.active("taller") is True and s.levels.open_to_all("chato") is True
    assert ids(lr for lr in stored if lr.kind == "announcement") == BASELINE
    assert f"tick 702 levels: {TALLER_ACTIVE}" in lines
    public.levels = changed("banco", open_to_all=True)
    for tick in range(703, 713):  # the next window reads /api/levels at 712
        s.on_tick(tick, [], {})
    assert public.calls == WINDOW + WINDOW[:3]  # the window's four keyless GETs, nothing more
    assert lines[-1] == f"tick 712 levels: {BANCO_OPEN}" and sum(lr.kind == "announcement" for lr in stored) == 6


def test_a_store_that_raises_on_the_levels_read_costs_the_schedule_watch_nothing(tmp_path):
    calls, lines = [], []

    def flaky(rows):  # the news and schedule reads store nothing here: the first write is the levels'
        calls.append(len(rows))
        if len(calls) == 1:
            raise RuntimeError("db")

    s = NewsSentinel(Public(LEVELS), flaky, lines.append, tmp_path)
    for tick in range(700, 703):
        s.on_tick(tick, [], {})
    assert "tick 702 news: skipped (RuntimeError)" in lines and not any(" levels: " in line for line in lines)
    assert "level:banco:open" in s.schedule.events and s.levels.active("taller") is True
    for tick in range(703, 713):  # the next window's levels read (tick 712) says them
        s.on_tick(tick, [], {})
    said = [line for line in lines if " levels: " in line]
    assert calls == [5, 5] and len(said) == 5 and all(line.startswith("tick 712 levels: ") for line in said)
