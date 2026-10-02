# Vendored copy of the Bazaar kit

- Upstream: https://bazaar.causaprima.ai/bazaar-kit.zip (the organisers' official kit)
- Version: SDK `__version__ = "0.2"`
- Zip SHA-256: `e899ea974d214af4ffe2f4030889944190dbd45eaaf8e01ef23ba2c44db4e8a3`
- Copied on 2026-10-02: every file in the zip's `bazaar-kit/` folder (`bazaar_sdk.py`, `starter_agent.py`,
  `starter_broker.py`, `README.md`, `RULES.md`), byte for byte.

Do not edit files here. When the organisers publish a new kit, re-copy it and update the version and checksum above:

```sh
curl -sSfLo /tmp/bazaar-kit.zip https://bazaar.causaprima.ai/bazaar-kit.zip
shasum -a 256 /tmp/bazaar-kit.zip
unzip -o -q /tmp/bazaar-kit.zip -d /tmp && cp /tmp/bazaar-kit/* vendor/bazaar-kit/
```

Bazaar-owned pieces live outside this directory:

- `tests/test_bazaar_kit.py`: a smoke test that pins the SDK version, the public methods the README lists and the
  `X-Team-Key` / `X-Broker-Key` headers. It fails when a new kit changes that surface.

Run from the repository root:

```sh
export BAZAAR_URL=https://bazaar.causaprima.ai BAZAAR_KEY=tk-...
cd vendor/bazaar-kit && python3 starter_agent.py
uvx pytest tests -q
```

## Open questions about the upstream code

The review on PR #18 raised two points about the upstream code. Neither is confirmed, and we leave the code as it is:

- `Bazaar.wait_tick()` also returns when the clock reports `paused`, before the tick advances. That looks deliberate,
  so the loop does not hang while the game is paused, but a caller that loops on it polls during a pause. Check
  `paused` in our own loop (#3).
- `starter_broker.public_plan()` reads `give.cash`, `want.types` and asset dicts `{kind, ref}` from
  `GET /api/broker/book`. The request format in `RULES.md` uses `give.assets` (ids) and `want.cards`. If the book
  returns offers in the request format, a sell offer raises `KeyError` and the broker exits. Confirm against a real
  book fixture (#2) before running it on our venue (#11).
