---
model: openrouter/openai/gpt-5.4
description: Bring up the Postgres + pgvector memory and load the captured feed into it
---

Use the `bazaar` skill. Run these from the repository root and summarise what matters for our next trade (prices, who needs what, tick budget). $ARGUMENTS narrows the request (a card, a dealer or a team).

```sh
uv run bazaar db up
uv run bazaar db init
uv run bazaar db load
uv run bazaar db tables
```

Never print a key. If a command needs `BAZAAR_KEY` and it is missing, say so instead of guessing.
