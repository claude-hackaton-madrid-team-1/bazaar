"""The Market Test bench (`bazaar_sim.bench`): presets, traders, one venue's session, the stall and the oracle,
and the simulator's venues running it tick by tick."""

from __future__ import annotations

import itertools
import random
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from bazaar_sim import bench, broker
from bazaar_sim.bench import HARD, NORMAL, STATIC, BenchSession, simulate, stall_policy
from bazaar_sim.errors import SimError
from bazaar_sim.models import BenchTrader
from bazaar_sim.world import SimConfig, World
from tests.simkit import manual_world

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor" / "bazaar-kit"))
import starter_broker  # noqa: E402  (the kit's own broker, unchanged)

US = "t01"


def seller(id_: str = "s", limit: int = 30, quote: int = 36, **kw: object) -> BenchTrader:
    return BenchTrader(id=id_, side="sell", limit=limit, quote=quote, **kw)  # type: ignore[arg-type]


def buyer(id_: str = "b", limit: int = 60, quote: int = 50, **kw: object) -> BenchTrader:
    return BenchTrader(id=id_, side="buy", limit=limit, quote=quote, **kw)  # type: ignore[arg-type]


# ---------------------------------------------------------------- presets and traders


def test_the_presets_carry_the_numbers_of_the_schedule_and_issue_12():
    assert (NORMAL.traders, NORMAL.ticks, NORMAL.firm_share, NORMAL.impatient_share) == (10, 16, 0.2, 0.25)
    assert (HARD.traders, HARD.ticks, HARD.firm_share, HARD.impatient_share) == (12, 16, 0.35, 0.35)
    assert NORMAL.impatient_life == (1, 2) and NORMAL.patient_life == (3, 6)
    assert NORMAL.spread == 10  # a patient trader arriving last still leaves inside the 16 ticks
    assert broker.BENCH_TRADERS == 10
    with pytest.raises(ValueError):
        bench.preset("easy")


def test_traders_are_seeded_shaded_and_drawn_in_the_preset_shares():
    assert bench.make_book(NORMAL, 3) == bench.make_book(NORMAL, 3)
    assert bench.make_book(NORMAL, 3) != bench.make_book(NORMAL, 4)
    traders = [t for seed in range(400) for t in bench.make_book(HARD, seed)]
    for t in traders:
        assert t.quote >= t.limit if t.side == "sell" else t.quote <= t.limit
        assert 0 <= t.arrive <= HARD.spread and (1 <= t.life <= 2 or 3 <= t.life <= 6)
        assert t.firm or 0.5 <= t.relax <= 1.0
    firm = sum(t.firm for t in traders) / len(traders)
    impatient = sum(t.life <= 2 for t in traders) / len(traders)
    assert abs(firm - 0.35) < 0.03 and abs(impatient - 0.35) < 0.03
    assert sum(t.side == "sell" for t in traders) == len(traders) // 2


def test_two_presets_with_one_seed_share_limits_and_quotes():
    normal, hard = bench.make_book(NORMAL, 9), bench.make_book(HARD, 9)
    assert [(t.limit, t.quote) for t in normal] == [(t.limit, t.quote) for t in hard[:10]]


def test_the_static_preset_is_the_first_simulators_book_draw_for_draw_and_the_default(monkeypatch):
    monkeypatch.delenv("SIM_BENCH_PRESET", raising=False)
    for seed in range(50):
        rng, old = random.Random(seed), []
        for k in range(10):  # the generator #55 shipped
            if k % 2 == 0:
                cost = rng.randint(20, 60)
                old.append(
                    BenchTrader(id=f"b1-{k}", side="sell", limit=cost, quote=round(cost * rng.uniform(1.05, 1.3)))
                )
            else:
                value = rng.randint(40, 95)
                old.append(
                    BenchTrader(id=f"b1-{k}", side="buy", limit=value, quote=round(value * rng.uniform(0.75, 0.95)))
                )
        assert bench.make_book(STATIC, seed) == old
    for t in bench.make_book(STATIC, 1):
        assert t.arrive == 0 and t.firm and bench.present(t, 15) and bench.quote_at(t, 15) == t.quote
    assert SimConfig().bench_preset == "static" and SimConfig.from_env().bench_preset == "static"


def test_a_quote_relaxes_toward_the_limit_with_age_and_a_firm_one_never_moves():
    t = seller(limit=30, quote=40, arrive=2, life=5, relax=1.0)
    assert [bench.quote_at(t, k) for k in range(2, 7)] == [40, 38, 35, 32, 30]  # the limit on its last tick
    half = buyer(limit=60, quote=40, arrive=0, life=3, relax=0.5)
    assert [bench.quote_at(half, k) for k in range(3)] == [40, 45, 50]  # never past half the shade
    firm = seller(limit=30, quote=40, arrive=0, life=6)
    assert {bench.quote_at(firm, k) for k in range(6)} == {40}
    assert [bench.present(t, k) for k in range(8)] == [False, False, True, True, True, True, True, False]


def test_a_bench_offer_has_both_sides_in_full_and_hides_the_limit():
    sell = bench.offer(seller(quote=36), 0, 4)
    buy = bench.offer(buyer(quote=50), 0, 4)
    assert sell["want"] == {"cash": 36, "assets": [], "types": []} and sell["give"]["cash"] == 0
    assert buy["give"] == {"cash": 50, "assets": [], "types": []} and buy["want"]["types"] == ["card:BENCH"]
    assert "limit" not in sell and "life" not in buy


# ---------------------------------------------------------------- one venue's session


def test_the_quote_rule_refuses_a_pair_that_crosses_only_at_the_limits():
    session = BenchSession([seller(quote=55), buyer(quote=50)], rule="quote")
    with pytest.raises(SimError):
        session.match("s", "b", 52)
    assert session.refused == {"quote": 1} and session.realised() == 0


def test_the_limit_rule_accepts_inside_the_hidden_limits_and_refuses_past_them():
    session = BenchSession([seller(quote=55), buyer(quote=50)], rule="limit", fee_bps=1000)
    with pytest.raises(SimError):
        session.match("s", "b", 56)  # 56 + 6 fee > the buyer's limit 60
    assert session.match("s", "b", 52)["fee"] == 5
    assert session.realised() == 30 and session.refused == {"limit": 1}
    with pytest.raises(SimError):
        session.match("s", "b", 52)  # matched already
    assert session.refused["gone"] == 1


def test_a_trader_who_left_or_has_not_arrived_cannot_be_matched():
    session = BenchSession([seller(arrive=0, life=1), buyer(arrive=1, life=2)], rule="limit")
    with pytest.raises(SimError):
        session.match("s", "b", 40)
    session.advance()
    assert [o["id"] for o in session.book()["bench_offers"]] == ["b"]
    with pytest.raises(SimError):
        session.match("s", "b", 40)
    assert session.refused == {"gone": 2}
    with pytest.raises(ValueError):
        BenchSession([], rule="midpoint")


def test_the_book_tick_is_the_game_tick_as_the_simulators_venues_show_it():
    ticks = []
    simulate(lambda book: ticks.append(book["tick"]) or [], NORMAL, 2, start_tick=31)
    assert ticks == list(range(31, 47))


def test_the_bench_command_prints_the_stall_and_the_oracle_and_refuses_bad_options():
    from typer.testing import CliRunner

    from bazaar_sim.cli import app

    runner = CliRunner()
    ok = runner.invoke(app, ["bench", "--seeds", "20", "--presets", "hard", "--rules", "limit", "--relax", "0.2,0.6"])
    assert ok.exit_code == 0 and "hard     limit" in ok.output
    for bad in (["--relax", "0.5"], ["--relax", "0.5,1.5"], ["--seeds", "0"], ["--rules", "mid"]):
        assert runner.invoke(app, ["bench", "--seeds", "5", *bad]).exit_code != 0


def test_simulate_counts_every_read_and_post_against_the_rate_budget():
    sent = []

    def spammer(book):
        sent.append(book["tick"])
        return [("nobody", "nothing", 10)]

    r = simulate(spammer, NORMAL, 1, reads_per_tick=3)
    assert r.reads == 3 * NORMAL.ticks and r.posts == 3 * NORMAL.ticks and r.max_requests_per_tick == 6
    assert r.refused == {"gone": 48} and r.realised == 0 and sent[:4] == [0, 0, 0, 1]


# ---------------------------------------------------------------- the stall and the oracle


def test_the_stall_policy_through_a_session_realises_what_the_stall_replica_does():
    for seed, p in itertools.product(range(150), (NORMAL, HARD)):
        r = simulate(stall_policy, p, seed)
        assert r.realised == r.stall_realised and r.refused == {}


def test_the_stall_crosses_the_pairs_the_kits_starter_broker_plans():
    for seed in range(150):
        session = BenchSession(bench.make_book(HARD, seed), ticks=16)
        while not session.done:
            book = session.book()
            planned = [(s, b) for s, b, _ in starter_broker.bench_plan(book)]
            ours = [(s, b) for s, b, _ in stall_policy(book)]
            assert ours == planned
            for s, b, price in stall_policy(book):
                session.match(s, b, price)
            session.advance()


def test_the_oracle_bounds_every_policy_and_its_schedule_is_feasible():
    for seed, p, rule in itertools.product(range(100), (NORMAL, HARD), bench.MATCH_RULES):
        traders = bench.make_book(p, seed)
        r = simulate(stall_policy, p, seed, rule=rule, traders=traders)
        assert r.stall_realised <= r.oracle_realised <= r.possible
        session = BenchSession(traders, ticks=p.ticks, rule=rule)
        schedule = bench.oracle_schedule(traders, p.ticks, rule)
        while not session.done:
            for s, b, price, k in schedule:
                if k == session.tick:
                    session.match(s, b, price)
            session.advance()
        assert session.realised() == r.oracle_realised and not session.refused


def test_the_oracle_under_the_limit_rule_realises_the_whole_static_book():
    for seed in range(100):
        r = simulate(stall_policy, STATIC, seed, rule="limit")
        assert r.oracle_realised == r.possible


def _brute(w: list[list[int]], row: int = 0, used: frozenset[int] = frozenset()) -> int:
    if row == len(w):
        return 0
    skip = _brute(w, row + 1, used)
    take = [w[row][c] + _brute(w, row + 1, used | {c}) for c in range(len(w[0])) if c not in used and w[row][c]]
    return max([skip, *take])


def test_best_matching_is_exact_against_brute_force():
    rng = random.Random(5)
    for _ in range(300):
        rows, cols = rng.randint(1, 6), rng.randint(1, 6)
        w = [[rng.choice([0, 0, rng.randint(1, 40)]) for _ in range(cols)] for _ in range(rows)]
        total, pairs = bench.best_matching(w)
        assert total == sum(w[r][c] for r, c in pairs) == _brute(w)
        assert len({r for r, _ in pairs}) == len(pairs) == len({c for _, c in pairs})


def test_bench_points_give_half_at_the_stall_and_full_at_the_top_three():
    assert bench.bench_points(0.6, 0.6, 0.9) == 0.5
    assert bench.bench_points(0.9, 0.6, 0.9) == 1.0
    assert bench.bench_points(0.75, 0.6, 0.9) == 0.75
    assert bench.bench_points(0.3, 0.6, 0.9) == 0.25
    assert bench.bench_points(0.95, 0.6, 0.9) == 1.0
    assert bench.session_points(0.62, 0.6, [0.6, 0.6]) == 1.0  # a field at the stall: any edge is the top
    assert bench.session_points(0.6, 0.6, [0.9, 0.9, 0.9]) == 0.5
    assert simulate(stall_policy, NORMAL, 3).points() == 0.5


# ---------------------------------------------------------------- the simulator's venues


def _open(w: World, mechanism: str, team: str = US, fee_bps: int = 0) -> tuple[str, str]:
    w.team(team).unlocked.append("chato")
    opened = broker.open_venue(w, team, {"name": f"V{team}", "fee_bps": fee_bps, "rules": {"mechanism": mechanism}})
    return opened["venue"], opened["broker_key"]


def test_the_simulators_book_shows_who_is_there_now_at_their_current_quote():
    m = manual_world(bench_first_tick=1, bench_preset="hard")
    w = m.world
    vid, key = _open(w, "board")
    m.step()
    venue = broker.venue_for_broker(w, key)
    assert venue is not None
    run = w.state.bench[-1]
    assert run.preset == "hard" and len(run.traders) == 12
    for k in range(16):
        shown = {o["id"]: o for o in broker.book(w, venue)["bench_offers"]}
        assert set(shown) == {t.id for t in run.traders if bench.present(t, k)}
        for t in run.traders:
            if t.id in shown:
                side = shown[t.id]["want" if t.side == "sell" else "give"]
                assert side["cash"] == bench.quote_at(t, k)
        m.step()


def test_a_broker_cannot_match_a_trader_who_left():
    m = manual_world(bench_first_tick=1, bench_preset="normal")
    w = m.world
    vid, key = _open(w, "board")
    m.step()
    run = w.state.bench[-1]
    venue = broker.venue_for_broker(w, key)
    assert venue is not None
    s = next(t for t in run.traders if t.side == "sell")
    b = next(t for t in run.traders if t.side == "buy")
    m.step(max(s.arrive + s.life, b.arrive + b.life))
    with pytest.raises(SimError):
        broker.match(w, venue, {"sell": s.id, "buy": b.id, "price": b.limit})


def test_the_limit_rule_lets_a_broker_match_inside_the_limits():
    m = manual_world(bench_first_tick=1, bench_preset="static", bench_match_rule="limit")
    w = m.world
    vid, key = _open(w, "board")
    m.step()
    run = w.state.bench[-1]
    venue = broker.venue_for_broker(w, key)
    assert venue is not None and run.rule == "limit"
    s = min((t for t in run.traders if t.side == "sell"), key=lambda t: t.limit)
    b = max((t for t in run.traders if t.side == "buy"), key=lambda t: t.limit)
    assert broker.match(w, venue, {"sell": s.id, "buy": b.id, "price": s.limit})["ok"]


def test_an_auto_venue_scores_what_the_stall_replica_scores_and_the_finish_names_the_stall():
    for seed in range(6):
        m = manual_world(bench_first_tick=1, seed=seed, bench_preset="hard")
        w = m.world
        _open(w, "auto")
        m.step(18)
        done = next(e for e in w.state.events if e.type == "bench.finished")
        assert done.payload["preset"] == "hard"
        assert w.team(US).bench_efficiency == done.payload["stall_efficiency"] == done.payload["top3_efficiency"]


def test_every_nth_market_test_is_the_hard_one_and_the_schedule_says_so():
    m = manual_world(bench_first_tick=1, bench_every_ticks=20, bench_hard_every=2, bench_preset="normal")
    w = m.world
    m.step()
    assert w.state.bench[-1].preset == "normal"
    from bazaar_sim.views import schedule_view

    nxt = next(s for s in schedule_view(w)["upcoming"] if s["action"] == "bench")
    assert nxt["params"] == {"name": "The hard Market Test", "ticks": 16, "traders": 12}
    m.step(20)
    assert w.state.bench[-1].preset == "hard" and len(w.state.bench[-1].traders) == 12


def test_a_typo_in_the_bench_settings_fails_at_boot():
    with pytest.raises(ValueError):
        SimConfig(bench_preset="hardest")
    with pytest.raises(ValueError):
        replace(SimConfig(), bench_match_rule="mid")


# ---------------------------------------------------------------- the kit's own broker over real HTTP


def test_the_kits_starter_broker_runs_the_market_test_over_http_and_scores_what_the_stall_scores():
    from bazaar_sdk import Bazaar, BazaarError

    from tests.simkit import QUIET, running_sim

    config = replace(QUIET, bench_first_tick=1, bench_preset="hard", seed=11)
    with running_sim(config, run_clock=False) as (url, sim):
        w = sim.world
        with w.lock:
            w.team(US).unlocked.append("chato")
        team = Bazaar(url, "sim-team1", wait_on_tick=False, retries=1)
        brk = team.broker(team.open_venue("Uno", fee_bps=0, rules={"mechanism": "board"})["broker_key"])
        seen: dict[str, int] = {}
        accepted = 0
        for _ in range(16):
            w.advance()
            book = brk.book()
            for o in book["bench_offers"]:
                seen.setdefault(o["id"], book["tick"])
            for sell, buy, price in starter_broker.bench_plan(book):
                accepted += bool(brk.match(sell, buy, price)["ok"])
        gone = [i for i in seen if i not in {o["id"] for o in brk.book()["bench_offers"]}]
        assert accepted >= 1 and gone
        sells = [i for i in gone if any(t.id == i and t.side == "sell" for t in w.state.bench[-1].traders)]
        buys = [i for i in gone if i not in sells]
        assert sells and buys
        with pytest.raises(BazaarError) as e:
            brk.match(sells[0], buys[0], 50)  # out of the book (matched or departed): refused at any price
        assert e.value.code == "invalid"
        w.advance()
        done = next(e for e in w.state.events if e.type == "bench.finished")
        assert team.me()["score"]["bench_efficiency"] == done.payload["stall_efficiency"]
