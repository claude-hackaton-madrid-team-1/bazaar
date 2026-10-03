# B5 rehearsal: every conflict resolution

One line per conflicted hunk group, in merge order. The merge commits on `night/b5-rehearsal` carry the code;
this file says what each side wanted and what was kept. "FIX" lines are cross-PR bugs fixed in their own commits.

- #62 vs main: replayed pass-1 resolution (README ledger rules + main's Live/dry block; cli duel fail-closed ledger read without #62's unused `kind`; _run_agent banner + open_ledger(live=)).
- #72(new, = #61+#68+#72+takeover) vs #60/#62 (guardrails.py): replayed (hold docstring + #62 ledger wording; #60 DUEL_DAYS_MAX + #68 ActionKind; duel rule + halted verdict).
- #72 vs #62 (cli.py dealer_buy): #72's committed() context (our open offers) inside #62's fail-closed pre-check (refusing to trade) and guard (LedgerUnavailable -> denial); #62's _ledger("dealer-buy", live=True); both imports.
- #72 vs #62 (cli.py duel_run Context): #62's pre-read accepts_this_tick + #68's stops=kill_switch(rules).
- #72 vs #62 (tests/test_ledger.py): union of imports; #62's constants/refused()/renamed fallback test + #72's two refund-dating tests.
- #72 vs #62 (tests, test-level): #62's LiveDealerClient fake gains my_offers() (#72's dealer buy reads our open offers); #72's _ledger stub accepts #62's live= keyword.
- FIX F1' (#62 x #72): reserve hook LedgerUnavailable -> traceback; with #72's meet_ask a plain False would bid with the ledger down. Now holds the tick via the kill-switch callable; test: 2 failures -> 2 held ticks -> accept.
- #71(e489449, live venue; force-pushed twice tonight) vs #60/#72 (guardrails.py): ActionKind union (#60/#68 cancel/close_thread + #71 venue_*/broker_match); Context keeps #68's stops and #71's has_venue (+ runs_venue/effective_cash_floor/floor_text); context_from fills both; check() = #60 duel rule + #71 venue rule, #68's halted verdict; kept _duel_limit_violations, halts() and #71's venue block. Audit: every purchase check uses effective_cash_floor (check() line), only pack_gate's informational cash_above_floor reads the raw floor (#71-internal, minor).
- #71 vs #72 (maker.py, taker.py, runtime/backend.py): docstring both paragraphs (hold + venue keeper); imports union (kill_switch, refund_row, LedgerRow, check + effective_cash_floor).
- #71 vs #60 (tests/test_guardrails.py): both groups; README command table: #72's sell cancel/flatten rows + #71's venue/broker rows; .ai/memory.md: both PRs' entries (both PRs commit to .ai/memory.md).
- #71 vs #72 (test-level): #72's tests/test_dealer_buy_cli.py read the committed cash_floor 270; #71's file makes it 100 + venue_bond_reserve 270 -> the fixture pins cash_floor 270 / allow_venue_open false so the cases prove what they were written for.
- FIX F2' (#68 x #71): broker_context/venue_context lacked stops=kill_switch(rules); the keeper runs before the maker's hold -> edit of trading_enabled ignored until restart. Fixed + 2 tests.
- #86 (head f6f4435) vs #62/#68 (cli.py duel_run): Context = #62's pre-read accepts_this_tick minus #86's own booking + #68's stops; the accept reservation = #86's `fresh` (not pre-booked) inside #62's try/LedgerUnavailable -> rejected row; #86 moved the per-duel body into play_one(d), so #62's `continue`s became `return`s (ruff F702 otherwise).
- #86 vs #71 (guardrails.check): #86's v2 signed-days + zero_days_free arguments, #71's venue rule, #68's halted verdict.
- FIX F5 (#62 x #86, #68 x #86): #86's slots read + v2 pre-booking outside #62's try -> outage kills the whole duel tick (#62's test failed); pre-booking used paused= not stops=. Fixed.
- #81 (head cce2de5, stacked on OLD #61) vs #72's takeover (taker._open / _desk_send): kept #72's semantics (reopen lower after she held her opening ask; bid/close_thread action with the halted hold) and added #81's dealer= on both actions. Audit: every dealer buy Action carries dealer= (taker open/bid/accept, cli dealer buy pre-check + guard, runtime dealer_buy, strategy, intents).
- #81 vs #62/#72 (cli.py dealer_buy pre-check): #72's committed() inside #62's try + #81's dealer=; typers: #71's venue/broker + #81's ladder.
- #81 vs #71 (guardrails.py): fields/ENFORCED_BY/Action union; check(): #81's per-dealer cap message + #71's effective_cash_floor/floor_text.
- FIX F6 (#55 x #81): ruff I001 in scripts/sim_e2e (bazaar_sim first-party after #55); ruff --fix.
- #79 (head 9e99763, stacked on OLD #72) vs #72 (seller.py): kept #72's one_per_thread and #79's trade_book.
- #79 vs #71/#81 (GUARDRAILS.md, guardrails.py fields/ENFORCED_BY/Action/Context): unions (venue section + counterparties section; Context.has_venue + Context.trades before #71's helpers).
- #79 vs #81 (taker accept): one Action with dealer= and counterparty=/volume=; imports union (+ intel book_values/listed_makers/settled_volume).
- #79 vs #62 (ledger_pg.py imports): union (HANDS_OFF, hands_off_id + #62's pgconn Reconnector imports).
- #79 vs #62 (cli.py _sell_context): #62 passes live= to _ledger('sell'); #79 extracted that code into _sell_context(client, me) without it (F821 undefined name, the hook refused the merge) -> _sell_context takes live from its two callers (in the merge commit).
- FIX F7 (#62 x #79, #60 x #79): hands_off_ids via #62's _run + on FallbackLedger (AttributeError in the maker otherwise); KW_ONLY Action; #79 tests (positional helper, duel terms, stdout JSON); 2 regression tests.
- #87 (head 5b9deaf) vs #79 (render.py imports): union (TYPE_CHECKING, Any).
- FIX F8 (#79 x #87, in the merge commit): both define a module-level cli._json_file with different semantics; the later one silently replaced the earlier (ruff F811, mypy no-redef), so `bazaar affinity --me file` parsed with #87's helper and failed ("6 sets but 0 multipliers"). One helper = superset (None -> None, {"body"}, feed event {"type","payload"}).
- #87 vs #71 (test-level): #87's sim test expected every scenario floor == Guardrails() default 270; the CLI reads the committed file, where #71 sets cash_floor 100 -> compare with the committed floor.
- #78 (head 6ab5b1e) vs #71 (.env.example): both blocks (broker key + tick stagger). vs #68 (tests/conftest.py): both autouse fixtures (trading_enabled copy + no tick stagger), each with its decorator.
- #78 vs #62 (test-level): #78's rate-budget duel test runs `duel run --play` on fakes against the real-game URL; #62 refuses live play without the shared ledger -> the fixture stubs _ledger with a local file, as #62's own tests do.
- FIX F9 (#71 x #77): #71's sim tests import bazaar_sim.broker._auto_bench/possible_gains, moved by #77 into bazaar_sim.bench -> ImportError; tests use bench.cross_by_quote/possible_gains.
- FIX F9b (#71 x #77): scripts/sim_market_test.py same broken import (not covered by the suite).

## Pass 1 (`night/b5-rehearsal-pass1`: old #71 e82ba8d-era build-only head and #72 before it bundled #61/#68)

- #62 vs main: README.md "guardrail ledger is shared": kept #62's new ledger rules (live needs shared DATABASE_URL, reconnect, fail closed) + main's newer "Live or dry run" block (taker/maker LIVE since 01:45, stop/turn-on steps); dropped #62's older "Turn an agent live" bullet (superseded by main).
- #62 vs #60 (cli.py duel_run): kept #62's try/except around ledger.accepts_in_tick (fail closed per duel); dropped #62's `kind` variable because #60 replaced `gr.Action(kind, ...)` with `duel_action(d, move)` (kind would be unused).
- #62 vs main (cli.py _run_agent): kept main's banner `· {settings.target_line()}` (from #55) and #62's open_ledger(live=, database_url=, game_url=) + LedgerNotShared -> refusing to trade.
- #68 vs #60/main (GUARDRAILS.md kill switch): took #68's HOLD wording; kept main's "per checkout / each Railway service has its own" on `pause_file`. No value changed.
- #68 vs #60/#62 (guardrails.py): docstring = #68's hold semantics + #62's "required by a live process" ledger wording; kept #60's DUEL_DAYS_MAX/duel_days_ok next to #68's ActionKind (cancel, close_thread); check() keeps #60's duel_inside_limit rule and returns #68's Verdict(..., halted); kept both _duel_limit_violations and halts().
- #68 vs #62 (cli.py duel_run Context): kept #62's pre-read accepts_this_tick (fail closed) and #68's stops=gr.kill_switch(rules) (live read).
- #68 vs main (cli.py rules_show and README kill-switch paragraph): took #68's hold text + main's "run from this checkout / each Railway service has its own".
- FIX F1 (#61 x #62): dealer buy reserve hook raised LedgerUnavailable -> traceback; now fails closed (wait).
- #71 vs main (.env.example): kept both blocks (simulator vars from #55 + BAZAAR_BROKER_KEY / BAZAAR_VENUE).
- #71 vs main (config.py): kept #55's sim constants and #71's BROKER_ENV_FILE; HAZARD: git placed #71's `Settings.require_broker_key` after main's module-level `env_file_path()` (a dead nested function, Settings would lose the method) -> moved it back into Settings. load_settings: #55's sim data_dir default runs first, then #71 reads <data_dir>/broker.env, so a simulator broker key lives in .local/sim-client/, never next to the real one.
- #71 vs #60/#68 (guardrails.py): ActionKind = union of #60/#68 (cancel, close_thread) and #71 (venue_*, broker_match); check() runs #60's duel rule then #71's _venue_violations and returns #68's Verdict(..., halted).
- #71 vs #60 (tests/test_guardrails.py): kept both test groups.
- #71 vs main/#68 (README command table): main's `sell cancel` (kill-switch note) + `flatten` rows, then #71's venue/broker rows (dropped #71's older `sell cancel` row).
- #71 vs main (docs/services.md): main's decision-row bullet list + #71's duel/broker kinds; kept main's "Simulator" and #71's "Our venue" sections.
- FIX F2 (#68 x #71): broker_context/venue_context did not read the kill switch live; added stops=kill_switch(rules); test flips the live file.
- FIX F3 (#55 x #71): mypy error in config.load_settings broker.env path (data dict became mixed with #55); str() it.
- #86 vs #68/#71 (guardrails.check): kept #86's signed-days argument (duel_policy == v2 and duel_days_signed) with #71's venue rule and #68's halted verdict. Read the merged duel_run: #62 fail-closed ledger read, #68 live kill switch, #60 duel_action guard and #86 planner compose.
- #81 vs #68 (taker._desk_send): kept #68's bid/close_thread action + halted hold; added #81's dealer=conv.dealer on the bid. Checked every dealer buy path carries dealer= (taker open/bid/accept, runtime dealer_buy, cli dealer buy).
- #81 vs #71 (cli.py typers): kept venue/broker apps (#71) and the ladder app (#81).
- #81 vs #62 (cli.py dealer_buy pre-check): kept #62's try/LedgerUnavailable -> refusing to trade; added #81's dealer=dealer.
- #81 vs #60/#71 (guardrails.py Guardrails/ENFORCED_BY/Action, tests): additive union (allow_venue_open + dealer_price_caps; limit/role/days/days_weight + dealer).
- #79 vs #71/#81 (GUARDRAILS.md, guardrails.py fields/ENFORCED_BY/Action): additive union (venue + dealer caps + counterparty cap sections and fields).
- #79 vs #81 (STRATEGY.md, strategy.Params, plan()): kept ladder_floor_quantile/ladder_level_deals and chaser_min_p; build_market(..., floors=...) then the chaser map replace.
- #79 vs #81 (taker accept of a board/dealer ask): one Action with #81's dealer= and #79's counterparty=/volume=.
- #79 vs main (runtime/actions.py imports): union (Mapping + replace).
- FIX F4 (#60 x #79): positional Action(..., your_value, counterparty) put the team in #60's duel 'limit' -> counterparty cap silently off; keyword args + KW_ONLY + regression test; #79 tests adapted (duel terms, stdout JSON).
- FIX F1'' (review of F1): a hold recorded by the reserve closure could fire on a later tick when negotiate returned early (meet_ask wait, words overrun); entries now expire with their tick (9a51731).
