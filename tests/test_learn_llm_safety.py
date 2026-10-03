"""PR #111 review fixes: LLM readings keep their own rows, never crowd blockers, and the reader is opt-in."""

import pytest

from bazaar_agent.agents.market import venues_from
from bazaar_agent.learn.live import LiveLearner
from bazaar_agent.learn.model import Learning
from bazaar_agent.learn.reader import FeedReader, from_refusal
from bazaar_agent.learn.store import LearningStore
from bazaar_agent.learn.venues import adjust
from bazaar_agent.ticks import Clock
from tests.agent_fakes import CHEAP, RASTRO
from tests.test_db import database_url, open_in, schema  # noqa: F401  (fixtures)
from tests.test_learn_reader import FEED, HOUR, US
from tests.test_learn_venues import fee_notice


def llm_twin(rules: Learning, text: str = "Organisers: from T300 every accept costs 50 P") -> Learning:
    return rules.model_copy(update={"source": "llm", "text": text, "confidence": 0.7})


def notice() -> Learning:
    return next(lr for lr in FeedReader(US).read(FEED, HOUR) if lr.evidence == (20009,))


def test_an_llm_reading_never_shares_a_rules_row():
    rules = notice()
    twin = llm_twin(rules)
    assert rules.key() != twin.key()
    store = LearningStore()
    store.record([rules, twin])
    assert {lr.source for lr in store.recall("organiser", {"announcement"})} == {"rules", "llm"}


@pytest.mark.integration
def test_the_upsert_never_rewrites_a_rules_row_with_llm_text(database_url, schema):  # noqa: F811
    from bazaar_agent import db

    store = LearningStore(lambda: open_in(database_url, schema), init_schema=db.init_schema)
    rules = notice()
    store.record([rules])
    forged = llm_twin(rules).model_copy(update={"source": "rules"})  # same key as the rules row...
    store.record([forged.model_copy(update={"source": "llm"})])  # ...but stored as llm: its own row
    with open_in(database_url, schema) as conn:
        rows = conn.execute("select source, claim from learnings order by source").fetchall()
    assert rows == [("llm", "Organisers: from T300 every accept costs 50 P"), ("rules", rules.text)]
    store.close()


def test_llm_rows_never_crowd_a_rules_blocker_out_of_the_recall_window():
    store = LearningStore()
    cooloff = from_refusal("abuela", "cooloff", "", {"until_tick": 300}, US, HOUR, None)
    flood = [
        Learning(subject_kind="dealer", subject="abuela", kind="announcement", tick=HOUR.tick + i, confidence=0.7,
                 text=f"spam {i}", source="llm", evidence=(10_000 + i,))
        for i in range(600)
    ]  # fmt: skip
    store.record([cooloff, *flood])
    learner = LiveLearner(store)
    assert learner.blocks([], US, Clock(tick=HOUR.tick + 50, t_hours=4.2)).stops("abuela") is not None


def test_the_llm_pass_is_off_unless_runtime_md_opts_in(monkeypatch):
    from bazaar_agent import cli
    from bazaar_agent.guardrails import Guardrails
    from bazaar_agent.llm import cli as llm_cli
    from bazaar_agent.llm import config as llm_config

    lines: list[str] = []
    asked: list[str] = []
    monkeypatch.setattr(llm_cli, "runtime_for", lambda *a: asked.append("runtime") or object())
    assert cli._feed_interpreter(object(), Guardrails(), lines.append) is None
    assert asked == [] and lines == ["feed reader: LLM pass off (RUNTIME.md llm_read_feed = false); structure only"]
    loaded = llm_config.load_runtime()
    opted = loaded.__class__(**{**loaded.__dict__, "config": loaded.config.model_copy(update={"llm_read_feed": True})})
    monkeypatch.setattr(llm_config, "load_runtime", lambda: opted)
    reader = cli._feed_interpreter(object(), Guardrails(), lines.append)
    assert reader is not None and reader.every_ticks == 10 and asked == ["runtime"]


def test_no_credential_means_no_runtime(monkeypatch):
    from bazaar_agent.config import Settings
    from bazaar_agent.guardrails import Guardrails
    from bazaar_agent.llm import cli as llm_cli

    bare = Settings.model_validate(
        {"simulated": True, "bazaar_url": "http://127.0.0.1:8765", "bazaar_key": "sim-team1"}
    )
    assert llm_cli.runtime_for(bare, Guardrails(), "feed reader") is None


def test_every_pending_fee_raise_counts():
    venues = venues_from({"venues": [RASTRO, CHEAP]})
    notices = [fee_notice("v02", 900, 120, tick=90), fee_notice("v02", 300, 130, tick=95)]
    (cheap,) = [v for v in adjust(venues, notices, 100, 40) if v.id == "v02"]
    assert cheap.fee_bps == 900  # the later, lower notice does not hide the pending 9 %
