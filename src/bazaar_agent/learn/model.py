"""One learning: a validated, structured fact read from the live feed (or our own threads).

Feed text is untrusted (prompt injection is allowed in this game): a learning is the ONLY thing
the feed may change, and only through these fields. Free text survives as quoted `text`, capped,
and never as an instruction. Every learning names its evidence (feed event ids) and, when it is a
blocker, the tick it expires at (`until_tick`, exclusive: the dealer is free again AT that tick).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SubjectKind = Literal["dealer", "venue", "team", "organiser"]
Kind = Literal[
    "blocker",  # a dealer will not deal with us (locked level, a lock after bad treatment)
    "cooloff",  # a dealer sent a team away until a tick
    "quota",  # a dealer's hourly allotment for a team is used up
    "sold_out",  # a dealer cannot sell that item / rarity this hour
    "price_floor",  # where a dealer stops conceding
    "behaviour",  # how a trader behaves (strikes, finals, duel outcomes)
    "rule_change",  # the clock, limits, rounds
    "fee_change",  # a venue's fee notice
    "announcement",  # levels, venues, organiser notices
]
Source = Literal["rules", "llm"]
BLOCKING_KINDS: frozenset[str] = frozenset({"blocker", "cooloff", "quota", "sold_out"})
SUBJECT_PATTERN = r"^[A-Za-z0-9_.:\-]{1,64}$"
TEXT_MAX = 300
EVIDENCE_MAX = 20
# detail fields that tell two facts about the same subject apart (an aggregate keeps one row per item)
IDENTITY_FIELDS = frozenset({"item", "rarity", "code", "aggregate", "venue", "effective_tick"})
# Where a rules blocker may come from (matched with `fullmatch`).
ORIGIN_THAT_BLOCKS = re.compile(r"(feed|refusal|thread:\d+)")


class Learning(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_kind: SubjectKind
    subject: str = Field(pattern=SUBJECT_PATTERN)  # dealer id, venue id, team id, "organiser"
    kind: Kind
    tick: int = Field(ge=0, le=2**31 - 1)  # the game tick of the evidence
    until_tick: int | None = Field(default=None, ge=0, le=2**31 - 1)  # expiry (exclusive); None = no expiry
    team: str | None = Field(default=None, pattern=SUBJECT_PATTERN)  # whom it binds; None = everyone
    evidence: tuple[int, ...] = ()  # feed event ids
    confidence: float = Field(ge=0.0, le=1.0)
    text: str = Field(min_length=1, max_length=TEXT_MAX)
    source: Source = "rules"
    detail: dict[str, Any] = Field(default_factory=dict)  # item, rarity, fee_bps, thread, code, ...

    @field_validator("text", mode="before")
    @classmethod
    def _one_line(cls, value: object) -> object:
        """Feed text is quoted data: one line, no control characters, capped."""
        if not isinstance(value, str):
            return value
        cleaned = " ".join("".join(ch if ch.isprintable() else " " for ch in value).split())
        return cleaned[: TEXT_MAX - 1] + "…" if len(cleaned) > TEXT_MAX else cleaned

    @model_validator(mode="after")
    def _blockers_expire(self) -> Learning:
        """A blocker without an expiry would hold forever: refused."""
        if self.kind in BLOCKING_KINDS and self.until_tick is None:
            raise ValueError(f"a {self.kind} learning needs until_tick")
        return self

    @field_validator("evidence", mode="before")
    @classmethod
    def _evidence(cls, value: object) -> object:
        if isinstance(value, list | tuple | set | frozenset):
            ids = sorted({int(v) for v in value})
            return tuple(ids[-EVIDENCE_MAX:])
        return value

    @property
    def blocking(self) -> bool:
        return self.kind in BLOCKING_KINDS

    def active(self, tick: int) -> bool:
        """Still in force at `tick`: no expiry, or the expiry tick is still ahead."""
        return self.until_tick is None or tick < self.until_tick

    def key(self) -> str:
        """The dedupe key: the same fact read twice (two processes, a restart) is one row.

        A fact is its subject, kind, whom it binds, its expiry and what it is about (`detail`'s
        identifying fields), not the wording or the confidence."""
        about = {k: self.detail[k] for k in sorted(self.detail) if k in IDENTITY_FIELDS}
        raw: list[object] = [self.subject_kind, self.subject, self.kind, self.team, self.until_tick, about]
        notice = self.kind in ("announcement", "rule_change") and "aggregate" not in self.detail
        if notice or (not about and self.until_tick is None):
            raw.append(list(self.evidence[:1]))  # a notice is its own event
        return hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()[:32]
