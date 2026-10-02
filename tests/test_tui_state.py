import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tui"))


def ev(type_, payload=None, tick=10, actor=""):
    return {"id": 1, "tick": tick, "t": 0.1, "type": type_, "scope": "public", "actor": actor, "payload": payload or {}}


def offer(maker, to, give_cash=0, want_cash=0, give_types=(), want_types=(), final=False, expires=12):
    return {
        "id": 7,
        "maker": maker,
        "to": to,
        "venue": None,
        "thread": 61,
        "status": "open",
        "give": {"cash": give_cash, "assets": [], "types": list(give_types)},
        "want": {"cash": want_cash, "assets": [], "types": list(want_types)},
        "expires_tick": expires,
        "created_tick": 10,
        "final": final,
    }


def message(sender, off, team="t01", text=None):
    return ev(
        "thread.message",
        {
            "thread": 61,
            "kind": "persona",
            "message": 1,
            "sender": sender,
            "text": text,
            "team": team,
            "with": "abuela",
            "offer": off,
        },
        actor=sender,
    )


ME = {
    "cash": 412,
    "score": {"score": 18.4, "rank": 9, "duel_points": 4.0, "ladder_points": 6.5, "neg_points": 7.9, "mm_points": 0.0},
    "album": {
        "filled": 14,
        "slots": 40,
        "pages": [
            {"set": "LAT", "name": "La Latina", "have": 2, "of": 10, "complete": False, "master": False},
        ],
    },
    "assets": [
        {"id": 1, "kind": "card", "ref": "LAT-03", "rarity": "common", "set": "LAT", "your_value": 6.0},
        {"id": 2, "kind": "card", "ref": "LAT-09", "rarity": "rare", "set": "LAT", "your_value": 40.0},
        {"id": 3, "kind": "card", "ref": "LAT-09", "rarity": "rare", "set": "LAT", "your_value": 4.0},
        {"id": 4, "kind": "pack", "ref": "sobre_barrio"},
    ],
}


class ReducerTest(unittest.TestCase):
    def setUp(self):
        from state import State

        self.s = State()
        self.apply = __import__("state").apply
        self.apply(self.s, ev("agent.hello", {"team": "t01", "name": "Team 1"}))

    def test_hello_names_the_team(self):
        self.assertEqual((self.s.team, self.s.name), ("t01", "Team 1"))

    def test_clock_moves_the_tick_and_day(self):
        self.apply(self.s, ev("clock", {"day": "sat", "tick_seconds": 30}, tick=144))
        self.assertEqual((self.s.tick, self.s.day, self.s.tick_seconds), (144, "sat", 30))

    def test_phase_and_goal(self):
        self.apply(self.s, ev("agent.phase", {"phase": "decide", "goal": "complete La Latina"}))
        self.assertEqual((self.s.phase, self.s.goal), ("decide", "complete La Latina"))

    def test_thoughts_and_actions_land_in_the_log_in_order(self):
        self.apply(self.s, ev("agent.thought", {"text": "Abuela is soft today"}, tick=3))
        self.apply(self.s, ev("agent.action", {"kind": "say", "summary": "bid 18 P"}, tick=4))
        self.assertEqual(
            [(line.tick, line.kind, line.text) for line in self.s.log],
            [(3, "thought", "Abuela is soft today"), (4, "say", "bid 18 P")],
        )

    def test_dealer_ask_and_our_bid_on_one_thread(self):
        self.apply(
            self.s,
            message(
                "abuela", offer("abuela", "t01", give_types=["pack:sobre_barrio"], want_cash=30), text="30 P, cariño"
            ),
        )
        self.apply(self.s, message("t01", offer("t01", "abuela", give_cash=18, want_types=["pack:sobre_barrio"])))
        th = self.s.threads[61]
        self.assertEqual((th.with_, th.topic, th.side), ("abuela", "sobre_barrio", "buy"))
        self.assertEqual((th.their_price, th.our_price, th.rounds), (30, 18, 2))
        self.assertEqual(th.last_text, "30 P, cariño")
        self.assertFalse(th.final)

    def test_final_offer_and_expiry_are_tracked(self):
        self.apply(
            self.s,
            message("abuela", offer("abuela", "t01", give_types=["card:LAT-08"], want_cash=22, final=True, expires=33)),
        )
        th = self.s.threads[61]
        self.assertTrue(th.final)
        self.assertEqual((th.expires_tick, th.topic), (33, "LAT-08"))

    def test_other_teams_threads_are_not_ours(self):
        self.apply(self.s, message("abuela", offer("abuela", "t07", want_cash=27), team="t07"))
        self.assertEqual(self.s.threads, {})

    def test_closed_thread_is_marked(self):
        self.apply(self.s, message("abuela", offer("abuela", "t01", want_cash=30)))
        self.apply(self.s, ev("thread.closed", {"thread": 61}))
        self.assertEqual(self.s.threads[61].status, "closed")

    def test_me_snapshot_sets_cash_score_album_and_values(self):
        self.apply(self.s, ev("agent.me", ME))
        self.assertEqual(self.s.cash, 412)
        self.assertEqual(self.s.score["rank"], 9)
        self.assertEqual(self.s.pages[0]["set"], "LAT")
        self.assertEqual(self.s.owned["LAT-09"], 2)
        self.assertEqual(self.s.values["LAT-09"], 4.0)
        self.assertNotIn("sobre_barrio", self.s.owned)

    def test_market_trade_between_other_teams(self):
        self.apply(
            self.s,
            ev(
                "settlement",
                {
                    "parties": ["t04", "t11"],
                    "venue": "rastro",
                    "persona": None,
                    "price": 14,
                    "kind": "trade",
                    "items": [{"ref": "MAL-02", "name": "Plaza del Dos de Mayo", "frm": "t04", "to": "t11"}],
                },
                tick=50,
            ),
        )
        t = self.s.tape[0]
        self.assertEqual(
            (t.tick, t.venue, t.seller, t.buyer, t.ref, t.price, t.ours, t.gain),
            (50, "rastro", "t04", "t11", "MAL-02", 14, False, None),
        )

    def test_our_buy_gains_value_minus_price(self):
        self.apply(self.s, ev("agent.me", ME))
        self.apply(
            self.s,
            ev(
                "settlement",
                {
                    "parties": ["abuela", "t01"],
                    "venue": None,
                    "persona": "abuela",
                    "price": 22,
                    "items": [{"ref": "LAT-09", "name": "San Isidro", "frm": "abuela", "to": "t01"}],
                },
            ),
        )
        t = self.s.tape[0]
        self.assertTrue(t.ours)
        self.assertEqual((t.venue, t.gain), ("abuela", 4.0 - 22))

    def test_our_sale_gains_price_minus_value(self):
        self.apply(self.s, ev("agent.me", ME))
        self.apply(
            self.s,
            ev(
                "settlement",
                {
                    "parties": ["t01", "t05"],
                    "venue": "rastro",
                    "price": 14,
                    "items": [{"ref": "LAT-03", "name": "x", "frm": "t01", "to": "t05"}],
                },
            ),
        )
        self.assertEqual(self.s.tape[0].gain, 14 - 6.0)

    def test_settlement_your_value_wins_over_the_snapshot(self):
        self.apply(self.s, ev("agent.me", ME))
        self.apply(
            self.s,
            ev(
                "settlement",
                {
                    "parties": ["abuela", "t01"],
                    "price": 9,
                    "your_value": 16.0,
                    "items": [{"ref": "MAL-05", "frm": "abuela", "to": "t01"}],
                },
            ),
        )
        self.assertEqual(self.s.tape[0].gain, 7.0)

    def test_tape_is_newest_first_and_bounded(self):
        for i in range(300):
            self.apply(
                self.s,
                ev(
                    "settlement",
                    {"parties": ["t02", "t03"], "price": i, "items": [{"ref": "LAV-01", "frm": "t02", "to": "t03"}]},
                    tick=i,
                ),
            )
        self.assertEqual(self.s.tape[0].price, 299)
        self.assertLessEqual(len(self.s.tape), 200)

    def test_settlement_counts_into_last_prices_per_card(self):
        for p in (10, 12, 11):
            self.apply(
                self.s,
                ev(
                    "settlement",
                    {"parties": ["t02", "t03"], "price": p, "items": [{"ref": "LAV-01", "frm": "t02", "to": "t03"}]},
                ),
            )
        self.assertEqual(list(self.s.prices["LAV-01"]), [10, 12, 11])

    def test_duel_message_tracks_both_sides(self):
        self.apply(self.s, ev("duel.message", {"duel": 3, "role": "seller", "sender": "t01", "price": 60, "days": 4}))
        self.apply(self.s, ev("duel.message", {"duel": 3, "role": "seller", "sender": "rival", "price": 41, "days": 7}))
        d = self.s.duels[3]
        self.assertEqual(
            (d.role, d.our_price, d.their_price, d.our_days, d.their_days, d.rounds), ("seller", 60, 41, 4, 7, 2)
        )

    def test_duel_result_closes_the_duel(self):
        self.apply(self.s, ev("duel.message", {"duel": 3, "role": "buyer", "sender": "t01", "price": 40}))
        self.apply(self.s, ev("duel.result", {"duel": 3, "deal": True, "price": 47, "points": 1.2}))
        d = self.s.duels[3]
        self.assertEqual((d.status, d.deal_price, d.points), ("deal", 47, 1.2))
        self.apply(self.s, ev("duel.result", {"duel": 3, "deal": False}))
        self.assertEqual(d.status, "no deal")

    def test_unknown_types_are_ignored(self):
        before = repr(self.s)
        self.apply(self.s, ev("egg.found", {"x": 1}))
        self.assertEqual(repr(self.s), before)


class MockTest(unittest.TestCase):
    def test_mock_only_emits_known_types_and_moves_the_game(self):
        from mock import MockGame  # noqa: UP026
        from state import KNOWN_TYPES, State, apply

        game, s = MockGame(seed=7), State()
        types = set()
        for _ in range(400):
            for e in game.step():
                self.assertEqual(set(e), {"id", "tick", "t", "type", "scope", "actor", "payload"})
                types.add(e["type"])
                apply(s, e)
        self.assertLessEqual(types, KNOWN_TYPES)
        self.assertGreater(s.tick, 5)
        self.assertTrue(s.threads)
        self.assertTrue(any(t.ours for t in s.tape))
        self.assertTrue(any(not t.ours for t in s.tape))
        self.assertTrue(s.pages)
        self.assertEqual(s.team, "t01")

    def test_mock_carries_real_ids(self):
        from mock import MockGame  # noqa: UP026

        game = MockGame(seed=7)
        events = [e for _ in range(600) for e in game.step()]
        ids = [e["id"] for e in events]
        self.assertEqual(len(ids), len(set(ids)))

        settlements = [e["payload"] for e in events if e["type"] == "settlement"]
        sids = [p["settlement"] for p in settlements]
        self.assertEqual(len(sids), len(set(sids)))
        for p in settlements:
            for item in p["items"]:
                self.assertLessEqual({"id", "kind", "ref", "serial"}, set(item))
        assets = [i["id"] for p in settlements for i in p["items"]]
        self.assertEqual(len(assets), len(set(assets)))

        me = next(e["payload"] for e in events if e["type"] == "agent.me")
        held = [a["id"] for a in me["assets"]]
        self.assertTrue(all(isinstance(i, int) for i in held))
        self.assertEqual(len(held), len(set(held)))
        self.assertTrue(all("serial" in a for a in me["assets"]))

        msgs = [e["payload"] for e in events if e["type"] == "thread.message"]
        mids = [p["message"] for p in msgs]
        self.assertEqual(len(mids), len(set(mids)))

        sells = [p["offer"] for p in msgs if p["sender"] == "t01" and p["offer"]["give"]["assets"]]
        self.assertTrue(sells)
        snapshots = [e["payload"] for e in events if e["type"] == "agent.me"]
        ever_held = {a["id"] for s in snapshots for a in s["assets"]}
        for o in sells:
            for a in o["give"]["assets"]:
                self.assertIn(a["id"], ever_held)

    def test_mock_is_deterministic_per_seed(self):
        from mock import MockGame  # noqa: UP026

        a, b = MockGame(seed=3), MockGame(seed=3)
        self.assertEqual([a.step() for _ in range(50)], [b.step() for _ in range(50)])


if __name__ == "__main__":
    unittest.main()
