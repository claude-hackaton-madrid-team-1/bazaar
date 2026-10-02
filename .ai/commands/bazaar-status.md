---
description: Show the live game clock and our team status
---

Use the `bazaar` skill. Run these from the repository root and summarise what matters for our next trade (prices, who needs what, tick budget). $ARGUMENTS narrows the request (a card, a dealer or a team).

```sh
uv run bazaar clock
uv run bazaar status
```

Never print a key. If a command needs `BAZAAR_KEY` and it is missing, say so instead of guessing.
