"""The simulated world's state: plain pydantic models, so the whole world snapshots to one JSON document.

Nothing here has behaviour beyond small helpers; the rules live in `world.py`, `dealers.py`,
`market.py`, `duels.py` and `rivals.py`. Ids are integers from `WorldState.counters`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

OfferStatus = Literal["open", "accepted", "settled", "cancelled", "expired", "replaced", "failed"]
ThreadStatus = Literal["open", "deal", "walked", "closed", "cooloff"]


class Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class Asset(Model):
    id: int
    kind: Literal["card", "pack"]
    ref: str
    serial: int
    owner: str  # a team id, a dealer id, or "world"
    history: list[dict[str, Any]] = Field(default_factory=list)  # {"from", "to", "tick", "why"}


class Side(Model):
    """One side of a structured offer: cash, exact assets, and asset types (`card:LAV-03`, `pack:sobre_barrio`)."""

    cash: int = 0
    assets: list[int] = Field(default_factory=list)
    types: list[str] = Field(default_factory=list)


class Offer(Model):
    id: int
    maker: str
    to: str | None = None
    venue: str | None = None
    thread: int | None = None
    status: OfferStatus = "open"
    give: Side = Field(default_factory=Side)
    want: Side = Field(default_factory=Side)
    expires_tick: int
    created_tick: int
    final: bool = False
    accepted_by: str | None = None
    accepted_tick: int | None = None
    accept_assets: list[int] = Field(default_factory=list)


class Message(Model):
    id: int
    tick: int
    sender: str
    text: str | None = None
    offer: int | None = None  # the structured offer this message carried


class Negotiation(Model):
    """A dealer's private side of one conversation. `limit` is its secret: the floor when it sells,
    the ceiling when it buys. `ask` is its standing price (None before it has spoken)."""

    side: Literal["sell", "buy"]  # sell = the dealer sells to the team
    item: str  # a card ref or a pack id
    item_kind: Literal["card", "pack"]
    rarity: str | None = None
    assets: list[int] = Field(default_factory=list)  # the team's assets on a sell topic
    list_price: int
    opening: int
    limit: int
    ask: int | None = None
    last_team_price: int | None = None
    last_team_text: str | None = None
    rounds: int = 0
    patience: int
    final: bool = False
    final_tick: int | None = None
    pending_reply: bool = True
    kind_bonus_used: bool = False


class Thread(Model):
    id: int
    kind: Literal["persona", "team"]
    team: str  # the side that opened it
    with_: str = Field(alias="with")
    topic: dict[str, Any] | None = None
    venue: str | None = None
    status: ThreadStatus = "open"
    closed_reason: str | None = None
    until_tick: int | None = None
    created_tick: int
    last_activity_tick: int
    messages: list[Message] = Field(default_factory=list)
    neg: Negotiation | None = None


class DealRecord(Model):
    dealer: str
    item: str
    price: int
    tick: int
    hour: int
    share: float  # share of the dealer's price range captured, 0..1
    negotiated: bool


class Team(Model):
    id: str
    name: str
    bot: bool = False
    cash: int = 400
    affinity: dict[str, float] = Field(default_factory=dict)
    unlocked: list[str] = Field(default_factory=lambda: ["abuela"])
    badges: list[str] = Field(default_factory=list)
    frozen: bool = False
    deals: list[DealRecord] = Field(default_factory=list)
    mood: dict[str, float] = Field(default_factory=dict)  # per dealer: kindness up, rudeness down
    cooloff: dict[str, int] = Field(default_factory=dict)  # per dealer: until tick
    trade_gain: float = 0.0
    duel_points: float = 0.0
    mm_points: float = 0.0
    bench_efficiency: float | None = None
    bench_venue: str | None = None
    luck: float = 0.0
    venue: str | None = None


class Venue(Model):
    venue: str
    name: str
    owner: str
    owner_name: str
    status: Literal["open", "closing", "closed", "suspended"] = "open"
    fee_bps: int = 500
    fee_per_card: int = 1
    rules: dict[str, Any] = Field(default_factory=dict)
    bond: int = 0
    starter: bool = False
    trades: int = 0
    volume: int = 0
    fees: int = 0
    traders: list[str] = Field(default_factory=list)
    pairs: int = 0
    pending_fee: dict[str, int] | None = None
    suspension_reason: str = ""
    house: bool = False
    description: str = ""
    opened_tick: int = 0
    live_tick: int = 0
    closed_tick: int | None = None
    broker_key_hash: str | None = None
    value_created: float = 0.0


class DuelOffer(Model):
    id: int
    price: int
    days: int = 0
    tick: int


class Duel(Model):
    duel: int
    session: int
    team: str
    role: Literal["seller", "buyer"]
    item: str
    issues: list[str]
    your_limit: int
    rival: str
    rival_limit: int
    your_days_weight: float | None = None
    rival_days_weight: float | None = None
    deadline_tick: int
    decay_per_round: float
    started_tick: int
    rounds: int = 0
    status: Literal["live", "deal", "no_deal"] = "live"
    practice: bool = False
    your_offer: DuelOffer | None = None
    rival_offer: DuelOffer | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)  # {"tick", "from", "text", "price", "days"}
    accepted: Literal["team", "rival"] | None = None
    accepted_tick: int | None = None
    last_message_tick: int | None = None
    result: dict[str, Any] | None = None
    price: int | None = None
    days: int | None = None


class BenchTrader(Model):
    """One synthetic trader of a Market Test. The defaults are the old static trader (there all run, never relaxes)."""

    id: str
    side: Literal["sell", "buy"]
    limit: int  # hidden: a seller's cost or a buyer's value
    quote: int  # the opening quote, shaded away from the limit (`bench.quote_at` gives the current one)
    arrive: int = 0  # the run-relative tick it shows up in the book
    life: int = 1_000  # ticks it stays (1 = only its arrival tick); unmatched, it then leaves
    relax: float = 0.0  # share of its shade given up by its last tick (0 = firm, 1 = quotes its limit at the end)

    @property
    def firm(self) -> bool:
        return self.relax <= 0.0


class BenchRun(Model):
    run: int
    start_tick: int
    end_tick: int
    traders: list[BenchTrader]
    matched: dict[str, list[list[str]]] = Field(default_factory=dict)  # venue -> [[sell id, buy id], ...]
    scored: bool = False
    preset: str = "static"
    rule: str = "quote"  # "quote" or "limit": what a broker's match must cross at (`bench.refusal`)


class Event(Model):
    id: int
    tick: int
    t: float
    type: str
    scope: str = "public"
    actor: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class ClockState(Model):
    tick: int = 0
    tick_seconds: float = 10.0
    tick_started_at: float = 0.0  # wall clock (epoch seconds) when the current tick began
    t_seconds: float = 0.0  # game seconds elapsed (sum of tick lengths)
    paused: bool = False
    booted_at: float = 0.0


class WorldState(Model):
    version: int = 1
    seed: int = 7
    clock: ClockState = Field(default_factory=ClockState)
    teams: dict[str, Team] = Field(default_factory=dict)
    assets: dict[int, Asset] = Field(default_factory=dict)
    offers: dict[int, Offer] = Field(default_factory=dict)
    threads: dict[int, Thread] = Field(default_factory=dict)
    venues: dict[str, Venue] = Field(default_factory=dict)
    duels: dict[int, Duel] = Field(default_factory=dict)
    bench: list[BenchRun] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    counters: dict[str, int] = Field(default_factory=dict)
    minted: dict[str, int] = Field(default_factory=dict)
    usage: dict[str, int] = Field(default_factory=dict)  # "<team>:<what>:<tick>" -> count (per-tick caps)
    chato_open_tick: int = 0
    leaderboard: list[dict[str, Any]] = Field(default_factory=list)
    leaderboard_tick: int = 0
    duel_session: int = 0
    matches: list[dict[str, Any]] = Field(default_factory=list)  # crossed pairs waiting to settle
