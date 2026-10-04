# SALES3: truthful market-focused Sales wording

Local backlog, operator request 2026-10-04. Apply the existing `.ai/skills/influence-psychology/SKILL.md` to actual Sales contact. Scope is wording only: guarded code remains the sole authority for prices, copies, venues and execution.

Plan: add a Sales-specific prompt and bilingual fallback; improve the active deterministic public-offer introduction with a voluntary need-check and exact offer/market CTA; keep authentication, tick budgets, standard Opus 5.5 and structured terms unchanged.

Acceptance and evidence:
- Verified prompt/fallback use cooperation, a tentative need-check and transparent attached-term/venue/fee review. `src/bazaar_agent/agents/sales_words.py`; tests cover bilingual fallback, prompt constraints and model/price secrecy.
- Verified actual promotion names the observed offer/ref/price/venue, asks whether it fits and tells the recipient to verify expiry/fees/cost at their own values. Text reserves nothing and accepts nothing. `src/bazaar_agent/agents/sales_promotion.py`; existing actor test checks exact reference and no structured terms, augmented with the new CTA.
- Verified executor and guards unchanged. Focused final run: `60 passed in 0.41s`; Ruff `All checks passed!`; sales_words mypy `Success: no issues found in 1 source file`.

Honest implementation metric: 3/3 source and focused-test criteria verified, 100%. Unverified: model's live wording compliance, increased conversion/profit/score and production rollout. Could-not-do: deployment belongs to coordinator; no game write performed.

Skill assessment, editorial judgment rather than measured conversion: before 5/10 generic invitation; after 7/10 cooperation + voluntary need-check + transparent terms/CTA. Full 10/10 is not claimed: accepted trades are irreversible and model WordsRequest lacks verified recipient need/venue. Deterministic promotion uses its actual verified quote. Fake scarcity/social proof/gifts and score promises are excluded; no external skill installed.
