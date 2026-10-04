# PACK1 — stop replenishment and prioritize guarded duplicate dealer sales

User requests sales after the tick2308 silver-pack loss. Set pack_restock_enabled=false and dealer_sell_enabled=true; change no executor, prices, sell floors, protected copies, quotas or accept slots. Taker/Maker CLI CARD HUNT defaults on; the production Taker BAZAAR_CARD_HUNT is unset/default-on. Coordinator must verify Maker CARD HUNT before rollout and resume Taker only after the new policy loads. Existing tests must prove packs excluded, profitable ladder buys retained, dealer sales only into missing slots, shared commitments respected and manual dealer threads not adopted.

Rollout: only coordinator merges through deploy guard after active manual deals are safe. Current Taker pause persists on its service volume. No guarantee of buyers, fills or recovered score.

## Evidence and limits
- `uv run pytest tests/test_card_hunt.py tests/test_dealer_sell_desk.py tests/test_pack_gate.py -q`: `52 passed in 1.45s`.
- `uv run pytest tests/test_guardrails.py -q`: `41 passed in 0.20s`.
- `uv run bazaar rules`: exit 0; `git diff --check`: exit 0.
- Production Maker and Taker nonsecret SSH read-back: BAZAAR_CARD_HUNT unset/default-on (CLI default True). Maker/Sales resume verified in logs2356–2357; Taker service-local pause persists.
- Existing dealer desk skips manual busy dealers, protects sole copies, checks fresh commitments under the publication mutex, and reserves its own exact copy. It opens only on missing known ladder slots with CARD HUNT.

Honest implementation: policy and existing guard checks verified (3/3, 100%); no deployment or score recovery claim. Unverified: post-deploy sale fills and score effect. Could-not-do: automatic reclaim is absent; the dealer desk cannot sell already listed copies or current Maker ask targets. Enabling it arms future free inventory only. Existing max four dealer-sell openings/hour and all other caps unchanged.
