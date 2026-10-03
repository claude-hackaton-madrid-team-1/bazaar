---
model: openrouter/openai/gpt-5.4
description: Read the market like an order book: tape, dealer curves, competitor flow and the venue book
---

Use the `bazaar` skill. Run these from the repository root and summarise what matters for our next trade (prices, who needs what, tick budget). $ARGUMENTS narrows the request (a card, a dealer or a team).

```sh
uv run bazaar tape --limit 20
uv run bazaar curves --dealer abuela --threads 10
uv run bazaar teams
uv run bazaar book
```

Never print a key. If a command needs `BAZAAR_KEY` and it is missing, say so instead of guessing.
