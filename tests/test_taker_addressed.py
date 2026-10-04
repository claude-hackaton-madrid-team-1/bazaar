"""Offers another team addressed to us (Sun 4 Oct): read from `/api/me/offers` (already in the snapshot), decided
through the same path as a board offer, each one leaving a decision row. A keyless board never shows them."""

from bazaar_agent.agents.market import addressed_to_us
from bazaar_agent.agents.taker import Taker, TakerConfig, addressed_mode
from bazaar_agent.sdk import BazaarError
from tests.agent_fakes import TICK, FakePublic, FakeTeam, ask, bid, clock, parts, rows
from tests.test_strategy import PARAMS


class Team(FakeTeam):
    """FakeTeam whose accept records the copies handed over, and may refuse like the server."""

    def __init__(self, *a, refuse=None, **kw):
        super().__init__(*a, **kw)
        self.refuse = refuse

    def accept(self, offer_id, assets=None):
        self.sent.append(("accept", offer_id, assets))
        if self.refuse is not None:
            raise self.refuse
        return {"ok": True}


def to_us(offer, maker="t05"):
    return {**offer, "maker": maker, "to": "t01"}


def taker(tmp_path, team, *, live=True, mode="asks", accept_bids=False, boards=None, params=None, **rules):
    lines: list[str] = []
    kw = parts(tmp_path, **rules)
    if params is not None:
        kw["params"] = lambda tick: params
    t = Taker(
        team,
        FakePublic(boards=boards or {}),  # the keyless board: an addressed offer is never on it
        live=live,
        log=lines.append,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=0, accept_bids=accept_bids, addressed=mode),
        **kw,
    )
    return t, lines


def addressed_rows(root):
    return [r for r in rows(root) if r.get("kind") == "addressed_offer"]


# ---------------------------------------------------------------- reading them


def test_only_open_plain_offers_another_team_addressed_to_us_are_read():
    ours = {**ask(1, "LAV-02", 10), "maker": "t01", "to": "t05"}  # ours, addressed to t05
    theirs = to_us(ask(2, "LAV-02", 10))
    in_thread = {**to_us(ask(3, "LAV-02", 10)), "thread": 77}  # the team desk's
    elsewhere = {**ask(4, "LAV-02", 10), "to": "t09"}
    public = ask(5, "LAV-02", 10)
    gone = {**to_us(ask(6, "LAV-02", 10)), "status": "accepted"}
    body = {"offers": [ours, theirs, in_thread, elsewhere, public, gone]}
    assert [o["id"] for o in addressed_to_us(body, "t01")] == [2]
    assert addressed_to_us(body, "") == []


def test_the_mode_reads_safely_and_an_unknown_value_turns_it_off():
    assert addressed_mode(None) == ("asks", None) and addressed_mode(" ALL ") == ("all", None)
    assert addressed_mode("off") == ("off", None)
    mode, note = addressed_mode("yes")
    assert mode == "off" and note is not None and "BAZAAR_ADDRESSED_OFFERS" in note


# ---------------------------------------------------------------- asks addressed to us


def test_a_good_ask_addressed_to_us_is_accepted_with_no_extra_request(tmp_path):
    # LAV-02 is worth 20.8 to us: 10 + fee 2 = 12 leaves 8.8 > min_buy_surplus 2, the same test as a board ask.
    team = Team(offers=[to_us(ask(31, "LAV-02", 10))])
    t, _ = taker(tmp_path, team)
    t.on_tick(clock())
    assert team.sent == [("accept", 31, None)]
    assert team.reads.count("my_offers") == 1  # the snapshot's read, nothing more
    (row,) = [r for r in rows(tmp_path) if r.get("kind") == "accept_ask" and r.get("chosen")]
    assert row["inputs"]["addressed_to_us"] is True and row["inputs"]["offer_id"] == 31
    assert addressed_rows(tmp_path) == []  # the accept row is its decision


def test_off_leaves_them_unread_as_before(tmp_path):
    team = Team(offers=[to_us(ask(31, "LAV-02", 10))])
    t, _ = taker(tmp_path, team, mode="off")
    t.on_tick(clock())
    assert team.sent == [] and addressed_rows(tmp_path) == []


def test_a_bad_ask_addressed_to_us_is_rejected_with_its_reason_once(tmp_path):
    # 17 + fee 2 = 19 leaves 1.8 < min_buy_surplus 2; LAV-01 we already hold.
    team = Team(offers=[to_us(ask(31, "LAV-02", 17)), to_us(ask(32, "LAV-01", 2, asset=905))])
    t, _ = taker(tmp_path, team)
    t.on_tick(clock())
    assert team.sent == []
    by_id = {r["inputs"]["offer_id"]: r for r in addressed_rows(tmp_path)}
    assert set(by_id) == {31, 32}
    assert "min_buy_surplus 2" in by_id[31]["reason"] and by_id[31]["status"] == "rejected"
    assert by_id[32]["reason"] == "we already hold LAV-01"
    assert by_id[31]["inputs"]["maker"] == "t05" and by_id[31]["chosen"] is False
    team.now = clock(tick=TICK + 1)
    t.on_tick(clock(tick=TICK + 1))
    assert len(addressed_rows(tmp_path)) == 2  # the same reason is not recorded every tick


def test_an_offer_we_cannot_price_or_take_is_recorded_not_guessed(tmp_path):
    swap = {**to_us(ask(33, "LAV-02", 10)), "want": {"cash": 0, "assets": [], "types": ["card:LAV-01"]}}
    closed = to_us(ask(34, "LAV-02", 10, venue="v77"))
    t, _ = taker(tmp_path, team := Team(offers=[swap, closed]))
    t.on_tick(clock())
    assert team.sent == []
    by_id = {r["inputs"]["offer_id"]: r["reason"] for r in addressed_rows(tmp_path)}
    assert by_id[33].startswith("not a plain one-card offer") and "venue v77" in by_id[34]


def test_a_duel_holding_the_accept_defers_an_addressed_ask_to_the_next_tick(tmp_path):
    team = Team(offers=[to_us(ask(31, "LAV-02", 10))])
    t, lines = taker(tmp_path, team, max_accepts_per_tick=2)
    assert t.ledger.reserve_accept(TICK, 1.5, 0, "duel:7", 2)  # the duel player went first
    t.on_tick(clock(accepts_per_team_per_tick=2))
    assert team.sent == []
    (row,) = [r for r in rows(tmp_path) if r.get("kind") == "accept_ask"]
    assert any("a duel holds the team's accept this tick (duels first)" in line for line in lines)
    assert row["inputs"]["addressed_to_us"] is True and row["status"] == "rejected"
    team.now = clock(tick=TICK + 1)
    t.on_tick(clock(tick=TICK + 1, accepts_per_team_per_tick=2))
    assert team.sent == [("accept", 31, None)]


def test_dry_run_sends_nothing_and_records_the_would_accept(tmp_path):
    team = Team(offers=[to_us(ask(31, "LAV-02", 10))])
    t, lines = taker(tmp_path, team, live=False)
    t.on_tick(clock())
    assert team.sent == []
    (row,) = [r for r in rows(tmp_path) if r.get("kind") == "accept_ask" and r.get("chosen")]
    assert (row["status"], row["dry_run"], row["inputs"]["addressed_to_us"]) == ("approved", True, True)
    assert any("WOULD accept LAV-02" in line for line in lines)


# ---------------------------------------------------------------- bids addressed to us


def test_bids_addressed_to_us_wait_for_all_and_public_bids_stay_behind_accept_bids(tmp_path):
    rich = to_us(bid(77, "LAT-09", 70), maker="t02")  # 70 - fee 5 - our loss 45 = +20
    public = bid(78, "LAT-09", 72, maker="m9")
    asks_only = Team(offers=[rich])
    taker(tmp_path / "asks", asks_only, boards={"rastro": [public]})[0].on_tick(clock())
    assert asks_only.sent == []
    (row,) = addressed_rows(tmp_path / "asks")
    assert "BAZAAR_ADDRESSED_OFFERS=asks" in row["reason"] and row["inputs"]["side"] == "bid"

    every = Team(offers=[rich])
    t, lines = taker(tmp_path / "all", every, mode="all", boards={"rastro": [public]})
    t.on_tick(clock())
    assert every.sent == [("accept", 77, [5])]  # the addressed bid; the richer public one needs accept_bids
    (row,) = [r for r in rows(tmp_path / "all") if r.get("kind") == "accept_bid" and r.get("chosen")]
    assert row["inputs"]["addressed_to_us"] is True
    assert t.ledger.accept_items(TICK) == ["sell:5"] and t.ledger.spent_since(0) == 0


def test_a_thin_addressed_bid_is_rejected_with_its_reason(tmp_path):
    team = Team(offers=[to_us(bid(79, "LAT-09", 50), maker="t02")])  # 50 - 4 - 45 = +1 < sell_min_surplus 5
    taker(tmp_path, team, mode="all")[0].on_tick(clock())
    assert team.sent == []
    (row,) = addressed_rows(tmp_path)
    assert "sell_min_surplus 5" in row["reason"] and row["inputs"]["offer_id"] == 79


def test_our_private_value_floor_holds_even_when_the_surplus_bar_is_off(tmp_path):
    # With sell_min_surplus pushed to -100 the bid reaches the guardrails, which still refuse a net under
    # sell_min_value_ratio 1.0 × your_value 35: nothing is sent and the skip row says why.
    team = Team(offers=[to_us(bid(80, "LAT-09", 30), maker="t02")])
    t, _ = taker(tmp_path, team, mode="all", params=PARAMS.model_copy(update={"sell_min_surplus": -100}))
    t.on_tick(clock())
    assert team.sent == [] and t.ledger.accept_items(TICK) == []
    (row,) = [r for r in rows(tmp_path) if r.get("kind") == "accept_bid"]
    assert row["chosen"] is False and "your_value 35" in row["guardrail"]


def test_a_refused_sell_into_an_expired_bid_gives_the_teams_accept_back(tmp_path):
    # The bid expired (or another team took it) between our read and our accept: a 4xx costs nothing, so the
    # team's accept of this tick is free again for the next candidate or a duel.
    gone = BazaarError("offer_not_open", "offer 77 is not open", 400)
    team = Team(offers=[to_us(bid(77, "LAT-09", 70), maker="t02")], refuse=gone)
    t, _ = taker(tmp_path, team, mode="all")
    t.on_tick(clock())
    assert team.sent == [("accept", 77, [5])]
    assert t.ledger.accepts_in_tick(TICK) == 0
