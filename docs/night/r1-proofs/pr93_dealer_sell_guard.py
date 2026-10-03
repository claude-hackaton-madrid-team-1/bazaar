from types import SimpleNamespace

from typer.testing import CliRunner

from bazaar_agent import cli
from bazaar_agent.guardrails import Guardrails


class Ledger:
    def __init__(self):
        self.rows = []

    def spent_since(self, t):
        return 0

    def accepts_in_tick(self, t):
        return 0

    def packs_since(self, t):
        from collections import Counter

        return Counter()

    def record(self, *a):
        self.rows.append(a)

    def reserve_accept(self, *a):
        self.rows.append(("accept",) + a)
        return True


class Client:
    def __init__(self):
        self.opened, self.asks, self.status, self.reads = False, [], "open", 0

    def me(self):
        return {"cash": 500, "assets": [{"id": 437, "kind": "card", "ref": "LAV-06", "rarity": "uncommon", "your_value": 12}]}

    def clock(self):
        self.reads += 1
        return {"tick": 100 + self.t, "next_tick_in": 30, "tick_seconds": 60, "t_hours": 10.0}

    t = 0

    def flag(self, *a):
        raise AssertionError("no flag on a sale")

    def catalog(self):
        return {}

    def open_thread(self, dealer, topic):
        self.opened = True
        return {"id": 91}

    def thread(self, tid):
        offer = {"id": 700 + len(self.asks), "maker": "abuela", "status": "open", "final": False,
                 "give": {"cash": 10 + len(self.asks)}, "want": {"assets": [{"id": 437}]}}
        return {"status": self.status, "standing_offers": [] if self.status != "open" else [offer], "messages": []}

    def say(self, tid, text, price):
        self.asks.append(price)
        if price <= 14:
            self.status = "deal"

    def accept(self, oid):
        self.status = "deal"

    def close_thread(self, tid):
        self.status = "closed"


def run(monkeypatch, rules):
    client, ledger = Client(), Ledger()
    monkeypatch.setattr(cli, "_rules", lambda: SimpleNamespace(rules=rules))
    monkeypatch.setattr(cli, "load_settings", lambda: None)
    monkeypatch.setattr(cli, "team_client", lambda s: client)
    monkeypatch.setattr(cli, "_ledger", lambda s: ledger)
    monkeypatch.setattr("time.sleep", lambda s: setattr(client, "t", client.t + 1))
    r = CliRunner().invoke(cli.app, ["dealer", "sell", "437", "--start", "16", "--min", "12", "--live"])
    return r, client, ledger


def test_kill_switch_stops_the_sale_before_open(monkeypatch):
    r, client, ledger = run(monkeypatch, Guardrails(trading_enabled=False))
    assert r.exit_code != 0 and not client.opened and ledger.rows == [], r.output


def test_a_deal_is_income_on_the_ledger(monkeypatch):
    r, client, ledger = run(monkeypatch, Guardrails())
    spends = [row for row in ledger.rows if row[0] == "spend"]
    print(r.output, ledger.rows)
    assert len(spends) == 1 and spends[0][3] < 0 and spends[0][3] == -client.asks[-1] if not any(
        row[0] == "accept" for row in ledger.rows) else spends[0][3] < 0
