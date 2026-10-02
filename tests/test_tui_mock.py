import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tui"))


KNOWN_TYPES = {
    "agent.hello",
    "clock",
    "agent.phase",
    "agent.thought",
    "agent.action",
    "agent.me",
    "thread.message",
    "thread.closed",
    "settlement",
    "duel.message",
    "duel.result",
}


class MockTest(unittest.TestCase):
    def test_mock_only_emits_known_types_and_moves_the_game(self):
        from mock import MockGame  # noqa: UP026

        game = MockGame(seed=7)
        events = [e for _ in range(400) for e in game.step()]
        for e in events:
            self.assertEqual(set(e), {"id", "tick", "t", "type", "scope", "actor", "payload"})
        self.assertLessEqual({e["type"] for e in events}, KNOWN_TYPES)
        self.assertGreater(max(e["tick"] for e in events), 5)
        parties = [e["payload"]["parties"] for e in events if e["type"] == "settlement"]
        self.assertTrue(any("t01" in p for p in parties))
        self.assertTrue(any("t01" not in p for p in parties))
        self.assertEqual((events[0]["type"], events[0]["payload"]["team"]), ("agent.hello", "t01"))

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
