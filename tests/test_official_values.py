"""The official value cap (Day-2 hint 1): `official_values.OfficialValues` and its rule in `guardrails.check()`."""

import math

import pytest

from bazaar_agent import guardrails as gr
from bazaar_agent.official_values import OfficialValues
from bazaar_agent.sdk import BazaarError

pytestmark = pytest.mark.official_values

RULES = gr.Guardrails(cash_floor=0, venue_bond_reserve=0, max_spend_per_game_hour=1000)


class Reader:
    """A fake `Bazaar.value(card)`: answers from a table, records every call."""

    def __init__(self, values: dict[str, object], fail: Exception | None = None) -> None:
        self.values, self.fail, self.calls = values, fail, []

    def __call__(self, card: str) -> object:
        self.calls.append(card)
        if self.fail is not None:
            raise self.fail
        return {"card": card, "your_value": self.values[card]}


def ctx(values: OfficialValues | None, held: dict[str, int] | None = None, tick: int = 7) -> gr.Context:
    return gr.Context(cash=400, held=held or {}, tick=tick, t_hours=1.0, stops=(), values=values)


def test_a_buy_above_the_official_value_is_refused_even_when_our_model_says_more():
    # MAL-06, measured live on Saturday: official 27.5, our model 36.0. A bid of 30 sat between the two.
    values = OfficialValues(Reader({"MAL-06": 27.5}))
    verdict = gr.check(gr.Action("bid", "MAL-06", "uncommon", 26), ctx(values), RULES)
    assert verdict.allowed
    verdict = gr.check(gr.Action("accept_buy", "MAL-06", "rare", 30), ctx(values), RULES)
    assert not verdict.allowed
    assert verdict.violations == ("price 30 > official value 27.5 of MAL-06 (GET /api/me/value)",)


def test_a_price_equal_to_the_official_value_is_allowed_and_the_margin_lowers_the_cap():
    values = OfficialValues(Reader({"SAL-07": 32.5}))
    assert gr.check(gr.Action("buy", "SAL-07", "rare", 32), ctx(values), RULES).allowed
    margin = RULES.model_copy(update={"official_value_margin": 3.0})
    verdict = gr.check(gr.Action("buy", "SAL-07", "rare", 30), ctx(values), margin)
    assert not verdict.allowed and "official_value_margin 3" in str(verdict)


def test_the_copy_a_swap_gives_counts_against_the_official_value():
    values = OfficialValues(Reader({"LAV-02": 10.0}))
    assert gr.check(gr.Action("bid", "LAV-02", "common", 0, gives_value=9.5), ctx(values), RULES).allowed
    verdict = gr.check(gr.Action("bid", "LAV-02", "common", 2, gives_value=9.5), ctx(values), RULES)
    assert "price 2 + copy given 9.5 > official value 10" in str(verdict)


def test_no_value_book_refuses_every_card_buy_but_never_a_pack_or_a_sell():
    verdict = gr.check(gr.Action("buy", "LAV-03", "common", 5), ctx(None), RULES)
    assert not verdict.allowed and "official value of LAV-03 not read" in str(verdict)
    assert gr.check(gr.Action("buy", "sobre_barrio", "pack", 17), ctx(None), RULES).allowed
    assert gr.check(gr.Action("sell", "LAV-03", "common", 50, your_value=4.0), ctx(None), RULES).allowed


def test_sells_and_packs_never_read_an_official_value():
    reader = Reader({})
    values = OfficialValues(reader)
    gr.check(gr.Action("sell", "LAV-03", "common", 50, your_value=4.0), ctx(values, {"LAV-03": 1}), RULES)
    gr.check(gr.Action("accept_sell", "LAV-03", "common", 50, your_value=4.0), ctx(values, {"LAV-03": 1}), RULES)
    gr.check(gr.Action("buy", "sobre_barrio", "pack", 17), ctx(values), RULES)
    assert reader.calls == [] and values.reads == 0


def test_a_buy_another_rule_refuses_is_never_read_and_neither_is_a_held_card_or_a_halt():
    reader = Reader({"LAV-09": 200.0})
    values = OfficialValues(reader)
    assert not gr.check(gr.Action("buy", "LAV-09", "rare", 95), ctx(values), RULES).allowed  # max_price_rare 80
    assert not gr.check(gr.Action("buy", "LAV-09", "rare", 50), ctx(values, {"LAV-09": 1}), RULES).allowed
    halted = gr.Context(cash=400, held={}, tick=7, t_hours=1.0, stops=("pause file",), values=values)
    assert gr.check(gr.Action("buy", "LAV-09", "rare", 50), halted, RULES).halted
    assert reader.calls == []


def test_a_ranking_check_never_reads_the_official_value():
    reader = Reader({"LAV-09": 1.0})
    ranked = gr.Context(cash=400, held={}, tick=7, t_hours=1.0, stops=(), values=OfficialValues(reader), ranking=True)
    assert gr.check(gr.Action("buy", "LAV-09", "rare", 50), ranked, RULES).allowed
    assert reader.calls == []


def test_a_card_is_read_once_per_tick_and_again_on_a_new_tick_or_after_our_copies_change():
    reader = Reader({"LAV-08": 15.0, "LAV-07": 12.0})
    values = OfficialValues(reader)
    for _ in range(3):
        gr.check(gr.Action("bid", "LAV-08", "uncommon", 14), ctx(values, tick=7), RULES)
    gr.check(gr.Action("bid", "LAV-07", "uncommon", 11), ctx(values, tick=7), RULES)
    assert reader.calls == ["LAV-08", "LAV-07"] and values.reads == 2
    gr.check(gr.Action("bid", "LAV-08", "uncommon", 14), ctx(values, tick=8), RULES)
    assert reader.calls == ["LAV-08", "LAV-07", "LAV-08"]
    assert values.value("LAV-08", 8, 1) == 15.0  # one more copy held (a deal settled): read again
    assert values.reads == 4


@pytest.mark.parametrize(
    "fail",
    [BazaarError("network", "connection reset"), BazaarError("rate_limited", "429", 429), KeyError("value")],
    ids=["network", "429", "anything-else"],
)
def test_a_failed_read_refuses_the_buy_and_is_not_retried_in_the_same_tick(fail):
    reader = Reader({}, fail=fail)
    values = OfficialValues(reader)
    for _ in range(2):
        verdict = gr.check(gr.Action("buy", "LAV-03", "common", 5), ctx(values), RULES)
        assert not verdict.allowed and "could not be read" in str(verdict)
    assert reader.calls == ["LAV-03"] and values.failures == 1


@pytest.mark.parametrize(
    "answer",
    [
        {"card": "LAV-03", "your_value": math.nan},
        {"card": "LAV-03", "your_value": math.inf},
        {"card": "LAV-03", "your_value": -1},
        {"card": "LAV-03", "your_value": True},
        {"card": "LAV-03", "your_value": "12"},
        {"card": "LAV-04", "your_value": 12.0},
        {"your_value": 12.0},
        ["LAV-03", 12.0],
        None,
    ],
    ids=["nan", "inf", "negative", "bool", "string", "other-card", "no-card", "list", "none"],
)
def test_a_malformed_answer_refuses_the_buy(answer):
    values = OfficialValues(lambda card: answer)
    assert values.value("LAV-03", 7, 0) is None and values.failures == 1


def test_the_client_is_looked_up_at_read_time_so_a_client_without_value_fails_closed():
    class NoValue:
        pass

    values = OfficialValues.of(NoValue())
    assert values.value("LAV-03", 7, 0) is None


def test_the_rule_is_loaded_from_guardrails_md_and_documented():
    loaded = gr.load_guardrails()
    assert loaded.rules.official_value_margin == 0
    assert "official_value_margin" in {line.rule_id for line in loaded.lines}
    assert "official_value_margin" in gr.ENFORCED_BY


@pytest.mark.parametrize(
    "fail",
    [
        BazaarError("server_error", "503", 503),
        BazaarError("rate_limited", "429", 429),
        BazaarError("network", "timed out"),
        RuntimeError("boom"),
    ],
    ids=["503", "429", "network", "anything-else"],
)
def test_a_value_route_down_refuses_every_later_card_of_the_tick_without_another_read(fail):
    reader = Reader({"LAV-08": 15.0}, fail=fail)
    values = OfficialValues(reader)
    assert not gr.check(gr.Action("bid", "LAV-07", "uncommon", 11), ctx(values, tick=7), RULES).allowed
    verdict = gr.check(gr.Action("bid", "LAV-08", "uncommon", 11), ctx(values, tick=7), RULES)
    assert not verdict.allowed and "could not be read" in str(verdict)
    assert reader.calls == ["LAV-07"] and values.reads == 1 and values.failures == 2
    reader.fail = None  # the next tick reads again
    assert gr.check(gr.Action("bid", "LAV-08", "uncommon", 11), ctx(values, tick=8), RULES).allowed
    assert reader.calls == ["LAV-07", "LAV-08"]


def test_a_card_specific_refusal_does_not_stop_the_other_cards_of_the_tick():
    reader = Reader({"LAV-08": 15.0}, fail=BazaarError("unknown_card", "LAV-99", 422))
    values = OfficialValues(reader)
    assert values.value("LAV-99", 7, 0) is None
    reader.fail = None
    assert values.value("LAV-08", 7, 0) == 15.0 and reader.calls == ["LAV-99", "LAV-08"]


def test_the_team_client_reads_a_value_once_with_no_retry(monkeypatch):
    from bazaar_agent import sdk

    calls = []

    def once(self, method, path, body=None, query=None):
        calls.append((method, path, query))
        raise sdk.BazaarError("network", "timed out")

    monkeypatch.setattr(sdk.TrackedBazaar, "_call", once)
    client = sdk.TeamBazaar("http://127.0.0.1:9", "sim-team1")
    with pytest.raises(sdk.BazaarError):
        client.value("LAV-03")
    assert calls == [("GET", "/api/me/value", {"card": "LAV-03"})]


def test_raising_the_card_caps_never_lifts_the_official_value_cap():
    """Sun 4 Oct: max_price_uncommon 30 and max_price_rare 105 are only ceilings; the server's value still binds."""
    loaded = gr.load_guardrails().rules
    assert (loaded.max_price_uncommon, loaded.max_price_rare) == (30, 105)
    rules = loaded.model_copy(update={"cash_floor": 0, "venue_bond_reserve": 0, "max_spend_per_game_hour": 1000})
    values = OfficialValues(Reader({"MAL-06": 27.5, "LAV-09": 90.0}))
    ask = gr.check(gr.Action("accept_buy", "MAL-06", "uncommon", 30), ctx(values), rules)
    assert not ask.allowed and ask.violations == ("price 30 > official value 27.5 of MAL-06 (GET /api/me/value)",)
    rare = gr.check(gr.Action("accept_buy", "LAV-09", "rare", 105), ctx(values), rules)
    assert not rare.allowed and "official value 90 of LAV-09" in str(rare)
    assert gr.check(gr.Action("accept_buy", "LAV-09", "rare", 90), ctx(values), rules).allowed
