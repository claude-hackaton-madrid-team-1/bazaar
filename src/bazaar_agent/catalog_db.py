"""The card catalog in Postgres (`cards`): every card of every set, from the public `GET /api/catalog`.

Written by whoever already read the catalog (the agents read it every tick, the MCP server every
`backend.CATALOG_TICKS`), so it costs no extra game call. `CatalogSync` writes it the first time, when
the released sets change (El Retiro on Saturday, Chamberí on Sunday) and at most every `REFRESH_TICKS`
ticks otherwise, for `minted` (packs mint copies all game). A row never moves back to an older tick.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from typing import Any

import psycopg
from pydantic import BaseModel, ConfigDict, Field, ValidationError

REFRESH_TICKS = 10  # `minted` moves as packs open: rewrite the rows at most this often
CARD_REF = r"^[A-Z]{3}-\d{2}$"


class CatalogCard(BaseModel):
    """One card as `/api/catalog` lists it (validated: the catalog is external input)."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(pattern=CARD_REF)
    name: str | None = Field(default=None, max_length=200)
    rarity: str = Field(max_length=20)
    flavour: str | None = Field(default=None, max_length=1200)
    book: float | None = Field(default=None, ge=0)
    print_run: int | None = Field(default=None, ge=0)
    minted: int | None = Field(default=None, ge=0)
    hidden: bool = False
    page: bool = False


@dataclass(frozen=True)
class CardRow:
    id: str
    set_code: str
    set_name: str | None
    name: str | None
    rarity: str
    book: float | None
    print_run: int | None
    minted: int | None
    released: bool
    page: bool
    hidden: bool
    flavour: str | None


def card_rows(catalog: dict[str, Any]) -> list[CardRow]:
    """Every valid card of every set, sorted by ref. A malformed entry is skipped, never stored."""
    rows: dict[str, CardRow] = {}
    for s in catalog.get("sets") or []:
        if not isinstance(s, dict) or not isinstance(s.get("id"), str):
            continue
        for raw in s.get("cards") or []:
            try:
                c = CatalogCard.model_validate(raw)
            except ValidationError:
                continue
            name = s.get("name") if isinstance(s.get("name"), str) else None
            rows[c.id] = CardRow(
                c.id, s["id"], name, c.name, c.rarity, c.book, c.print_run, c.minted,
                bool(s.get("released")), c.page, c.hidden, c.flavour,
            )  # fmt: skip
    return [rows[ref] for ref in sorted(rows)]


def released_sets(catalog: dict[str, Any]) -> frozenset[str]:
    return frozenset(str(s.get("id")) for s in catalog.get("sets") or [] if isinstance(s, dict) and s.get("released"))


def save_catalog(conn: psycopg.Connection, catalog: dict[str, Any], tick: int) -> int:
    """Upsert every card at `tick`; a writer at an older tick never overwrites a newer row. Rows in key order."""
    rows = card_rows(catalog)
    with conn.cursor() as cur:
        cur.executemany(
            "insert into cards (id, set_code, set_name, name, rarity, book, print_run, minted, released, page, "
            "hidden, flavour, updated_tick) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "on conflict (id) do update set set_code = excluded.set_code, set_name = excluded.set_name, "
            "name = excluded.name, rarity = excluded.rarity, book = excluded.book, print_run = excluded.print_run, "
            "minted = greatest(excluded.minted, cards.minted), released = excluded.released, page = excluded.page, "
            "hidden = excluded.hidden, flavour = excluded.flavour, updated_tick = excluded.updated_tick "
            "where cards.updated_tick is null or excluded.updated_tick >= cards.updated_tick",
            [(*astuple_row(r), tick) for r in rows],
        )
    conn.commit()
    return len(rows)


def astuple_row(r: CardRow) -> tuple[Any, ...]:
    return (r.id, r.set_code, r.set_name, r.name, r.rarity, r.book, r.print_run, r.minted, r.released, r.page,
            r.hidden, r.flavour)  # fmt: skip


CARD_COLUMNS = ("ref", "set", "set_name", "name", "rarity", "book", "print_run", "minted", "released", "page",
                "hidden", "updated_tick")  # fmt: skip


def read_cards(
    conn: psycopg.Connection, set_code: str | None = None, rarity: str | None = None, ref: str | None = None
) -> list[dict[str, Any]]:
    """The stored catalog, filtered, ordered by ref (`updated_tick` says how fresh each row is)."""
    rows = conn.execute(
        "select id, set_code, set_name, name, rarity, book, print_run, minted, released, page, hidden, updated_tick "
        "from cards where (%(set)s::text is null or set_code = %(set)s) "
        "and (%(rarity)s::text is null or rarity = %(rarity)s) and (%(ref)s::text is null or id = %(ref)s) "
        "order by id",
        {"set": set_code, "rarity": rarity, "ref": ref},
    ).fetchall()
    conn.commit()
    return [_card_dict(row) for row in rows]


def _card_dict(row: Iterable[Any]) -> dict[str, Any]:
    out = dict(zip(CARD_COLUMNS, row, strict=True))
    out["book"] = float(out["book"]) if out["book"] is not None else None
    return out


def rows_as_dicts(rows: Iterable[CardRow], tick: int | None) -> list[dict[str, Any]]:
    """`card_rows` in the shape `read_cards` answers (the live fallback when Postgres is down)."""
    out = []
    for r in rows:
        d = asdict(r)
        out.append({"ref": d.pop("id"), "set": d.pop("set_code"), **d, "updated_tick": tick})
    for d in out:
        d.pop("flavour", None)
    return out


class CatalogSync:
    """Writes the catalog a process already read into `cards`: first time, on a release, every N ticks."""

    def __init__(self, write: Callable[[dict[str, Any], int], int], every: int = REFRESH_TICKS) -> None:
        self._write, self.every = write, every
        self._last_tick: int | None = None
        self._released: frozenset[str] | None = None

    def due(self, tick: int, catalog: dict[str, Any]) -> bool:
        if self._last_tick is None or tick < self._last_tick:
            return True
        return released_sets(catalog) != self._released or tick - self._last_tick >= self.every

    def observe(self, tick: int, catalog: dict[str, Any]) -> int | None:
        """Rows written, or None when nothing was due. A failed write is retried on the next observe."""
        if not self.due(tick, catalog):
            return None
        written = self._write(catalog, tick)
        self._last_tick, self._released = tick, released_sets(catalog)
        return written
