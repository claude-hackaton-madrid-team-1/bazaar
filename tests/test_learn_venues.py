"""The maker's venue notices and the taker's LLM reader hook (N12 part 2). Both fail open."""

import json

from bazaar_agent.agents.maker import Maker
from bazaar_agent.agents.market import venues_from
from bazaar_agent.learn.live import LiveLearner
from bazaar_agent.learn.model import Learning
from bazaar_agent.learn.reader import FeedReader
from bazaar_agent.learn.store import LearningStore
from bazaar_agent.learn.venues import VenueNotices, adjust
from bazaar_agent.ticks import Clock
from tests.agent_fakes import CHEAP, RASTRO, TICK, FakePublic, FakeTeam, clock, parts
from tests.test_learn_reader import FEED, US


def fee_notice(venue, bps, effective, tick=100, per_card=0):  # type: ignore[no-untyped-def]
    return Learning(
        subject_kind="venue",
        subject=venue,
        kind="fee_change",
        tick=tick,
        confidence=1,
        text="notice",
        evidence=(tick,),
        detail={"venue": venue, "fee_bps": bps, "fee_per_card": per_card, "effective_tick": effective},
    )


def test_a_fee_announced_within_the_listing_life_counts_now():
    venues = venues_from({"venues": [RASTRO, CHEAP]})
    cheap = [v for v in adjust(venues, [fee_notice("v02", 900, 120)], 100, 40) if v.id == "v02"][0]
    assert cheap.fee_bps == 900  # v02 is 0% now but charges 9% from T120, inside a 40-tick listing
    later = [v for v in adjust(venues, [fee_notice("v02", 900, 200)], 100, 40) if v.id == "v02"][0]
    assert later.fee_bps == 0  # beyond the listing's life
    cut = [v for v in adjust(venues, [fee_notice("rastro", 0, 120)], 100, 40) if v.id == "rastro"]
    assert cut[0].fee_bps == 500  # a cut does not count before it happens


def test_a_closing_venue_is_left_out_until_it_reopens():
    venues = venues_from({"venues": [RASTRO, CHEAP]})
    closing = Learning(
        subject_kind="venue",
        subject="v02",
        kind="announcement",
        tick=90,
        confidence=1,
        text="x",
        evidence=(1,),
        detail={"venue": "v02", "state": "closing"},
    )
    back = closing.model_copy(update={"tick": 95, "evidence": (2,), "detail": {"venue": "v02", "state": "reopened"}})
    assert [v.id for v in adjust(venues, [closing], 100, 40)] == ["rastro"]
    assert [v.id for v in adjust(venues, [closing, back], 100, 40)] == ["rastro", "v02"]


def test_venue_notices_read_the_feed_and_fail_open():
    notices = VenueNotices()
    notices.update(FEED, US)
    assert {lr.subject for lr in notices.notices} >= {"v03", "v04", "v02"}
    venues = venues_from({"venues": [RASTRO, CHEAP]})
    assert [v.id for v in notices.adjust(venues, 100, 40)] == ["rastro"]  # v02 was suspended (fixture)
    lines: list[str] = []
    broken = VenueNotices(lines.append)
    broken.notices = [object()]  # type: ignore[list-item]
    assert broken.adjust(venues, 100, 40) == venues and len(lines) == 1


def test_the_maker_scores_a_venue_at_its_announced_fee(tmp_path):
    busy = {**CHEAP, "trades": 45}  # a 0% venue busier than El Rastro: today's choice for every listing

    def venues_posted(notices, path):  # type: ignore[no-untyped-def]
        lines: list[str] = []
        m = Maker(
            FakeTeam(),
            FakePublic(venues=(RASTRO, busy)),
            live=False,
            log=lines.append,
            now=lambda: 1000.0,
            notices=notices,
            **parts(path),
        )
        m.on_tick(clock())
        return {line.split(" on ", 1)[1].split(" ", 1)[0] for line in lines if "WOULD post" in line}

    assert venues_posted(None, tmp_path / "a") == {"v02"}
    noticed = VenueNotices()
    noticed.notices = [fee_notice("v02", 1000, TICK + 10, per_card=5)]
    noticed.reader = FeedReader(US)  # already reading: the tick's update adds nothing from the fake feed
    noticed.reader.newest = 10**9
    assert venues_posted(noticed, tmp_path / "b") == {"rastro"}


# ---------------------------------------------------------------- the taker's LLM reader hook


class FakeInterpreter:
    def __init__(self, results):  # type: ignore[no-untyped-def]
        self.results, self.offers = list(results), []

    def offer(self, events, known, tick, tick_seconds):  # type: ignore[no-untyped-def]
        self.offers.append(([e["id"] for e in events], dict(known), tick))
        return self.results.pop(0) if self.results else []


def test_the_learner_hands_new_texts_to_the_llm_reader_and_keeps_what_comes_back():
    llm = Learning(
        subject_kind="dealer",
        subject="abuela",
        kind="behaviour",
        tick=170,
        confidence=0.7,
        text="Abuela likes kindness.",
        source="llm",
        evidence=(20001,),
    )
    lines: list[str] = []
    reader = FakeInterpreter([[llm]])
    store = LearningStore()
    learner = LiveLearner(store, lines.append, reader)
    known = {"abuela": "dealer", "v03": "venue"}
    learner.blocks(FEED, US, Clock(tick=180, t_hours=4.2), known)  # type: ignore[arg-type]
    learner.blocks(FEED, US, Clock(tick=181, t_hours=4.21), known)  # type: ignore[arg-type]
    first_ids, seen_known, _ = reader.offers[0]
    assert len(first_ids) == len([e for e in FEED if "id" in e]) and seen_known == known
    assert reader.offers[1][0] == []  # each event goes to the reader once
    assert lines == ["learnings: the LLM reader added 1 (behaviour); none of them can block a dealer"]
    learner.flush()
    assert store.recall("abuela", {"behaviour"})[0].source == "llm"


def test_the_cli_llm_pass_keeps_validated_learnings(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from bazaar_agent import cli
    from bazaar_agent.learn import cli as learn_cli
    from bazaar_agent.learn import interpret as interpret_module
    from bazaar_agent.llm import cli as llm_cli

    feed_dir = tmp_path / "feed"
    feed_dir.mkdir()
    (feed_dir / "feed.jsonl").write_text("\n".join(json.dumps(e) for e in FEED if "id" in e) + "\n")
    monkeypatch.setenv("BAZAAR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BAZAAR_TEAM_ID", "t01")
    monkeypatch.setattr(learn_cli, "_connect", lambda: None)
    monkeypatch.setattr(llm_cli, "runtime_for", lambda *a: object())
    seen: list[int] = []

    def fake_interpret(batch, runtime, known, tick, tick_seconds):  # type: ignore[no-untyped-def]
        seen.append(len(batch))
        draft = interpret_module.Draft(
            learnings=[
                interpret_module.DraftLearning(
                    event_id=batch[0].event_id,
                    subject="organiser",
                    kind="rule_change",
                    until_tick=None,
                    confidence=0.9,
                    text="The market closes at 23:00.",
                )
            ]
        )
        return interpret_module.validate(draft, batch, known, "fake")

    monkeypatch.setattr(interpret_module, "interpret", fake_interpret)
    result = CliRunner().invoke(cli.app, ["learnings", "--llm", "3", "--all", "--json", "--subject", "organiser"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output[result.output.index("{") :])
    # the newest 3 texts: two organiser notices and one venue notice, read in two calls (never mixed);
    # the venue's text may not teach about the organiser, so only the organiser call's learning is kept
    assert sorted(seen) == [1, 2] and [lr["source"] for lr in out["learnings"]].count("llm") == 1
