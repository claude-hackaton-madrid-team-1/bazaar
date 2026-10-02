import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor" / "bazaar-kit"))

TEAM_METHODS = [
    "health", "clock", "catalog", "leaderboard", "feed", "schedule", "dealers", "dealer", "levels", "call",
    "venues", "board", "card", "me", "value", "my_threads", "my_offers", "open_thread", "thread", "say",
    "close_thread", "list_offer", "cancel", "accept", "open_pack", "flag", "open_venue", "set_fee",
    "close_venue", "broker", "duels", "duel_say", "duel_accept", "wait_tick",
]
BROKER_METHODS = ["book", "clock", "match", "announce"]


class KitTest(unittest.TestCase):
    def test_sdk_is_the_published_version(self):
        import bazaar_sdk

        self.assertEqual(bazaar_sdk.__version__, "0.2")

    def test_team_client_exposes_every_route_in_the_readme(self):
        from bazaar_sdk import Bazaar

        b = Bazaar("https://bazaar.causaprima.ai", "tk-test")
        for name in TEAM_METHODS:
            self.assertTrue(callable(getattr(b, name, None)), name)
        self.assertEqual(b._headers, {"X-Team-Key": "tk-test"})

    def test_broker_client_sends_the_broker_key(self):
        from bazaar_sdk import Bazaar, Broker

        broker = Bazaar("https://bazaar.causaprima.ai/", "tk-test").broker("bk-test")
        self.assertIsInstance(broker, Broker)
        self.assertEqual(broker._headers, {"X-Broker-Key": "bk-test"})
        self.assertEqual(broker.url, "https://bazaar.causaprima.ai")
        for name in BROKER_METHODS:
            self.assertTrue(callable(getattr(broker, name, None)), name)

    def test_errors_carry_the_server_code(self):
        from bazaar_sdk import BazaarError

        e = BazaarError("wait_for_tick", "one per tick", 429)
        self.assertEqual((e.code, e.status, str(e)), ("wait_for_tick", 429, "wait_for_tick: one per tick"))


if __name__ == "__main__":
    unittest.main()
