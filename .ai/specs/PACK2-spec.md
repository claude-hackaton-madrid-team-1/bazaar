# PACK2 — forbid further pack purchases

User explicitly instructs Taker not to buy more packs. Set existing max_packs_per_game_hour=0 only; keep restock disabled and dealer sales off. Existing unconditional packs_last_hour >= limit check refuses buy, bid and accept_buy from zero purchases onward. Card purchases remain eligible under existing rules. No new executor or override.

Coordinator owns safe merge/deployment. Unverified: deployment and post-rollout game actions. Could-not-do: none for the policy slice.

Verification: 84 focused guardrails/restock/card-hunt/pack-gate tests passed in1.33s; bazaar rules, Ruff, Black and diff check exit0. Policy and regression criteria2/2 verified (100%); no deployment claim.
