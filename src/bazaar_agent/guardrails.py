"""Guardrails: the rules in GUARDRAILS.md, parsed into a typed model and enforced before any write.

Every write path (dealer bids and accepts, listings, cancels, thread closes, duel moves) calls `check()`
first. A denied action is not sent; the caller turns it into a walk or a hold. A kill-switch denial
(`Verdict.halted`) is always a HOLD: nothing is sent, not even a cancel or a close, and open offers and
threads stay as they are. `kill_switch()` answers "is it on right now?": it re-reads `trading_enabled`
from GUARDRAILS.md on every call (cached by mtime) and checks the pause file. An append-only ledger
shared by all processes counts spend per game hour and accepts per tick: the Postgres `ledger` table
(`ledger_pg.open_ledger`, shared across machines; required by a live process), or `.local/ledger.jsonl`
(this machine, dry run only).
"""

from __future__ import annotations

import fcntl
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, cast, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator, model_validator

from bazaar_agent import move_impact
from bazaar_agent.approvals import ApprovalBook
from bazaar_agent.config import REPO_ROOT
from bazaar_agent.intel import TEAM_ID
from bazaar_agent.official_values import OfficialValues, cap_violations

GUARDRAILS_FILE = REPO_ROOT / "GUARDRAILS.md"
RULE_LINE = re.compile(r"^- `(?P<id>[a-z_]+)` = (?P<value>.+?) — (?P<why>.+)$")
PRINCIPLE_LINE = re.compile(r"^- (?!`)(?P<text>.+)$")
SET_CODE = re.compile(r"^[A-Z]{3}$")
CARD_REF = re.compile(r"^[A-Z]{3}-[0-9]{2}$")
OFF_PAGE_RARITIES = ("epic", "legendary")  # RULES.md: on top of the page; any other rarity counts as a page card
NO_SETS = ("", "none", "-")


def team_ids(value: str) -> tuple[str, ...]:
    """'t05,t10' -> ('t05', 't10'); 'none' -> (). An id that is not a team id (tNN) is refused."""
    if value.strip().lower() in NO_SETS:
        return ()
    ids = tuple(t.strip().lower() for t in value.split(",") if t.strip())
    bad = [t for t in ids if not TEAM_ID.fullmatch(t)]
    if bad:
        raise ValueError(f"not a team id: {', '.join(bad)} (use e.g. t05,t10 or none)")
    return ids


def set_codes(value: str) -> tuple[str, ...]:
    """'RET,CHA' -> ('RET', 'CHA'); 'none' -> (). A code that is not three capitals is refused."""
    if value.strip().lower() in NO_SETS:
        return ()
    codes = tuple(c.strip() for c in value.split(",") if c.strip())
    bad = [c for c in codes if not SET_CODE.match(c)]
    if bad:
        raise ValueError(f"not a set code: {', '.join(bad)} (use e.g. RET,CHA or none)")
    return codes


def card_minimums(value: str) -> dict[str, int]:
    """'lat-10:80, SAL-01:12' -> {'LAT-10': 80, 'SAL-01': 12}; 'none' -> {}. Each entry is REF:MIN, MIN the least
    we may receive in P (1 or more); anything else is refused (ASCII checked before upper-casing: 'ſ' -> 'S')."""
    if value.strip().lower() in NO_SETS:
        return {}
    out: dict[str, int] = {}
    for entry in (e.strip() for e in value.split(",") if e.strip()):
        ref, _, low = entry.partition(":")
        ref, low = ref.strip().upper(), low.strip()
        ok = entry.isascii() and CARD_REF.fullmatch(ref) and low.isdigit() and int(low) >= 1 and ref not in out
        if not ok:
            raise ValueError(f"not a REF:MIN entry: {entry} (use e.g. LAT-10:80 or none)")
        out[ref] = int(low)
    return out


def card_refs(value: str) -> tuple[str, ...]:
    """The card refs of a `protect_page_exceptions` value, in order."""
    return tuple(card_minimums(value))


class GuardrailsError(ValueError):
    """GUARDRAILS.md has an unknown rule id or a bad value. The runtime refuses to start."""


def _dealer_ids(value: str) -> frozenset[str]:
    return frozenset(d.strip() for d in value.split(",") if d.strip() and d.strip().lower() != "none")


LIFTED_RARITIES = frozenset({"common", "uncommon", "rare"})  # cards a dealer's final may be taken above the cap


class Guardrails(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trading_enabled: bool = True
    pause_file: str = ".local/PAUSE"
    cash_floor: int = 270
    max_spend_per_game_hour: int = 150
    max_price_common: int = 12
    max_price_uncommon: int = 26
    max_price_rare: int = 80
    max_price_pack: int = 20
    max_price_epic: int = Field(default=0, ge=0)  # 0: buying an epic is not allowed (no max_price for it)
    off_page_min_surplus: int = Field(default=1, ge=1)  # an epic or legendary buy: at most official value minus this
    dealer_final_lift: float = Field(default=0.0, ge=0, le=0.5)
    trickster_max_strictness: float = Field(default=0.0, ge=0, le=1)  # 0: the published kind alone decides
    trickster_accept_fill_share: float = Field(default=1 / 3, gt=0, le=1)
    official_value_margin: float = Field(default=0.0, ge=0)
    max_packs_per_game_hour: int = 3
    sell_min_value_ratio: float = 1.0
    relist_step_share: float = Field(default=0.05, ge=0, le=0.5)
    relist_min_price_share: float = Field(default=0.6, ge=0, le=1)
    relist_max_lapses: int = Field(default=4, ge=1, le=100)
    relist_cooldown_ticks: int = Field(default=40, ge=1, le=2000)
    block_buying_held_cards: bool = True
    holdings_from_db: bool = True
    holdings_max_age_s: float = Field(default=5.0, ge=0, le=60)
    max_accepts_per_tick: int = 1
    dealer_max_ticks_per_thread: int = 14
    jev_can_accept_early: bool = True
    jev_accept_min_share: float = Field(default=0.0, ge=0, le=1)
    jev_timeout_s: float = 3.0
    # Speed (SP1). Off here, so code built without GUARDRAILS.md behaves as before; the file turns them on.
    jev_cache_ticks: int = Field(default=0, ge=0, le=60)
    parallel_reads: bool = False
    duel_anchor: float = 0.6
    duel_floor_margin: float = 0.05
    duel_endgame_ticks: int = 2
    duel_inside_limit: bool = True
    duel_policy: Literal["v1", "v2"] = "v1"
    duel_max_own_offers: int = Field(default=3, ge=1)
    duel_stall_ticks: int = Field(default=3, ge=1)
    duel_open_wait_ticks: int = Field(default=0, ge=0)
    duel_free_offers: int = Field(default=16, ge=0)
    duel_silent_floor_lead: int = Field(default=1, ge=0, le=4)
    duel_answer_share: float = Field(default=0.2, ge=0, le=1)
    duel_accept_margin_ticks: int = Field(default=1, ge=0)
    duel_endgame_min_share: float = Field(default=0.0, ge=0, le=1)
    duel_jitter: float = Field(default=0.0, ge=0, le=0.9)
    duel_jitter_seed: int = 0
    duel_days_signed: bool = False
    duel_days_auto: bool = False
    steer_max_change: float = Field(default=0.5, ge=0, le=1)
    steer_max_ttl_ticks: int = Field(default=240, ge=1)
    allow_flags: bool = False
    allow_venue_open: bool = False
    venue_bond_reserve: int = Field(default=270, ge=0)
    venue_open_after_game_hours: float = Field(default=6.5, ge=0)
    max_venues: int = Field(
        default=1, ge=1, le=2
    )  # 2: one board venue plus one auto hedge (RULES: a session counts our best venue)
    max_flags_sent: int = Field(default=2, ge=0, le=20)
    flag_trusted_dealers: str = "abuela,chato"  # comma-separated dealer ids the offer inspector never flags
    flag_dealers: str = "none"  # opt-in: the only dealer ids a flag may be SENT to (none: no dealer)
    inspect_accepts: bool = True
    bluff_enabled: bool = True

    @field_validator("flag_trusted_dealers", "flag_dealers")
    @classmethod
    def _dealer_list_parse(cls, value: str, info: ValidationInfo) -> str:
        if value.strip().lower() == "none":
            return value
        ids = [d.strip() for d in value.split(",")]
        if not all(re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", d) for d in ids):
            raise ValueError(f"{info.field_name} {value!r}: comma-separated dealer ids, e.g. abuela,chato (or none)")
        return value

    @model_validator(mode="after")
    def _never_flag_the_honest_dealers(self) -> Guardrails:
        honest = self.flag_dealer_ids & (self.trusted_dealers | {"abuela", "chato"})
        if honest:
            raise ValueError(f"flag_dealers {sorted(honest)}: a trusted dealer (or abuela, chato) is never flagged")
        return self

    @property
    def trusted_dealers(self) -> frozenset[str]:
        return _dealer_ids(self.flag_trusted_dealers)

    @property
    def flag_dealer_ids(self) -> frozenset[str]:
        return _dealer_ids(self.flag_dealers)

    protect_page_sets: str = "none"
    protect_page_exceptions: str = "none"  # card refs `protect_page_sets` lets us sell as a last copy
    open_sealed_packs: bool = False
    taller_enabled: bool = False
    max_taller_per_game_hour: int = Field(default=2, ge=0, le=20)
    card_release_boost_enabled: bool = False
    card_release_boost_ticks: int = Field(default=30, ge=0, le=600)
    news_signals_enabled: bool = False
    playbook_enabled: bool = False  # off here, so code built without GUARDRAILS.md behaves as before
    persona_model_enabled: bool = True
    max_counterparty_share: float = Field(default=1.0, gt=0, le=1)
    counterparty_cap_base: int = Field(default=200, ge=0)
    team_threads_enabled: bool = False
    team_threads_max_open: int = Field(default=2, ge=0, le=6)
    team_threads_dealer_reserve: int = Field(default=3, ge=0, le=6)
    team_thread_max_messages: int = Field(default=12, ge=1, le=100)
    team_thread_idle_ticks: int = Field(default=3, ge=1)
    team_swap_min_surplus: float = Field(default=3.0, ge=0)
    team_swap_max_their_share: float = Field(default=0.6, gt=0, le=1)
    team_swap_max_our_share: float = Field(default=0.85, gt=0, le=1)
    team_swap_jev_gate: bool = True
    team_swap_jev_min_confidence: float = Field(default=0.75, ge=0.5, le=1)
    team_swap_max_cash_per_hour: int = Field(default=40, ge=0)
    team_words_venue_invite: str = Field(default="none", max_length=8)
    team_desk_never_trade: str = "none"  # GUARDRAILS.md sets the live list (code without the file: no list)
    dealer_sell_enabled: bool = False
    dealer_sell_max_per_game_hour: int = Field(default=4, ge=0, le=8)
    dealer_sell_open_above_top: float = Field(default=1.6, ge=1.0, le=5.0)
    dealer_sell_rounds: int = Field(default=5, ge=1, le=20)
    dealer_sell_min_surplus: float = Field(default=2.0, ge=0)
    dealer_sell_open_max_over_median: float = Field(default=2.0, ge=1.0, le=5.0)
    dealer_sell_retry_game_hours: float = Field(default=1.0, ge=0)
    dealer_sell_dealer_gap_ticks: int = Field(default=6, ge=0)
    dealer_sell_final_min_first_ask_share: float = Field(default=0.0, ge=0, le=1)
    dealer_sell_taker_window_ticks: int = Field(default=0, ge=0, le=500)
    strategy_jev_refresh_ticks: int = Field(default=120, ge=1, le=2000)
    ladder_probe_min_share: float = Field(default=0.3, ge=0, le=1)
    risk_posture: str = Field(default="", max_length=200)
    buyer_rank_enabled: bool = False
    buyer_rank_fallback_ticks: int = Field(default=6, ge=1, le=40)
    # Live guard: off-by-default values here, so code built without GUARDRAILS.md behaves as before.
    deploy_guard_duel_ticks: int = Field(default=4, ge=0, le=100)
    deploy_guard_bench_ticks: int = Field(default=10, ge=0, le=200)
    breaker_read_timeout_s: float = Field(default=1.0, gt=0, le=5)
    human_approval_above: int = Field(default=0, ge=0)  # 0: off (GUARDRAILS.md turns it on)
    max_score_loss_per_move: float = Field(default=0.0, ge=0)  # 0: off (GUARDRAILS.md turns it on)
    score_per_neg_point_fallback: float = Field(default=0.053, gt=0, le=1)
    dealer_ladder_score: float = Field(default=0.05, ge=0, le=1)
    no_buyback_ticks: int = Field(default=0, ge=0, le=5000)  # 0: off (GUARDRAILS.md turns it on)
    live_watchdog_enabled: bool = False
    watchdog_window_ticks: int = Field(default=120, ge=1, le=2000)
    watchdog_swap_cash_per_hour: int = Field(default=40, ge=0)
    watchdog_max_swaps_per_team: int = Field(default=3, ge=1)
    watchdog_repeat_price_max: int = Field(default=3, ge=1)
    watchdog_repeat_trip_ticks: int = Field(default=20, ge=1, le=500)
    watchdog_refusal_storm: int = Field(default=50, ge=1)
    # Buy targets (`buy_targets.py`): a human's buy approval of an off-page card becomes a card the agents pursue.
    buy_targets_enabled: bool = False
    buy_target_start_share: float = Field(default=0.75, gt=0, le=1)
    buy_target_step_ticks: int = Field(default=6, ge=1, le=200)
    buy_target_steps: int = Field(default=5, ge=1, le=50)
    activity_stall_seconds: float = Field(default=0.0, ge=0, le=3600)  # 0: off (GUARDRAILS.md turns it on)

    @field_validator("team_desk_never_trade")
    @classmethod
    def _known_team_ids(cls, value: str) -> str:
        team_ids(value)
        return value

    def never_trades_with(self, team: str | None) -> bool:
        """A team the team desk never opens, proposes to or accepts from (`team_desk_never_trade`)."""
        return str(team or "").strip().lower() in team_ids(self.team_desk_never_trade)

    @field_validator("protect_page_sets")
    @classmethod
    def _known_set_codes(cls, value: str) -> str:
        set_codes(value)
        return value

    @field_validator("protect_page_exceptions")
    @classmethod
    def _known_card_refs(cls, value: str) -> str:
        card_minimums(value)
        return value

    def excepted(self, ref: str) -> bool:
        """A card named in `protect_page_exceptions`: its last copy may be sold, never below its MIN. The ref must
        match exactly (/me refs are canonical): any other spelling stays protected."""
        return ref in card_minimums(self.protect_page_exceptions)

    def exception_min(self, ref: str) -> int:
        """The least we may receive for an excepted card (0: not excepted)."""
        return card_minimums(self.protect_page_exceptions).get(ref, 0)

    def protects(self, ref: str, rarity: str | None, copies: int) -> bool:
        """Our only copy of a page card of a protected (new) page: never sold. A copy of unknown rarity
        counts as a page card (fail closed); a duplicate may still be sold. A card named in
        `protect_page_exceptions` is never protected (that card only, not its set)."""
        code = ref.split("-", 1)[0].strip().upper() if "-" in ref else ""
        page_card = str(rarity or "").strip().lower() not in OFF_PAGE_RARITIES
        return copies <= 1 and page_card and code in set_codes(self.protect_page_sets) and not self.excepted(ref)

    def max_price_for(self, rarity: str | None) -> int | None:
        return {
            "common": self.max_price_common,
            "uncommon": self.max_price_uncommon,
            "rare": self.max_price_rare,
            "pack": self.max_price_pack,
            "epic": self.max_price_epic or None,
        }.get(rarity or "")

    def value_margin_for(self, rarity: str | None) -> float:
        """How far under the official value a card buy must stay: `official_value_margin`, and for an epic or
        legendary at least `off_page_min_surplus` (at least 1, so such a buy is always strictly below our value)."""
        if str(rarity or "").strip().lower() in OFF_PAGE_RARITIES:
            return max(self.official_value_margin, float(self.off_page_min_surplus))
        return self.official_value_margin

    def final_cap_for(self, rarity: str | None) -> int | None:
        """The most a dealer's FINAL offer on a card may be taken at: the rarity cap lifted by
        `dealer_final_lift`. A pack keeps its cap; None when the rarity has no cap (never bought)."""
        cap = self.max_price_for(rarity)
        if cap is None or rarity not in LIFTED_RARITIES:
            return cap
        return math.floor(round(cap * (1 + self.dealer_final_lift), 6))


# Which code enforces each rule: shown by `bazaar rules`, kept honest by a test.
ENFORCED_BY: dict[str, str] = {
    "trading_enabled": "guardrails.kill_switch (re-read every tick) + check: hold, never walk",
    "pause_file": "guardrails.kill_switch + check: hold, never walk",
    "cash_floor": "guardrails.check",
    "max_spend_per_game_hour": "guardrails.check + ledger",
    "max_price_common": "guardrails.check",
    "max_price_uncommon": "guardrails.check",
    "max_price_rare": "guardrails.check",
    "max_price_pack": "guardrails.check",
    "max_price_epic": "guardrails.check (0: no epic is ever bought) + runtime.human_tools.buy_refusals",
    "off_page_min_surplus": "guardrails.check (every epic or legendary buy: official value minus this) + approve",
    "trickster_max_strictness": "agents.dealer.decide (a forgiving dealer's FINAL is not its limit)",
    "trickster_accept_fill_share": "agents.dealer.decide (a forgiving dealer: accept only low in its fill range)",
    "dealer_final_lift": "guardrails.check (a dealer's final only) + agents.dealer_plan",
    "official_value_margin": "guardrails.check (every card buy, official_values.OfficialValues: GET /api/me/value)",
    "max_packs_per_game_hour": "guardrails.check + ledger",
    "sell_min_value_ratio": "guardrails.check",
    "relist_step_share": "agents.relist.relist_price (maker asks)",
    "relist_min_price_share": "agents.relist.relist_floor (maker asks)",
    "relist_max_lapses": "agents.relist.relist_price (maker asks)",
    "relist_cooldown_ticks": "agents.relist.relist_price (maker asks)",
    "block_buying_held_cards": "guardrails.check (album from /me)",
    "holdings_from_db": "holdings.Holdings.me",
    "holdings_max_age_s": "holdings.Holdings.me (Postgres clock)",
    "max_accepts_per_tick": "guardrails.check + ledger.reserve_accept (shared, atomic)",
    "dealer_max_ticks_per_thread": "agents.dealer.negotiate",
    "jev_can_accept_early": "cli dealer buy → apply_advice; agents.duel_jev.choose",
    "jev_accept_min_share": "agents.dealer.apply_advice (taker._jev_early, cli dealer buy)",
    "jev_timeout_s": "jev.judge (dealer buy, taker, duels, maker)",
    "jev_cache_ticks": "agents.jev_cache (taker offer Jev, pack gate)",
    "parallel_reads": "agents.runtime.read_together (snapshot, taker boards and threads)",
    "duel_anchor": "agents.duelist.duel_move",
    "duel_floor_margin": "agents.duelist.duel_move",
    "duel_endgame_ticks": "agents.duelist.duel_move",
    "duel_inside_limit": "guardrails.check (duelist.duel_action) + agents.duelist.duel_move + agents.duel_v2",
    "duel_policy": "cli duel run + runtime duel_move + agents.duel_jev (v2: agents.duel_v2.plan_moves)",
    "duel_max_own_offers": "agents.duel_v2.duel_plan (v2 only)",
    "duel_stall_ticks": "agents.duel_v2.duel_plan (v2 only)",
    "duel_open_wait_ticks": "agents.duel_v2.duel_plan (v2 only)",
    "duel_free_offers": "agents.duel_v2.duel_plan (v2 only)",
    "duel_silent_floor_lead": "agents.duel_v2.duel_plan + counter_offer (v2 only)",
    "duel_answer_share": "agents.duel_v2.duel_plan (v2 only)",
    "duel_accept_margin_ticks": "agents.duel_v2.duel_plan + plan_moves (v2 only)",
    "duel_endgame_min_share": "agents.duel_v2.squeeze_threshold (v2 only)",
    "duel_jitter": "agents.duel_v2.jittered (v2 only)",
    "duel_jitter_seed": "agents.duel_v2.jittered (v2 only)",
    "duel_days_signed": "guardrails.check (duel_inside_limit) + agents.duel_v2.value_of (v2 only)",
    "duel_days_auto": "cli duel run + runtime duel_move (agents.duel_days.effective_rules; v2 only)",
    "steer_max_change": "llm.steering.clamp",
    "steer_max_ttl_ticks": "llm.steering.steering_from_draft",
    "allow_flags": "guardrails.check",
    "allow_venue_open": "guardrails.check (venue open/fee/announce, broker matches); agents.venue_keeper opens it",
    "venue_bond_reserve": "guardrails.check (effective_cash_floor while a planned venue is not open yet)",
    "venue_open_after_game_hours": "guardrails.check (venue_open) + agents.venue_keeper (first tick past it)",
    "max_flags_sent": "agents.inspector.FlagBook (flag_step: the desk; agents/flags.jsonl per data dir)",
    "flag_dealers": "agents.inspector.FlagBook (flag_step: the desk)",
    "flag_trusted_dealers": "agents.inspector.FlagBook (flag_step: the desk) + guardrails (never in flag_dealers)",
    "inspect_accepts": "agents.accept_gate (taker accepts, cli dealer buy, duel run --play, runtime duel_move)",
    "protect_page_sets": "guardrails.check (album from /me) + strategy.sell_moves",
    "protect_page_exceptions": "guardrails.protects + check (every sale >= MIN) + maker ask floors (maker_jev, relist)",
    "open_sealed_packs": "guardrails.check (open_pack) + agents.taker",
    "taller_enabled": "guardrails.check (taller, + max_score_loss_per_move) + agents.taker._taller (level_watch)",
    "max_taller_per_game_hour": "guardrails.check (taller: Context.taller_last_hour, shared ledger `taller:` rows)",
    "card_release_boost_enabled": "cards_heartbeat.boost -> strategy.rank (taker buys; ranking only)",
    "card_release_boost_ticks": "cards_heartbeat.boost (how long a release stays boosted)",
    "news_signals_enabled": "news.active_signals (off: the sentinel only logs and stores)",
    "playbook_enabled": "playbook.Playbook (news sentinel) → agents.taker._playbook_holds (no new dealer thread)",
    "persona_model_enabled": "agents.persona_desk via taker._persona_shaped + agents.dealer_sell_desk (ranking)",
    "max_counterparty_share": "guardrails.check (Action.counterparty + Context.trades: maker posts, taker accepts)",
    "counterparty_cap_base": "guardrails.check (with max_counterparty_share)",
    "team_threads_enabled": "agents.team_desk (read at start; BAZAAR_TEAM_THREADS=0 in the environment turns it off)",
    "team_threads_max_open": "agents.team_desk (openings)",
    "team_threads_dealer_reserve": "agents.team_desk (openings leave these conversation slots to dealers)",
    "team_thread_max_messages": "agents.team_desk (walks after this many of our messages)",
    "team_thread_idle_ticks": "agents.team_desk (closes a silent thread)",
    "team_swap_min_surplus": "swaps.judge (every proposal and accept)",
    "team_swap_max_their_share": "swaps.judge (every proposal and accept)",
    "team_swap_max_our_share": "swaps.judge (repeat deals with one team)",
    "team_swap_jev_gate": "agents.team_desk.jev_gate (every swap proposal and accept; fail closed)",
    "team_swap_jev_min_confidence": "agents.team_desk.jev_gate (Jev team_swap_worth_it threshold)",
    "team_words_venue_invite": "agents.team_desk (one invite line to our venue in every team proposal)",
    "team_desk_never_trade": "agents.team_desk (no open, proposal or accept with these teams)",
    "team_swap_max_cash_per_hour": "agents.team_desk (cash we add to swaps, `team:` spend rows in the ledger)",
    "bluff_enabled": "agents.bluff.enabled (with BAZAAR_BLUFF)",
    "dealer_sell_enabled": "agents.maker → agents.dealer_sell_desk.SellDesk (the maker only; not `dealer sell`)",
    "dealer_sell_max_per_game_hour": "agents.dealer_sell_desk.SellDesk (openings per game hour, this process)",
    "dealer_sell_open_above_top": "agents.dealer_sell_desk.plan_for (our opening ask over the dealer's top fill)",
    "dealer_sell_rounds": "agents.dealer_sell_desk.plan_for (steps from the opening ask to the typical fill)",
    "dealer_sell_min_surplus": "agents.dealer_sell_desk.candidates (the sell floor: what we lose + this)",
    "dealer_sell_open_max_over_median": "agents.dealer_sell_desk.plan_for (caps the opening ask over the median fill)",
    "dealer_sell_retry_game_hours": "agents.dealer_sell_desk.SellDesk (no reopen of a copy with a dealer that walked)",
    "dealer_sell_dealer_gap_ticks": "agents.dealer_sell_desk.SellDesk (a dealer left free after each sell thread)",
    "dealer_sell_final_min_first_ask_share": "agents.dealer_sell.decide_sell (a FINAL: also ≥ this × our first ask)",
    "dealer_sell_taker_window_ticks": "agents.dealer_sell_desk.SellDesk (not a dealer the taker wanted)",
    "strategy_jev_refresh_ticks": "agents.strategy_gate.StrategyGate (Jev asked again after this)",
    "risk_posture": "agents.strategy_gate.StrategyGate (added to every strategy state Jev reads)",
    "ladder_probe_min_share": "agents.ladder_probe.plan_one (share of her range a top keeps)",
    "buyer_rank_enabled": "agents.maker._address (the addressee of an ask the maker already decided to post)",
    "buyer_rank_fallback_ticks": "agents.maker._with_fallbacks (an addressed ask unfilled this long goes public)",
    "deploy_guard_duel_ticks": "deploy_guard.verdict (`bazaar deploy-guard`, scripts/merge_safe.sh)",
    "deploy_guard_bench_ticks": "deploy_guard.verdict (`bazaar deploy-guard`, scripts/merge_safe.sh)",
    "breaker_read_timeout_s": "guardrails.check → breakers.BreakerBoard.tripped (once per tick, fail open)",
    "human_approval_above": "guardrails.check → approvals.ApprovalBoard.read (once per tick, fail closed)",
    "max_venues": "guardrails.check (venue_open) + agents.venue_keeper.our_venue (board venue first)",
    "max_score_loss_per_move": "guardrails.check (every sale) → move_impact.sell_impact + impact_board (fail closed)",
    "score_per_neg_point_fallback": "move_impact.slope (k when our snapshots measured none)",
    "dealer_ladder_score": "move_impact.estimate (every dealer deal)",
    "no_buyback_ticks": "guardrails.check (every card buy) → impact_board (our sales in feed_events, fail closed)",
    "live_watchdog_enabled": "agents.taker → watchdog.run (after the tick's sends)",
    "buy_targets_enabled": "buy_targets.active → agents.maker (bid ladder) + agents.taker (asks within the ceiling)",
    "buy_target_start_share": "buy_targets.ladder_price (the first bid: this × the ceiling)",
    "buy_target_step_ticks": "buy_targets.ladder_price (ticks between two steps up)",
    "buy_target_steps": "buy_targets.ladder_price (steps from the first bid to the ceiling)",
    "watchdog_window_ticks": "watchdog.run (every rule's window)",
    "watchdog_swap_cash_per_hour": "watchdog.swap_rules (trips team_swap)",
    "watchdog_max_swaps_per_team": "watchdog.swap_rules (trips team_swap)",
    "watchdog_repeat_price_max": "watchdog.repeat_price_rule (trips the scope for a while)",
    "watchdog_repeat_trip_ticks": "watchdog.repeat_price_rule (the trip's until_tick)",
    "watchdog_refusal_storm": "watchdog.refusal_storms (WARN only)",
    "activity_stall_seconds": "agents.taker → activity.ActivityWatch (after the tick's sends; logs, never trades)",
}


@dataclass(frozen=True)
class RuleLine:
    rule_id: str
    raw_value: str
    why: str
    line: int


@dataclass(frozen=True)
class LoadedRules:
    rules: Guardrails
    lines: tuple[RuleLine, ...]
    principles: tuple[str, ...]
    path: Path


def _coerce(raw: str) -> Any:
    value = raw.strip().strip("`")
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    for convert in (int, float):
        try:
            return convert(value)
        except ValueError:
            pass
    return value


def parse_md_config(text: str, path: Path) -> tuple[tuple[RuleLine, ...], tuple[str, ...], dict[str, Any]]:
    """Read `` - `id` = value — why `` lines (rules) and other bullets (principles) from a Markdown file."""
    lines, principles, values = [], [], {}
    for number, raw in enumerate(text.splitlines(), start=1):
        if m := RULE_LINE.match(raw.strip()):
            rule = RuleLine(m["id"], m["value"].strip(), m["why"].strip(), number)
            if rule.rule_id in values:
                raise GuardrailsError(f"{path.name}:{number}: rule `{rule.rule_id}` is defined twice")
            lines.append(rule)
            values[rule.rule_id] = _coerce(rule.raw_value)
        elif raw.strip().startswith("- `"):
            raise GuardrailsError(f"{path.name}:{number}: not a rule line (expected - `id` = value — why)")
        elif m := PRINCIPLE_LINE.match(raw.strip()):
            principles.append(m["text"])
    return tuple(lines), tuple(principles), values


def validated(model: type[BaseModel], values: dict[str, Any], path: Path) -> Any:
    try:
        return model.model_validate(values)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
        raise GuardrailsError(f"{path.name}: {problems}") from None


def parse_guardrails(text: str, path: Path = GUARDRAILS_FILE) -> LoadedRules:
    lines, principles, values = parse_md_config(text, path)
    return LoadedRules(validated(Guardrails, values, path), lines, principles, path)


def load_guardrails(path: Path = GUARDRAILS_FILE) -> LoadedRules:
    if not path.is_file():
        raise GuardrailsError(f"{path} is missing: the runtime will not trade without its guardrails")
    return parse_guardrails(path.read_text(encoding="utf-8"), path)


# ---------------------------------------------------------------- the kill switch (read live)

_SWITCH_CACHE: dict[Path, tuple[tuple[int, int], str | None]] = {}


def _file_stop(path: Path) -> str | None:
    """Why GUARDRAILS.md stops trading right now (None: `trading_enabled` = true). Parsed again only when
    the file changed (mtime, size). Missing or invalid: every write holds (fail closed) until it is fixed."""
    try:
        stat = path.stat()
    except OSError:
        return f"{path.name} is missing: holding"
    key = (stat.st_mtime_ns, stat.st_size)
    cached = _SWITCH_CACHE.get(path)
    if cached is not None and cached[0] == key:
        return cached[1]
    try:
        loaded = load_guardrails(path)
        if "trading_enabled" not in {line.rule_id for line in loaded.lines}:  # empty or truncated mid-save
            stop: str | None = f"{path.name} has no trading_enabled line: holding"
        else:
            stop = None if loaded.rules.trading_enabled else "trading_enabled = false"
    except (GuardrailsError, OSError, ValueError) as e:  # UnicodeDecodeError is a ValueError
        stop = f"{path.name} is invalid ({type(e).__name__}: {str(e)[:160]}): holding; see `uv run bazaar rules`"
    _SWITCH_CACHE[path] = (key, stop)
    return stop


def kill_switch(rules: Guardrails, path: Path | None = None) -> tuple[str, ...]:
    """Is the kill switch on right now? Every reason it is (empty: trading may go on).

    `trading_enabled` comes from GUARDRAILS.md as it is NOW (`path`, default `GUARDRAILS_FILE`), so an
    edit takes effect on the next tick without a restart, both ways; the pause file is `rules.pause_file`.
    While it is on our processes send NOTHING to the game (no bids, accepts, posts, cancels, thread
    closes or walks); reads continue, and open offers and threads stay exactly as they are.
    """
    stops = [stop] if (stop := _file_stop(path or GUARDRAILS_FILE)) else []
    if (REPO_ROOT / rules.pause_file).exists():
        stops.append(f"pause file {rules.pause_file} exists")
    return tuple(stops)


# ---------------------------------------------------------------- ledger (shared by processes)


def is_pack(item: str) -> bool:
    """'sobre_barrio' is a pack; 'LAV-09' is a card and 'duel:12' is not an item we hold."""
    return bool(item) and "-" not in item and ":" not in item


LedgerKind = Literal["spend", "accept", "listing"]
# A listing a person posted by hand (`bazaar sell ... --live`) is booked with this item prefix and its offer id:
# the maker, which owns our board offers, never cancels or reprices it.
HANDS_OFF = "hands-off:"


def hands_off_id(item: str) -> int | None:
    rest = item[len(HANDS_OFF) :] if item.startswith(HANDS_OFF) else ""
    return int(rest) if rest.isdigit() else None


class LedgerStore(Protocol):
    """What the guardrails read and the agents write: the JSONL file or the shared Postgres table."""

    @property
    def where(self) -> str: ...  # where the counts live, for logs: "file ledger.jsonl", "postgres ledger table on …"

    def record(self, kind: str, tick: int, t_hours: float, price: int = 0, item: str = "") -> None: ...
    def spent_since(self, t_hours: float, prefix: str = "") -> int: ...  # spend rows whose item starts with prefix
    def packs_since(self, t_hours: float) -> Counter[str]: ...
    def accepts_in_tick(self, tick: int) -> int: ...
    def count_in_tick(self, kind: str, tick: int) -> int: ...
    def count_since(self, kind: str, t_hours: float, prefix: str = "") -> int: ...  # rows after `t_hours`
    def accept_items(self, tick: int) -> list[str]: ...
    def accept_rows(self, tick: int) -> list[tuple[str, int]]: ...
    def reserve_accept(self, tick: int, t_hours: float, price: int, item: str, limit: int) -> bool: ...
    def release_accept(self, tick: int, item: str) -> None: ...
    def hands_off_ids(self) -> set[int]: ...


RELEASE = "release"  # a JSONL row that gives back one reserved accept of its tick (`Ledger.release_accept`)


class Ledger:
    """Append-only JSONL of committed spend and accepts, read by every process on this machine."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.where = f"file {path.name}"

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def record(self, kind: str, tick: int, t_hours: float, price: int = 0, item: str = "") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"kind": kind, "tick": tick, "t_hours": t_hours, "price": price, "item": item}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    def spent_since(self, t_hours: float, prefix: str = "") -> int:
        return sum(
            int(e.get("price", 0))
            for e in self.entries()
            if e.get("kind") == "spend" and e["t_hours"] > t_hours and str(e.get("item") or "").startswith(prefix)
        )

    def packs_since(self, t_hours: float) -> Counter[str]:
        """Packs bought after `t_hours`, by pack id (a pack spend carries the pack id as its item)."""
        return Counter(
            str(e.get("item"))
            for e in self.entries()
            if e.get("kind") == "spend" and e["t_hours"] > t_hours and is_pack(str(e.get("item") or ""))
        )

    def accepts_in_tick(self, tick: int) -> int:
        return len(self.accept_items(tick))

    def count_in_tick(self, kind: str, tick: int) -> int:
        return sum(1 for e in self.entries() if e.get("kind") == kind and e.get("tick") == tick)

    def count_since(self, kind: str, t_hours: float, prefix: str = "") -> int:
        return sum(
            1
            for e in self.entries()
            if e.get("kind") == kind and e["t_hours"] > t_hours and str(e.get("item") or "").startswith(prefix)
        )

    def accept_items(self, tick: int) -> list[str]:
        """What took this tick's accepts: a card ref, a pack id, or `duel:<id>` (released ones left out)."""
        return [item for item, _ in self.accept_rows(tick)]

    def accept_rows(self, tick: int) -> list[tuple[str, int]]:
        """(item, price) of this tick's accepts, released ones left out."""
        rows: list[tuple[str, int]] = []
        for e in self.entries():
            item = str(e.get("item") or "")
            if e.get("tick") != tick:
                continue
            if e.get("kind") == "accept":
                price = e.get("price")
                rows.append((item, price if isinstance(price, int) and not isinstance(price, bool) else 0))
            elif e.get("kind") == RELEASE:
                gone = next((n for n, (it, _) in enumerate(rows) if it == item), None)
                if gone is not None:
                    del rows[gone]
        return rows

    def hands_off_ids(self) -> set[int]:
        """Offer ids a person posted by hand (`HANDS_OFF` listing rows)."""
        ids = (hands_off_id(str(e.get("item") or "")) for e in self.entries() if e.get("kind") == "listing")
        return {i for i in ids if i is not None}

    def reserve_accept(self, tick: int, t_hours: float, price: int, item: str, limit: int) -> bool:
        """Count and record an accept under one file lock: two processes cannot both take the last slot."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                if self.accepts_in_tick(tick) >= limit:
                    return False
                self.record("accept", tick, t_hours, price, item)
                return True
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def release_accept(self, tick: int, item: str) -> None:
        """Give back a reserved accept the game refused: a refused request costs nothing and moves nothing
        (RULES.md), so the team's accept of this tick is still free. Append-only: a RELEASE row."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                if item in self.accept_items(tick):
                    self.record(RELEASE, tick, 0.0, 0, item)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


LedgerRow = tuple[str, int, float, int, str]  # kind, tick, t_hours, price, item: `LedgerStore.record`'s arguments


def refund_row(
    price: int, item: str, created_tick: int | None, tick: int, t_hours: float, max_tick_seconds: float
) -> LedgerRow:
    """The ledger row that gives back a withdrawn bid's spend, booked in the game hour it was spent (the
    bid's `created_tick`): a refund booked at cancel time would outlive its spend inside the one-hour
    window and let `max_spend_per_game_hour` be spent twice.

    Only the spend's tick is known, and the pace may have changed since (60 s Friday ticks, 30 s on
    Saturday), so every tick since is taken at the clock's slowest pace (`max_tick_seconds`), plus one tick
    for the clock's rounded `t_hours`: the refund is never dated after its spend. Dated a little earlier, it
    leaves the window first, and the hour's spend over-counts for a moment (fail safe). A future created
    tick counts as now; an unknown one is dated an hour back, outside every window: no refund, fail safe."""
    if not isinstance(created_tick, int):
        return ("spend", tick, t_hours - 1.0, -price, item)
    ticks_ago = max(0, tick - created_tick)
    return ("spend", tick - ticks_ago, t_hours - (ticks_ago + 1) * max_tick_seconds / 3600, -price, item)


# ---------------------------------------------------------------- the check


DUEL_DAYS_MAX = 10  # RULES.md: two-issue duels trade delivery days 0 to 10


def duel_days_ok(days: float) -> bool:
    """Inside the rules' 0 to 10 days. Anything else (negative, NaN) would turn the days penalty into a bonus."""
    return 0 <= days <= DUEL_DAYS_MAX


# `cancel` (withdraw one of our offers) and `close_thread` (walk from a thread) move no cash: only the
# kill switch applies to them. `open_pack` moves no cash either; it also needs `open_sealed_packs`.
ActionKind = Literal[
    "buy",
    "sell",
    "accept_buy",
    "accept_sell",
    "bid",
    "duel_offer",
    "duel_accept",
    "flag",
    "cancel",
    "close_thread",
    "open_pack",
    "venue_open",
    "venue_close",
    "venue_fee",
    "venue_announce",
    "broker_match",
    "dealer_sell",
    "taller",
]
ACTION_KINDS: tuple[str, ...] = get_args(ActionKind)
# A sale: `sell` (a board ask), `accept_sell` (we take a bid), `dealer_sell` (our ask to a dealer on a sell thread).
SELLING = ("sell", "accept_sell", "dealer_sell")
TEAM_TRADES = ("buy", "sell", "accept_buy", "accept_sell", "bid")  # the kinds a counterparty cap applies to
ANY_TEAM = "*"  # the counterparty of an offer anyone may take: the worst case is the team we trade most with


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    item: str = ""  # card ref, pack id or duel id
    rarity: str | None = None  # "common" | "uncommon" | "rare" | "pack" | ...
    price: int | None = None
    your_value: float | None = None  # for sells: what we lose by selling that copy
    # Team-to-team trades: the other team (ANY_TEAM for an offer anyone may take). None: not a team trade
    # (a dealer), and `max_counterparty_share` does not apply.
    counterparty: str | None = None
    volume: int | None = None  # what the trade adds to the counterparty's share (default: `price`)
    final: bool = False  # a dealer's final offer (take it or it walks): its cap is `final_cap_for` (N14a)
    limit: int | None = None  # duels: our private limit (a seller's cost, a buyer's value)
    role: str | None = None  # duels: "seller" | "buyer"
    days: float | None = None  # two-issue duels: the delivery days of the deal (None in price-only duels)
    days_weight: float | None = None  # two-issue duels: `your_days_weight`
    gives_value: float = 0.0  # a swap: our copy given, net of their cash; the official value cap adds it to `price`
    scope: str | None = None  # the circuit breaker this write answers to (`breaker_scope`); None: by kind
    asset: int | None = None  # a sale: the asset id of the copy that leaves (None: the worst copy of `item` we hold)
    assets: tuple[int, ...] = ()  # the Workshop: the three copies we give, in the order of `item`'s refs


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    violations: tuple[str, ...] = field(default_factory=tuple)
    halted: bool = False  # the kill switch is among the reasons: HOLD (send nothing), never walk

    def __str__(self) -> str:
        return "allowed" if self.allowed else "denied: " + "; ".join(self.violations)


@dataclass(frozen=True)
class TradeBook:
    """Our team-to-team volume in primas: settled with each counterparty, and what our open board offers
    could still add (`addressed` to one team, or `public`: anyone may take those, so the worst case is that
    one team takes them all)."""

    settled: dict[str, int] = field(default_factory=dict)
    addressed: dict[str, int] = field(default_factory=dict)
    public: int = 0

    @property
    def total(self) -> int:
        return sum(self.settled.values())

    def exposure(self, team: str) -> int:
        """The most `team` may have traded with us once every open offer it can take fills."""
        if team == ANY_TEAM:
            teams = set(self.settled) | set(self.addressed)
            return max((self.exposure(t) for t in teams), default=self.public)
        return self.settled.get(team, 0) + self.addressed.get(team, 0) + self.public


def counterparty_refusal(trades: TradeBook | None, team: str, price: int, rules: Guardrails) -> str | None:
    """Why a trade of `price` with `team` would break `max_counterparty_share`; None when it passes or the
    cap is off (1.0). The cap is `share × max(our settled volume + price, counterparty_cap_base)`: the base
    lets the first trades through. Fails closed when our volume was not read, or when the counterparty is
    not a team id (a board pseudonym the feed did not resolve: its volume with us is unknown)."""
    share = rules.max_counterparty_share
    if share >= 1:
        return None
    if trades is None:
        return "max_counterparty_share is on but our team-to-team volume was not read"
    if team != ANY_TEAM and not TEAM_ID.match(team):
        return f"counterparty {team!r} is not a known team: its share of our volume is unknown"
    cap = share * max(trades.total + price, rules.counterparty_cap_base)
    exposure = trades.exposure(team)
    if exposure + price <= cap:
        return None
    who = "any team (public offer)" if team == ANY_TEAM else team
    return (
        f"counterparty {who}: {exposure} + {price} > max_counterparty_share {share:g} × "
        f"max(volume {trades.total + price}, {rules.counterparty_cap_base}) = {cap:.0f}"
    )


@dataclass(frozen=True)
class Context:
    cash: int
    held: dict[str, int]  # card ref -> copies we hold (from /api/me)
    tick: int
    t_hours: float
    spent_last_hour: int = 0
    accepts_this_tick: int = 0
    paused: bool = False
    packs_last_hour: int = 0
    has_venue: bool = False  # we run a venue we opened (open or closing), from /api/me `venue`
    # The kill switch read live by `kill_switch()` (context_from fills it). None: not read, so `check()`
    # falls back to `rules.trading_enabled` and `paused`.
    stops: tuple[str, ...] | None = None
    sellable: dict[str, int] | None = None  # copies not already in our open asks (seller.committed_context)
    # Our team-to-team volume (`TradeBook`), for `max_counterparty_share`. None: not read.
    trades: TradeBook | None = None
    values: OfficialValues | None = None  # GET /api/me/value reads: every card buy capped; None refuses them all
    ranking: bool = False  # a ranking or plan check: no official value read; the send's own check caps the buy
    # Tripped circuit breakers (`breakers.py`). None: read this process's board for `tick` (once per tick, fail open).
    breakers: frozenset[str] | None = None
    # Human approvals (`approvals.py`). None: read this process's board for `tick` (once per tick, fail closed).
    approvals: ApprovalBook | None = None
    # Our copies and complete pages from /api/me (`context_from`): each copy's your_value for the score impact guard.
    cards: move_impact.OurCards | None = None
    # How we got each copy and k (`impact_board`). None: read this process's board for `tick` (fail closed).
    impact: move_impact.Facts | None = None
    taller_last_hour: int = 0  # Workshop crafts in the last game hour (`max_taller_per_game_hour`, this process)
    # Why no Workshop craft may go this tick: an accept still settling hands over a copy we cannot name.
    taller_hold: str | None = None


# What a stored or answered /me (`holdings.without_secrets`) keeps of `starter_broker_key`: that it was there.
STARTER_STALL_MARKER = "has_starter_stall"


def runs_venue(me: dict[str, Any]) -> bool:
    """/api/me `venue`: our own market, open or closing (the bond is in it). A free starter stall is not one:
    /me carries `starter_broker_key` while we have the stall (the kit's `Bazaar.me`), and opening our own
    venue replaces the stall (RULES.md), so a venue named next to that key is the stall. The same answer
    drives the bond reserve and the refusal of a second opening."""
    venue = me.get("venue")
    if not venue:
        return False
    if isinstance(venue, dict) and venue.get("starter") is False:  # said outright: ours, whatever the key says
        return str(venue.get("status") or "open") in ("open", "closing")
    if me.get("starter_broker_key") or me.get(STARTER_STALL_MARKER):  # live /me, or one without its secrets
        return False
    if isinstance(venue, str):
        return True
    if not isinstance(venue, dict) or venue.get("starter") is True:
        return False
    return str(venue.get("status") or "open") in ("open", "closing")


def effective_cash_floor(rules: Guardrails, ctx: Context) -> int:
    """`cash_floor`, plus `venue_bond_reserve` while a planned venue (`allow_venue_open`) is not open yet:
    every purchase leaves the bond and opening fee in cash until the venue opens. The same for every writer."""
    # Zero unless a venue is planned: with allow_venue_open = false no bond reserve is ever held.
    reserve = rules.venue_bond_reserve if rules.allow_venue_open and not ctx.has_venue else 0
    return rules.cash_floor + reserve


def floor_text(rules: Guardrails, ctx: Context) -> str:
    if effective_cash_floor(rules, ctx) == rules.cash_floor:
        return f"cash_floor {rules.cash_floor}"
    return f"cash_floor {rules.cash_floor} + venue_bond_reserve {rules.venue_bond_reserve}"


def context_from(
    me: dict[str, Any],
    tick: int,
    t_hours: float,
    ledger: LedgerStore,
    rules: Guardrails,
    values: OfficialValues | None = None,
) -> Context:
    held: dict[str, int] = {}
    for a in me.get("assets") or []:
        if a.get("kind") == "card":
            held[str(a.get("ref"))] = held.get(str(a.get("ref")), 0) + 1
    return Context(
        cash=int(me.get("cash") or 0),
        held=held,
        tick=tick,
        t_hours=t_hours,
        spent_last_hour=ledger.spent_since(t_hours - 1.0),
        accepts_this_tick=ledger.accepts_in_tick(tick),
        paused=(REPO_ROOT / rules.pause_file).exists(),
        packs_last_hour=sum(ledger.packs_since(t_hours - 1.0).values()),
        has_venue=runs_venue(me),
        stops=kill_switch(rules),
        values=values,
        cards=move_impact.our_cards(me),
    )


def action_kind(kind: str) -> ActionKind:
    if kind not in ACTION_KINDS:
        raise ValueError(f"unknown action kind {kind!r}: use one of {', '.join(ACTION_KINDS)}")
    return cast(ActionKind, kind)


def check(action: Action, ctx: Context, rules: Guardrails) -> Verdict:
    """Every rule that the action breaks. Empty → allowed. The kill switch comes first and sets `halted`."""
    v: list[str] = list(halts(ctx, rules))
    halted = bool(v)
    buying = action.kind in ("buy", "accept_buy", "bid")
    accepting = action.kind in ("accept_buy", "accept_sell", "duel_accept")
    if buying and action.price is not None:
        cap = rules.max_price_for(action.rarity)
        top = rules.final_cap_for(action.rarity) if action.final and action.kind != "buy" else cap
        if cap is None or top is None:
            v.append(f"no max_price for rarity {action.rarity!r}: buying it is not allowed")
        elif action.price > top:
            lifted = f"dealer final cap {top} (max_price_{action.rarity} {cap} lifted)" if top > cap else ""
            v.append(f"price {action.price} > {lifted or f'max_price_{action.rarity} {cap}'}")
        if ctx.cash - action.price < effective_cash_floor(rules, ctx):
            v.append(f"cash {ctx.cash} - {action.price} < {floor_text(rules, ctx)}")
        if ctx.spent_last_hour + action.price > rules.max_spend_per_game_hour:
            v.append(
                f"spend {ctx.spent_last_hour} + {action.price} > max_spend_per_game_hour "
                f"{rules.max_spend_per_game_hour}"
            )
    if buying and action.rarity == "pack" and ctx.packs_last_hour >= rules.max_packs_per_game_hour:
        v.append(
            f"{ctx.packs_last_hour} pack(s) bought this game hour (max_packs_per_game_hour "
            f"{rules.max_packs_per_game_hour})"
        )
    if buying and rules.block_buying_held_cards and ctx.held.get(action.item, 0) > 0:
        v.append(f"we already hold {action.item} (block_buying_held_cards)")
    if action.kind in SELLING and action.price is not None and action.your_value is not None:
        floor = action.your_value * rules.sell_min_value_ratio
        if action.price < floor:
            v.append(f"sell price {action.price} < {rules.sell_min_value_ratio} × your_value {action.your_value}")
    selling = action.kind in SELLING
    copies = _copies(ctx, action.item)
    if selling and rules.protects(action.item, action.rarity, copies):
        v.append(f"{action.item} is our only copy of a page card of a new page (protect_page_sets)")
    if selling and (wrong := _not_a_copy_of_the_excepted_card(action, ctx, rules)):
        v.append(wrong)
    if selling and (low := rules.exception_min(action.item)) and (action.price is None or action.price < low):
        v.append(f"{action.item} sells for {low} P or more (protect_page_exceptions), not {action.price}")
    if accepting and ctx.accepts_this_tick >= rules.max_accepts_per_tick:
        v.append(f"{ctx.accepts_this_tick} accept(s) already this tick (max_accepts_per_tick)")
    team_trade = action.kind in TEAM_TRADES and action.counterparty is not None and action.price is not None
    volume = action.volume if action.volume is not None else action.price or 0
    if team_trade and (refusal := counterparty_refusal(ctx.trades, str(action.counterparty), volume, rules)):
        v.append(refusal)
    if action.kind == "flag" and not rules.allow_flags:
        v.append("allow_flags = false")
    if action.kind == "open_pack" and not rules.open_sealed_packs:
        v.append("open_sealed_packs = false")
    if action.kind == "taller":
        v.extend(_taller_violations(action, ctx, rules))
    if action.kind in ("duel_offer", "duel_accept") and rules.duel_inside_limit:
        v2 = rules.duel_policy == "v2"
        v.extend(_duel_limit_violations(action, v2 and rules.duel_days_signed, zero_days_free=v2))
    v.extend(_venue_violations(action, ctx, rules))
    v.extend(_breaker_violations(action, ctx, rules))
    if buying and not v:  # before the official value: a buy-back needs no /api/me/value read
        v.extend(_buyback_violations(action, ctx, rules))
    if buying and not v and not ctx.ranking:  # last, so /api/me/value is read only for a buy every rule allows
        v.extend(_official_value_violations(action, ctx, rules))
    if not v:  # after every other rule: the score a sale could cost us (the SAL-07 incident, move_impact)
        v.extend(_impact_violations(action, ctx, rules))
    if not v:  # after every other rule: a human is asked only about a trade nothing else refuses
        v.extend(_approval_violations(action, ctx, rules))
    return Verdict(not v, tuple(v), halted)


def _taller_violations(action: Action, ctx: Context, rules: Guardrails) -> list[str]:
    """The Workshop: `action.item` is the three card refs we give ("LAV-04,SAL-01,SAL-01"). Behind `taller_enabled`
    and `max_taller_per_game_hour`; every card keeps at least one free copy (a copy in an open ask of ours is not
    free: `Context.sellable`), whatever the set, so a page never loses its last copy."""
    v = [] if rules.taller_enabled else ["taller_enabled = false"]
    if rules.dealer_sell_enabled:  # SA1 interlock: the maker's sell desk may sell the very copy the Workshop keeps
        v.append("dealer_sell_enabled = true: the Workshop waits (nothing shared tells it what the sell desk sells)")
    if ctx.taller_last_hour >= rules.max_taller_per_game_hour:
        v.append(
            f"{ctx.taller_last_hour} Workshop craft(s) this game hour (max_taller_per_game_hour "
            f"{rules.max_taller_per_game_hour})"
        )
    refs = [r.strip() for r in action.item.split(",") if r.strip()]
    if len(refs) != 3:
        v.append(f"the Workshop takes three copies, not {len(refs)}")
    free = ctx.held if ctx.sellable is None else ctx.sellable
    for ref, n in sorted(Counter(refs).items()):
        if free.get(ref, 0) - n < 1:
            v.append(f"{ref}: giving {n} of our {free.get(ref, 0)} free copies leaves none (we keep one of each card)")
    if len(set(action.assets)) != len(action.assets):
        v.append(f"the Workshop's copies {list(action.assets)} repeat one")
    if action.assets and ctx.cards is None:
        v.append("the Workshop's copies cannot be matched to our /me (no cards read)")
    if ctx.taller_hold:  # `agents.taller.unnamed_settling`
        v.append(ctx.taller_hold)
    if action.assets and ctx.cards is not None:  # the copies named are the cards named, one by one
        named = [c.ref if (c := ctx.cards.copy(a)) is not None else None for a in action.assets]
        if named != refs:
            v.append(f"the Workshop's copies {list(action.assets)} are not the cards {refs} in our /me")
    if not v and rules.max_score_loss_per_move > 0 and not ctx.ranking:
        v.extend(_taller_impact(action, refs, ctx, rules))
    return v


def _taller_impact(action: Action, refs: list[str], ctx: Context, rules: Guardrails) -> list[str]:
    """`max_score_loss_per_move` for a craft: each copy given away at 0 and no ladder deal (`move_impact`: a copy a
    team trade brought us costs its your_value in neg_points). The card a craft brings is credited nothing: a pull is
    luck and never scores (RULES.md), so it adds no neg_points. Fails closed: unread origins count as team copies,
    and copies not named one by one, or with no value, refuse."""
    from bazaar_agent import impact_board

    if len(action.assets) != len(refs):
        return ["the Workshop's copies are not named one by one: their score impact cannot be estimated"]
    facts = ctx.impact if ctx.impact is not None else impact_board.board(rules.breaker_read_timeout_s).read(ctx.tick)
    total = 0.0
    for asset, ref in zip(action.assets, refs, strict=True):
        impact = move_impact.sell_impact(
            ctx.cards, ref, action.rarity, 0, None, facts, rules.score_per_neg_point_fallback, 0.0, asset
        )
        if impact.score is None:
            return [f"score impact of giving {ref} #{asset} cannot be estimated (max_score_loss_per_move)"]
        total += impact.score
    if total < -rules.max_score_loss_per_move:
        return [f"score impact {total:+.2f} < -{rules.max_score_loss_per_move:g} (max_score_loss_per_move)"]
    return []


def breaker_scope(action: Action) -> str | None:
    """The circuit breaker a write answers to. Cancels, closes and other writes that make us more careful have
    none: a tripped breaker never stops us from stepping back."""
    if action.scope is not None:
        return action.scope
    if action.kind == "duel_accept":
        return "duel_accept"
    if action.kind in ("accept_buy", "accept_sell"):
        return "board_accept"
    if action.kind == "dealer_sell":
        return "dealer_sell"
    if action.kind == "sell" or (action.kind == "bid" and action.counterparty is not None):
        return "maker_post"
    if action.kind in ("buy", "bid"):
        return "dealer_buy"
    return None


def _breaker_violations(action: Action, ctx: Context, rules: Guardrails) -> list[str]:
    scope = breaker_scope(action)
    if scope is None:
        return []
    from bazaar_agent import breakers

    tripped = ctx.breakers
    if tripped is None:
        tripped = breakers.board(rules.breaker_read_timeout_s).tripped(ctx.tick)
    return [f"circuit breaker {scope} is tripped (`bazaar breaker list`)"] if scope in tripped else []


def approval_side(action: Action) -> str | None:
    """The side a human approves for this write: a card buy or a card sell. Duels (synthetic prices, not our cash),
    packs (their own caps) and writes that move no cash have none."""
    if action.price is None or action.rarity == "pack" or is_pack(action.item):
        return None
    if action.kind in ("buy", "accept_buy", "bid"):
        return "buy"
    return "sell" if action.kind in SELLING else None


def _copies(ctx: Context, item: str) -> int:
    return (ctx.held if ctx.sellable is None else ctx.sellable).get(item, 0)


def _not_a_copy_of_the_excepted_card(action: Action, ctx: Context, rules: Guardrails) -> str | None:
    """`protect_page_exceptions` lifts the rule for a card, not for a label: a sale named after an excepted card
    must hand over a copy of that card (when /me was read and the action names its asset)."""
    if not rules.excepted(action.item) or action.asset is None or ctx.cards is None:
        return None
    if any(c.asset == action.asset and c.ref == action.item for c in ctx.cards.copies):
        return None
    return f"asset {action.asset} is not a copy of {action.item} in /me (protect_page_exceptions)"


def _approval_violations(action: Action, ctx: Context, rules: Guardrails) -> list[str]:
    """`human_approval_above`: a card trade at or above it (fee included, plus the copy a swap gives) needs an
    approval covering its card, side and price. Fails closed: approvals that cannot be read approve nothing."""
    side = approval_side(action)
    # A ranking or plan check skips it (as the official value cap): a plan prices at its ladder top, not at the
    # bid, and a human is asked only about a write about to be sent.
    if side is None or rules.human_approval_above <= 0 or action.price is None or ctx.ranking:
        return []
    price = action.price + (action.gives_value if side == "buy" else 0.0)
    if price < rules.human_approval_above:
        return []
    from bazaar_agent import approvals

    board = approvals.board(rules.breaker_read_timeout_s)
    book = ctx.approvals if ctx.approvals is not None else board.read(ctx.tick)
    if book is not None and book.covers(action.item, side, price, ctx.tick):
        return []
    shown = math.ceil(round(price, 6))
    held = ctx.held.get(action.item, 0)
    official = ctx.values.cached(action.item, ctx.tick, held) if ctx.values is not None else None
    board.needed(
        {
            "card": action.item,
            "side": side,
            "price": shown,
            "tick": ctx.tick,
            "counterparty": action.counterparty,
            "official_value": official,
            "our_value": action.your_value,
            "kind": action.kind,
        },
        int(ctx.t_hours),
    )
    unread = "" if book is not None else f" {approvals.UNREAD}"
    return [f"needs human approval: {action.item} {side} {shown}{unread}"]


def _impact_facts(ctx: Context, rules: Guardrails) -> move_impact.Facts | None:
    """This tick's origins, sales and score history (`impact_board`, read once per tick); None when unread."""
    if ctx.impact is not None:
        return ctx.impact
    from bazaar_agent import impact_board

    return impact_board.board(rules.breaker_read_timeout_s).read(ctx.tick)


_TARGET: dict[str, bool] = {}


def simulator_target() -> bool:
    """This process trades against the simulator (`Settings.simulator`), read once: rules that guard the real game
    against an unreadable shared database do not stop a simulator run, which has none by design."""
    if "sim" not in _TARGET:
        from bazaar_agent.config import load_settings

        try:
            _TARGET["sim"] = load_settings().simulator
        except Exception:  # noqa: BLE001 — an unreadable config is never taken for the simulator
            _TARGET["sim"] = False
    return _TARGET["sim"]


def _buyback_violations(action: Action, ctx: Context, rules: Guardrails) -> list[str]:
    """`no_buyback_ticks`: never buy (from a dealer, the board, or a swap) a card we sold or swapped away in the last
    that many ticks: a buy-back is not realistic trading (SAL-07: sold to Pilar at tick 948, bought back from Abuela
    at 958). Our sales come from our settlements. Unread, or a tape that lags (`Facts.tape_current`: a recent sale may
    be missing): a send to the real game is refused and holds; a ranking, or a simulator target (no shared database by
    design, `scripts/sim_smoke.py`), skips the rule."""
    if rules.no_buyback_ticks <= 0 or action.rarity == "pack" or is_pack(action.item):
        return []
    facts = _impact_facts(ctx, rules)
    team = ctx.cards.team if ctx.cards is not None else None
    if facts is None or (team is not None and facts.team != team) or not facts.tape_current:
        if ctx.ranking or simulator_target():
            return []
        return [f"no_buyback_ticks: {action.item} not bought {move_impact.SALES_UNREAD}"]
    sold = facts.sold.get(action.item)
    if sold is None or ctx.tick - sold >= rules.no_buyback_ticks:
        return []
    return [
        f"no buy-back: we sold {action.item} at tick {sold}, {ctx.tick - sold} ticks ago "
        f"(no_buyback_ticks {rules.no_buyback_ticks}: buying it back is not realistic trading)"
    ]


def _impact_violations(action: Action, ctx: Context, rules: Guardrails) -> list[str]:
    """`max_score_loss_per_move`: a sale (a board ask, a bid we take, a dealer sell, the copy a swap gives) whose
    estimated score change (`move_impact.sell_impact`) is below minus this needs a human approval of that card, side
    sell, at that price or more. Fails closed: unread facts price the copy as bought from a team (k at its fallback),
    and a copy with no value refuses."""
    if rules.max_score_loss_per_move <= 0 or action.kind not in SELLING or action.price is None or ctx.ranking:
        return []
    if action.rarity == "pack" or is_pack(action.item):
        return []
    from bazaar_agent import approvals

    facts = _impact_facts(ctx, rules)
    dealer = action.kind == "dealer_sell" or action.scope == "dealer_sell"
    who = None if dealer else (action.counterparty if move_impact.is_team(action.counterparty) else ANY_TEAM)
    impact = move_impact.sell_impact(
        ctx.cards,
        action.item,
        action.rarity,
        action.price,
        who,
        facts,
        rules.score_per_neg_point_fallback,
        rules.dealer_ladder_score,
        action.asset,
        action.your_value,
    )
    if impact.score is not None and impact.score >= -rules.max_score_loss_per_move:
        return []
    board = approvals.board(rules.breaker_read_timeout_s)
    book = ctx.approvals if ctx.approvals is not None else board.read(ctx.tick)
    if book is not None and book.covers(action.item, "sell", action.price, ctx.tick):
        return []
    board.needed(
        {
            "card": action.item,
            "side": "sell",
            "price": action.price,
            "tick": ctx.tick,
            "counterparty": action.counterparty,
            "our_value": impact.value,
            "kind": action.kind,
            "score_impact": None if impact.score is None else round(impact.score, 2),
            "reason": impact.reason,
        },
        int(ctx.t_hours),
    )
    cost = (
        "cannot be estimated" if impact.score is None else f"{impact.score:+.2f} < -{rules.max_score_loss_per_move:g}"
    )
    unread = "" if book is not None else f" {approvals.UNREAD}"
    return [
        f"score impact {cost} (max_score_loss_per_move: {impact.reason}); "
        f"needs human approval: {action.item} sell {action.price}{unread}"
    ]


def _official_value_violations(action: Action, ctx: Context, rules: Guardrails) -> list[str]:
    """Day-2 hint 1 (`official_values.cap_violations`). A pack has no official value (its rarity cap applies)."""
    if action.price is None or action.rarity == "pack" or is_pack(action.item):
        return []
    held = ctx.held.get(action.item, 0)
    margin = rules.value_margin_for(action.rarity)  # an epic or legendary: strictly below, never liftable
    return cap_violations(action.item, action.price, action.gives_value, ctx.values, ctx.tick, held, rules, margin)


# Our own market (RULES.md "Your own market"): opening costs a refundable bond plus an opening fee.
VENUE_BOND = 250
VENUE_OPENING_FEE = 20
VENUE_COST = VENUE_BOND + VENUE_OPENING_FEE
# Writes that only make sense while we run a venue: all of them wait for `allow_venue_open`. Closing does not,
# so a venue opened by hand can still be closed from the CLI (the kill switch still stops it).
VENUE_SWITCHED: frozenset[str] = frozenset({"venue_open", "venue_fee", "venue_announce", "broker_match"})


def _venue_violations(action: Action, ctx: Context, rules: Guardrails) -> list[str]:
    """Venue writes: the switch, opening once and not before `venue_open_after_game_hours`, and the bond +
    opening fee never taking cash below `cash_floor` (the reserve is what the opening spends, so it is not
    added on top here).

    The bond is not a purchase: it is never counted against `max_spend_per_game_hour` or a rarity cap
    (`action.price` is the cash the open takes, `VENUE_COST` when the caller leaves it out)."""
    v: list[str] = []
    if action.kind in VENUE_SWITCHED and not rules.allow_venue_open:
        v.append("allow_venue_open = false (build only: flip it in GUARDRAILS.md to run our venue)")
    if action.kind == "venue_open":
        cost = VENUE_COST if action.price is None else action.price
        if ctx.cash - cost < rules.cash_floor:
            v.append(f"cash {ctx.cash} - venue bond and fee {cost} < cash_floor {rules.cash_floor}")
        if ctx.has_venue and rules.max_venues <= 1:
            v.append("we already run a venue: never open a second one")
        if ctx.t_hours < rules.venue_open_after_game_hours:
            v.append(f"game hour {ctx.t_hours:g} < venue_open_after_game_hours {rules.venue_open_after_game_hours:g}")
    return v


def halts(ctx: Context, rules: Guardrails) -> tuple[str, ...]:
    """The kill-switch reasons in this context: the live read when there is one, else the loaded rules."""
    if ctx.stops is not None:
        return ctx.stops
    stops = [] if rules.trading_enabled else ["trading_enabled = false"]
    if ctx.paused:
        stops.append(f"pause file {rules.pause_file} exists")
    return tuple(stops)


def _duel_limit_violations(action: Action, signed: bool = False, zero_days_free: bool = False) -> list[str]:
    """A duel deal must be strictly better than our limit (a seller above its cost, a buyer below its value),
    after its days at |weight| each against us: the same worst case as `duelist.worth`, recomputed here.
    `signed` (`duel_days_signed`, v2 only): the weight is primas gained (+) or lost (−) per day instead.
    `zero_days_free` (v2 only): 0 days cost nothing under either sign, so a missing weight does not block them."""
    if action.price is None or action.limit is None or action.role not in ("seller", "buyer"):
        return ["cannot value the duel move (price, limit or role missing): duel_inside_limit"]
    missing = action.days is not None and action.days_weight is None
    if missing and (action.days or not zero_days_free):  # v2: 0 days cost nothing whatever the weight (B2c)
        return ["days without your_days_weight: cannot value the duel move (duel_inside_limit)"]
    if action.days is not None and not duel_days_ok(action.days):
        return [f"days {action.days} outside 0 to {DUEL_DAYS_MAX}: cannot value the duel move (duel_inside_limit)"]
    if action.days_weight is not None and not math.isfinite(action.days_weight):
        return [f"your_days_weight {action.days_weight}: cannot value the duel move (duel_inside_limit)"]
    weight = action.days_weight or 0.0
    penalty = (-weight if signed else abs(weight)) * (action.days or 0.0)
    seller = action.role == "seller"
    worth = action.price - penalty if seller else action.price + penalty
    if (worth > action.limit) if seller else (worth < action.limit):
        return []
    side = "above" if seller else "below"
    return [
        f"duel {action.role} price {action.price} is worth {worth:g}, not strictly {side} limit "
        f"{action.limit} (duel_inside_limit)"
    ]
