"""Which dealers (or dealer items) are blocked for us right now, from recalled learnings.

Only a blocker read from structure (`source == "rules"`, from the feed, a refusal or our own thread,
never an LLM reading of someone's words) and bound to OUR team id blocks (another team's cooloff is
behaviour, not our problem), only while it is in force (`until_tick` exclusive), and a `locked` blocker
is lifted by a later unlock (`level.unlocked` for us, `persona.open_to_all`). A blocker never adds a
move: it only removes one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from bazaar_agent.learn.model import ORIGIN_THAT_BLOCKS, Learning
from bazaar_agent.learn.reader import COOLOFF_CAP_TICKS, HOURLY_CAP_TICKS, LOCKED_RECHECK_TICKS

# The longest a blocker may hold after the tick it was learned, whatever `until_tick` a stored row carries
# (rows written before the caps, or by another process): a refusal is free, a stale block loses trades.
CAP_TICKS = {
    "cooloff": COOLOFF_CAP_TICKS,
    "quota": HOURLY_CAP_TICKS,
    "sold_out": HOURLY_CAP_TICKS,
    "blocker": LOCKED_RECHECK_TICKS,
}


def in_force(lr: Learning, tick: int) -> bool:
    """Still blocking at `tick`: before its `until_tick`, and within its kind's cap from when it was learned.
    A fact learned "in the future" (a tick counter reset, simulator rows) is not believed (one tick of slack
    for an event that lands just before the clock read)."""
    cap = CAP_TICKS.get(lr.kind)
    if (
        lr.kind == "quota"
        and lr.source == "rules"
        and lr.detail.get("origin") == "refusal"
        and lr.detail.get("quota_until_tick") == lr.until_tick
    ):
        cap = None  # explicit server refusal dated by our game clock; legacy/LLM rows keep their caps
    return lr.active(tick) and lr.tick <= tick + 1 and (cap is None or tick + 1 < lr.tick + cap + 1)


@dataclass(frozen=True)
class Blocks:
    dealers: Mapping[str, Learning] = field(default_factory=dict)  # the whole dealer
    items: Mapping[tuple[str, str], Learning] = field(default_factory=dict)  # (dealer, item)

    def stops(self, dealer: str, item: str | None = None) -> Learning | None:
        """The blocker that stops opening a thread with `dealer` for `item`, if any."""
        whole = self.dealers.get(dealer)
        if whole is not None:
            return whole
        return self.items.get((dealer, item)) if item is not None else None

    def __bool__(self) -> bool:
        return bool(self.dealers or self.items)

    def describe(self) -> list[str]:
        return [lr.text for lr in (*self.dealers.values(), *self.items.values())]


def _unlocks(learnings: Iterable[Learning], us: str) -> dict[str, int]:
    """dealer → the newest tick it was unlocked for us (or for everyone)."""
    out: dict[str, int] = {}
    for lr in learnings:
        if lr.subject_kind == "dealer" and lr.source == "rules" and lr.detail.get("unlocks") and lr.team in (None, us):
            out[lr.subject] = max(out.get(lr.subject, -1), lr.tick)
    return out


def _stronger(old: Learning | None, new: Learning) -> Learning:
    """Of two blockers on the same thing, the one that lasts longer (then the more certain)."""
    if old is None:
        return new
    return max(old, new, key=lambda lr: (lr.until_tick or 0, lr.confidence))


def blocks_for(learnings: Iterable[Learning], us: str | None, tick: int) -> Blocks:
    """The blockers in force for our team at `tick`."""
    if not us:
        return Blocks()
    pool = list(learnings)
    unlocked = _unlocks(pool, us)
    dealers: dict[str, Learning] = {}
    items: dict[tuple[str, str], Learning] = {}
    for lr in pool:
        if not lr.blocking or lr.subject_kind != "dealer" or lr.team != us or not in_force(lr, tick):
            continue
        if lr.source != "rules" or not ORIGIN_THAT_BLOCKS.fullmatch(str(lr.detail.get("origin", ""))):
            continue
        if lr.kind == "blocker" and unlocked.get(lr.subject, -1) >= lr.tick:
            continue
        item = lr.detail.get("item")
        if isinstance(item, str):
            items[(lr.subject, item)] = _stronger(items.get((lr.subject, item)), lr)
        else:
            dealers[lr.subject] = _stronger(dealers.get(lr.subject), lr)
    return Blocks(dealers, items)
