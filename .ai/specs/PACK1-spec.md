# PACK1 — stop pack restocking and prioritize team market sales

User prioritizes Sales hunting buyers across allied markets, not new dealer sales. Set pack_restock_enabled=false only. Keep dealer_sell_enabled=false and all other guards, caps, floors and executor logic unchanged. With deployed Taker CARD HUNT default-on (BAZAAR_CARD_HUNT unset), both restock and holding-value pack buys are excluded; other profitable guarded trades remain eligible.

Coordinator must merge through deploy guard and resume Taker only after the policy loads. Existing Maker/Sales are resumed; Taker's service-local pause persists. No new dealer openings or listing reclaim is added.

## Evidence
Focused card-hunt/dealer-desk/pack-gate/guardrails/pack-restock/Pilar-readiness modules are rerun after the final policy correction. Guard assertions preserve pack cap, cash floor and quota checks.

Unverified: deployment, team sale fills and score recovery. Could-not-do: this minimal policy does not change Sales targeting or reclaim already committed copies. No SDK/network fix.

Final verification: `uv run pytest tests/test_pack_restock.py tests/test_pilar_readiness.py tests/test_card_hunt.py tests/test_dealer_sell_desk.py tests/test_pack_gate.py tests/test_guardrails.py -q` → `119 passed`; rules validation and diff check exit0. Policy/test alignment verified; deployment/fills unverified.
