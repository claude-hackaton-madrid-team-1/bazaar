---
model: openrouter/openai/gpt-5.4
description: Start (or check) the tick-driven feed capture that keeps the history the server drops after 500 events
---

Use the `bazaar` skill. Run these from the repository root and summarise what matters for our next trade (prices, who needs what, tick budget). $ARGUMENTS narrows the request (a card, a dealer or a team).

```sh
uv run bazaar feed stats
uv run bazaar feed capture   # keep it running in its own terminal
```

Never print a key. If a command needs `BAZAAR_KEY` and it is missing, say so instead of guessing.
