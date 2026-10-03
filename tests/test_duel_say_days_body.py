"""A two-issue duel message carries `days` at the top level and inside `offer` (RULES.md: either form)."""

from __future__ import annotations

from typing import Any

from bazaar_agent.sdk import TeamBazaar


def _client(sent: list[tuple[str, str, Any]]) -> TeamBazaar:
    client = TeamBazaar("http://127.0.0.1:9", "tk-t-t")

    def fake(method: str, path: str, body: Any = None, query: dict[str, Any] | None = None) -> Any:
        sent.append((method, path, body))
        return {"ok": True}

    client._call = fake  # type: ignore[method-assign]
    return client


def test_a_priced_two_issue_message_carries_days_in_both_forms() -> None:
    sent: list[tuple[str, str, Any]] = []
    _client(sent).duel_say(7, "hola", price=60, days=3)
    assert sent == [
        ("POST", "/api/duels/7/messages", {"text": "hola", "price": 60, "days": 3, "offer": {"price": 60, "days": 3}})
    ]


def test_zero_days_is_still_sent() -> None:
    sent: list[tuple[str, str, Any]] = []
    _client(sent).duel_say(7, "", price=60, days=0)
    assert sent[0][2]["days"] == 0 and sent[0][2]["offer"] == {"price": 60, "days": 0}


def test_a_price_only_message_is_unchanged() -> None:
    sent: list[tuple[str, str, Any]] = []
    _client(sent).duel_say(7, "hola", price=60)
    assert sent[0][2] == {"text": "hola", "price": 60}


def test_words_only() -> None:
    sent: list[tuple[str, str, Any]] = []
    _client(sent).duel_say(7, "hola")
    assert sent[0][2] == {"text": "hola"}


def test_the_body_reaches_the_sdk_transport_and_the_write_hook_still_fires(monkeypatch: Any) -> None:
    import bazaar_sdk

    seen: list[tuple[str, Any]] = []
    hooks: list[tuple[str, str, str]] = []

    def transport(self: Any, method: str, path: str, body: Any = None, query: dict[str, Any] | None = None) -> Any:
        seen.append((path, body))
        return {"ok": True}

    monkeypatch.setattr(bazaar_sdk.Bazaar, "_call", transport)
    client = TeamBazaar("http://127.0.0.1:9", "tk-t-t", on_write=lambda m, p, ph: hooks.append((m, p, ph)))
    client.duel_say(7, "hola", price=60, days=3)
    body = {"text": "hola", "price": 60, "days": 3, "offer": {"price": 60, "days": 3}}
    assert seen == [("/api/duels/7/messages", body)]
    assert hooks == [("POST", "/api/duels/7/messages", "before"), ("POST", "/api/duels/7/messages", "after")]
