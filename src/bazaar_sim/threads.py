"""Conversations: with a dealer (`kind: persona`) or with another team on a venue (`kind: team`).

A team message lands now; the dealer answers at the next tick (`dealer_turn`), as the real feed
shows. One open conversation per dealer, six in all, one message per conversation per tick.
A dealer conversation that ends says why in `closed_reason` (`walked`, `idle`, `cooloff`, ...).
"""

from __future__ import annotations

from typing import Any

from bazaar_sim import catalog, dealers, validate
from bazaar_sim.errors import SimError, invalid, not_found, wait_for_tick
from bazaar_sim.models import Message, Negotiation, Side, Thread
from bazaar_sim.views import item_name, offer_view
from bazaar_sim.world import World

OFFER_TTL_TICKS = 2  # a structured offer inside a conversation stands for two ticks
CHATO_RARE_STOCK = 2  # rares per team per game hour


# ---------------------------------------------------------------- opening a conversation


def open_thread(w: World, team_id: str, body: dict[str, Any]) -> Thread:
    counterpart = body.get("with")
    if not isinstance(counterpart, str) or not counterpart:
        raise invalid("`with` must name a dealer (abuela) or a team (t03)")
    open_now = [t for t in w.state.threads.values() if t.status == "open" and team_id in (t.team, t.with_)]
    if len(open_now) >= w.limit("max_open_threads_per_team"):
        raise SimError("too_many_threads", f"at most {w.limit('max_open_threads_per_team')} open conversations", 400)
    if counterpart in catalog.raw_dealers():
        if counterpart in w.state.disabled_dealers:
            raise SimError("dealer_closed", f"{counterpart} has closed the stall", 400)
        return _open_dealer_thread(w, team_id, counterpart, body.get("topic"), open_now)
    if counterpart in w.state.teams:
        return _open_team_thread(w, team_id, counterpart, body)
    raise not_found(f"dealer or team {counterpart!r}")


def _open_dealer_thread(w: World, team_id: str, dealer_id: str, topic: Any, open_now: list[Thread]) -> Thread:
    team = w.team(team_id)
    if dealer_id not in team.unlocked and not w.open_to_all(dealer_id):
        raise SimError("locked", f"{dealer_id} is not unlocked for you yet (see GET /api/dealers)", 403)
    until = team.cooloff.get(dealer_id, 0)
    if until > w.tick:
        raise SimError("cooloff", f"{dealer_id} is not dealing with you until tick {until}", 403, until_tick=until)
    if any(t.with_ == dealer_id for t in open_now):
        raise SimError("thread_exists", f"one open conversation per dealer: close the one with {dealer_id}", 400)
    data = catalog.raw_dealers()[dealer_id]
    deals_now = [d for d in team.deals if d.dealer == dealer_id and d.hour == w.hour]
    if len(deals_now) >= int(data["menu"]["deals_per_team_per_hour"]):
        raise SimError("persona_quota", f"{dealer_id} has done all its deals with you this game hour", 429)
    if not isinstance(topic, dict):
        raise invalid('a dealer conversation needs a topic, e.g. {"buy": {"pack": "sobre_barrio"}}')
    style = w.style(dealer_id)
    neg = _negotiation(w, team_id, dealer_id, data, topic, style)
    team.mood[dealer_id] = dealers.carried_mood(style, team.mood.get(dealer_id, 0.0))
    th = Thread.model_validate(
        {
            "id": w.next_id("thread"),
            "kind": "persona",
            "team": team_id,
            "with": dealer_id,
            "topic": topic,
            "created_tick": w.tick,
            "last_activity_tick": w.tick,
            "neg": neg,
        }
    )
    w.state.threads[th.id] = th
    w.emit("thread.opened", {"thread": th.id, "kind": "persona", "team": team_id, "with": dealer_id, "topic": topic})
    return th


def _negotiation(
    w: World, team_id: str, dealer_id: str, data: dict[str, Any], topic: dict[str, Any], style: dealers.Style
) -> Negotiation:
    rng = w.rng("negotiation", team_id, dealer_id, w.state.counters.get("thread", 0))
    if isinstance(topic.get("sell"), dict):
        return _sell_topic(w, team_id, dealer_id, data, topic["sell"], style, rng)
    buy = topic.get("buy")
    if not isinstance(buy, dict):
        raise invalid('topic must be {"buy": {...}} or {"sell": {"assets": [...]}}')
    if isinstance(buy.get("pack"), str):
        pack = buy["pack"]
        item = catalog.dealer_menu_sells(data, pack=pack)
        if item is None:
            raise invalid(f"{dealer_id} does not sell {pack}")
        team = w.team(team_id)
        bought = [d for d in team.deals if d.dealer == dealer_id and d.item == pack and d.hour == w.hour]
        if len(bought) >= int(item.get("per_team_per_hour", 99)):
            raise SimError(
                "persona_quota", f"{dealer_id} sells you {item['per_team_per_hour']} {pack} per game hour", 429
            )
        return dealers.start(
            style,
            side="sell",
            item=pack,
            item_kind="pack",
            rarity=None,
            list_price=int(item["list_price"]),
            opening=int(item.get("opening_ask") or item["list_price"]),
            assets=[],
            rng=rng,
        )
    ref = _card_for(w, team_id, dealer_id, data, buy, rng)
    card = catalog.cards()[ref]
    menu = catalog.dealer_menu_sells(data, rarity=card.rarity)
    assert menu is not None
    return dealers.start(
        style,
        side="sell",
        item=ref,
        item_kind="card",
        rarity=card.rarity,
        list_price=int(menu["list_price"]),
        opening=None,
        assets=[],
        rng=rng,
    )


def _card_for(w: World, team_id: str, dealer_id: str, data: dict[str, Any], buy: dict[str, Any], rng: Any) -> str:
    released = catalog.released_sets()
    extra_rarities = ("epic", "legendary") if w.scenario else ()
    if isinstance(buy.get("card"), str):
        card = catalog.card(buy["card"])
        if card is None or card.set_code not in released or not (card.page or card.rarity in extra_rarities):
            raise invalid(f"{buy['card']} is not a card on sale")
        candidates = [card.ref]
    else:
        rarity, code = buy.get("rarity"), buy.get("set")
        if rarity is None and code is None:
            raise invalid('a buy topic names a "pack", a "card", or a "rarity" and/or "set"')
        candidates = [
            c.ref
            for c in catalog.cards().values()
            if (c.page or c.rarity in extra_rarities)
            and c.set_code in released
            and rarity in (None, c.rarity)
            and code in (None, c.set_code)
        ]
        candidates = [r for r in candidates if catalog.dealer_menu_sells(data, rarity=catalog.cards()[r].rarity)]
        if not candidates:
            raise invalid(f"{dealer_id} sells no {rarity or ''} card {code or ''}".replace("  ", " "))
    rarity_of = catalog.cards()[candidates[0]].rarity
    if catalog.dealer_menu_sells(data, rarity=rarity_of) is None:
        raise invalid(f"{dealer_id} does not sell {rarity_of} cards")
    on_sale = [r for r in candidates if w.mintable(r)]
    if not on_sale:
        raise SimError("sold_out", f"{candidates[0]} is out of print", 400)
    if dealer_id == "chato" and rarity_of == "rare":
        team = w.team(team_id)
        rares = [d for d in team.deals if d.dealer == "chato" and d.hour == w.hour and catalog.card(d.item)]
        if sum(1 for d in rares if catalog.cards()[d.item].rarity == "rare") >= CHATO_RARE_STOCK:
            raise SimError("sold_out", f"chato sells you {CHATO_RARE_STOCK} rares per game hour", 400)
    held = w.held_counts(team_id)
    missing = [r for r in on_sale if held.get(r, 0) == 0]
    return str(rng.choice(missing or on_sale))


def _sell_topic(
    w: World,
    team_id: str,
    dealer_id: str,
    data: dict[str, Any],
    sell: dict[str, Any],
    style: dealers.Style,
    rng: Any,
) -> Negotiation:
    ids = validate.int_list(sell.get("assets"), "topic.sell.assets")
    if not ids:
        raise invalid("a sell topic lists the asset ids you sell")
    book = 0
    bid = 0.0
    extra_rarities = ("epic", "legendary") if w.scenario else ()
    for aid in ids:
        asset = w.asset(aid)
        if asset.owner != team_id:
            raise SimError("not_owner", f"asset {aid} is not yours", 403)
        card = catalog.card(asset.ref)
        if (
            card is None
            or not (card.page or card.rarity in extra_rarities)
            or not catalog.dealer_buys(data, card.rarity, card.set_code)
        ):
            raise invalid(f"{dealer_id} does not buy {asset.ref}")
        book += card.book
        fever = w.scenario.fever_mult(w, dealer_id, card.set_code, card.rarity) if w.scenario else 1.0
        bid += card.book * dealers.buy_share(style, card.set_code, card.rarity) * fever
    first = catalog.cards()[w.asset(ids[0]).ref]
    return dealers.start(
        style,
        side="buy",
        item=first.ref,
        item_kind="card",
        rarity=first.rarity,
        list_price=book,
        opening=None,
        assets=ids,
        rng=rng,
        bid=round(bid),
    )


def _open_team_thread(w: World, team_id: str, other: str, body: dict[str, Any]) -> Thread:
    if other == team_id:
        raise invalid("you cannot talk to yourself")
    venue = body.get("venue") or "rastro"
    v = w.state.venues.get(venue) if isinstance(venue, str) else None
    if v is None or v.status != "open":
        raise SimError("venue_not_live", f"venue {venue!r} is not open", 400)
    topic = validate.topic_between_teams(body.get("topic"))
    th = Thread.model_validate(
        {
            "id": w.next_id("thread"),
            "kind": "team",
            "team": team_id,
            "with": other,
            "topic": topic,
            "venue": venue,
            "created_tick": w.tick,
            "last_activity_tick": w.tick,
        }
    )
    w.state.threads[th.id] = th
    w.emit("thread.opened", {"thread": th.id, "kind": "team", "team": team_id, "with": other, "topic": topic})
    return th


# ---------------------------------------------------------------- messages


def participant_thread(w: World, team_id: str, tid: int) -> Thread:
    th = w.state.threads.get(tid)
    if th is None or team_id not in (th.team, th.with_):
        raise not_found(f"thread {tid}")
    return th


def say(w: World, team_id: str, tid: int, body: dict[str, Any]) -> dict[str, Any]:
    th = participant_thread(w, team_id, tid)
    if th.status != "open":
        raise SimError("thread_closed", f"thread {tid} is {th.status}", 400)
    if any(o.thread == tid and o.status == "accepted" for o in w.state.offers.values()):
        raise SimError("deal_pending", f"a deal in thread {tid} settles at the next tick", 400)
    text = validate.clean_text(body.get("text"))
    cap = w.limit("messages_per_side_per_tick")
    if w.used(team_id, f"msg:{tid}") >= cap:
        raise wait_for_tick("one message per conversation per tick", w.tick + 1)
    if th.kind == "persona":
        offer_id = _dealer_bid(w, team_id, th, validate.optional_price(body.get("price")))
    else:
        offer_id = _team_offer(w, team_id, th, body.get("offer"))
    w.use(team_id, f"msg:{tid}", cap, "one message per conversation per tick")  # checked above: never refuses
    message = post_message(w, th, team_id, text or None, offer_id, public_text=False)
    if th.neg is not None:
        th.neg = th.neg.model_copy(update={"pending_reply": True})
        team = w.team(team_id)
        team.mood[th.with_] = min(dealers.MOOD_CAP, team.mood.get(th.with_, 0.0) + dealers.mood_delta(text))
    return {"ok": True, "message": message.id, "offer": offer_id, "tick": w.tick}


def _dealer_bid(w: World, team_id: str, th: Thread, price: int | None) -> int | None:
    from bazaar_sim.market import create_offer

    neg = th.neg
    if price is None or neg is None:
        return None
    if neg.side == "sell":
        if price > w.team(team_id).cash:
            raise SimError("insufficient_cash", f"you have {w.team(team_id).cash} P, not {price}", 400)
        give, want = Side(cash=price), Side(types=[f"{neg.item_kind}:{neg.item}"])
    else:
        for aid in neg.assets:
            if w.asset(aid).owner != team_id:
                raise SimError("not_owner", f"asset {aid} is no longer yours", 403)
        give, want = Side(assets=list(neg.assets)), Side(cash=price)
    _retire(w, th, team_id, "replaced")
    offer = create_offer(w, team_id, th.with_, None, th.id, give, want, OFFER_TTL_TICKS)
    return offer.id


def _team_offer(w: World, team_id: str, th: Thread, raw: Any) -> int | None:
    from bazaar_sim.market import offer_from_input

    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise invalid("offer must be {give, want}")
    other = th.with_ if team_id == th.team else th.team
    offer = offer_from_input(
        w, team_id, {"give": raw.get("give"), "want": raw.get("want"), "venue": th.venue, "to": other}, thread=th.id
    )
    return offer.id


def post_message(
    w: World, th: Thread, sender: str, text: str | None, offer_id: int | None, *, public_text: bool
) -> Message:
    message = Message(id=w.next_id("message"), tick=w.tick, sender=sender, text=text, offer=offer_id)
    th.messages.append(message)
    th.last_activity_tick = w.tick
    capped = th.kind == "team" and th.status == "open" and len(th.messages) >= w.limit("messages_per_team_thread")
    offer = w.state.offers.get(offer_id) if offer_id is not None else None
    w.emit(
        "thread.message",
        {
            "thread": th.id,
            "kind": th.kind,
            "message": message.id,
            "sender": sender,
            "text": text if public_text else None,  # a team's words are private; a dealer's are public
            "team": th.team,
            "with": th.with_,
            "offer": offer_view(w, offer) if offer is not None else None,
        },
        actor=sender,
    )
    if capped:  # RULES.md: a conversation between two teams ends in a deal or after 200 messages
        end(w, th, "closed", "message_cap")
    return message


def _retire(w: World, th: Thread, maker: str, status: str) -> None:
    for o in w.state.offers.values():
        if o.thread == th.id and o.maker == maker and o.status == "open":
            o.status = status  # type: ignore[assignment]


def close_thread(w: World, team_id: str, tid: int, reason: str = "closed") -> dict[str, Any]:
    th = participant_thread(w, team_id, tid)
    if th.status == "open":
        end(w, th, "closed", reason)
    return {"ok": True, "thread": tid, "status": th.status}


def end(w: World, th: Thread, status: str, reason: str, until_tick: int | None = None) -> None:
    th.status = status  # type: ignore[assignment]
    th.closed_reason = reason
    th.until_tick = until_tick
    for o in w.state.offers.values():
        if o.thread == th.id and o.status == "open":
            o.status = "cancelled"
    w.emit("thread.closed", {"thread": th.id, "kind": th.kind, "team": th.team, "with": th.with_, "reason": reason})


# ---------------------------------------------------------------- the dealer's turn (each tick)


def dealer_turn(w: World) -> None:
    pending = {o.thread for o in w.state.offers.values() if o.status == "accepted"}
    for th in list(w.state.threads.values()):
        if th.status != "open" or th.kind != "persona" or th.neg is None or not th.neg.pending_reply:
            continue
        if th.id not in pending:  # an accepted deal settles first; nothing to answer meanwhile
            _answer(w, th)


def _latest_team_move(w: World, th: Thread) -> tuple[int | None, str | None]:
    for m in reversed(th.messages):
        if m.sender == th.team:
            offer = w.state.offers.get(m.offer) if m.offer is not None else None
            live = offer is not None and offer.status == "open"
            cash = (offer.give.cash or offer.want.cash) if offer is not None and live else None
            return (cash or None), m.text
        if m.sender == th.with_:
            break
    return None, None


def _answer(w: World, th: Thread) -> None:
    from bazaar_sim.market import create_offer

    assert th.neg is not None
    dealer_id, team = th.with_, w.team(th.team)
    style = w.style(dealer_id)
    mood = team.mood.get(dealer_id, 0.0)
    if mood <= style.cooloff_at:
        until = w.tick + style.cooloff_ticks
        team.cooloff[dealer_id] = until
        team.mood[dealer_id] = 0.0
        post_message(w, th, dealer_id, "That is no way to talk. Come back later.", None, public_text=True)
        end(w, th, "cooloff", "cooloff", until)
        w.emit("persona.cooloff", {"team": th.team, "persona": dealer_id, "until_tick": until}, actor=dealer_id)
        return
    price, text = _latest_team_move(w, th)
    rng = w.rng("reply", th.id, len(th.messages))
    answer = dealers.reply(style, th.neg, price, text, mood, rng, item_name(th.neg.item))
    th.neg = answer.neg
    if answer.kind == "accept":
        _dealer_accepts(w, th, answer.text)
        return
    if answer.kind == "walk":
        post_message(w, th, dealer_id, answer.text, None, public_text=True)
        end(w, th, "walked", "walked")
        return
    _retire(w, th, dealer_id, "replaced")
    assert answer.price is not None
    neg = th.neg
    if neg.side == "sell":
        give, want = Side(types=[f"{neg.item_kind}:{neg.item}"]), Side(cash=answer.price)
    else:
        give, want = Side(cash=answer.price), Side(assets=list(neg.assets))
    final = answer.kind == "final"
    offer = create_offer(w, dealer_id, th.team, None, th.id, give, want, OFFER_TTL_TICKS, final=final)
    if final:
        th.neg = neg.model_copy(update={"final_tick": w.tick})
    post_message(w, th, dealer_id, answer.text, offer.id, public_text=True)


def _dealer_accepts(w: World, th: Thread, text: str) -> None:
    """The dealer takes OUR standing offer. It settles in the same tick as her "Deal!" (real feed,
    2026-10-02: 13 of 13 Abuela deals settled in the tick of that message): the team's offer was made
    a tick earlier, so it still settles on the tick after it was posted."""
    from bazaar_sim.market import settle_now

    ours = [o for o in w.state.offers.values() if o.thread == th.id and o.maker == th.team and o.status == "open"]
    if not ours:
        return
    offer = ours[-1]
    offer.status = "accepted"
    offer.accepted_by = th.with_
    offer.accepted_tick = w.tick
    _retire(w, th, th.with_, "replaced")
    post_message(w, th, th.with_, text, None, public_text=True)
    settle_now(w, offer)


# ---------------------------------------------------------------- housekeeping and levels


def housekeeping(w: World) -> None:
    for th in list(w.state.threads.values()):
        if th.status != "open":
            continue
        pending = any(o.thread == th.id and o.status == "accepted" for o in w.state.offers.values())
        if pending:
            continue
        neg = th.neg
        if neg is not None and neg.final and neg.final_tick is not None and w.tick > neg.final_tick + OFFER_TTL_TICKS:
            post_message(w, th, th.with_, dealers.reply_walk_text(th.with_), None, public_text=True)
            end(w, th, "walked", "walked")
        elif w.tick - th.last_activity_tick > w.config.idle_ticks:
            end(w, th, "closed", "idle")


GATED = ("chato", "pilar")  # dealers that open early to teams that earned them, and to everyone at a set tick


def levels_tick(w: World) -> None:
    for dealer_id in (d for d in GATED if d in catalog.raw_dealers()):
        _level_tick(w, dealer_id)


def _level_tick(w: World, dealer_id: str) -> None:
    data = catalog.raw_dealers()[dealer_id]
    open_tick = w.open_to_all_tick(dealer_id)
    assert open_tick is not None
    if w.tick == 1:
        w.emit(
            "level.activated",
            {
                "level": dealer_id,
                "kind": "persona",
                "name": data["name"],
                "teaser": data["teaser"],
                "how": data["how"],
                "opens_to_all_in_hours": round(open_tick * w.game_tick_seconds / 3600.0, 3),
            },
            actor="admin",
        )
    if w.tick == open_tick:
        w.emit("persona.open_to_all", {"persona": dealer_id, "name": data["name"], "level": int(data["level"])})
        for team in w.state.teams.values():
            if dealer_id not in team.unlocked:
                team.unlocked.append(dealer_id)


def maybe_unlock(w: World, team_id: str) -> None:
    """A few negotiated deals with the dealer before unlock the next one early (a deal at its opening price does
    not count): Abuela -> El Chato, El Chato -> Doña Pilar."""
    for dealer_id in GATED:
        _maybe_unlock_one(w, team_id, dealer_id)


def _maybe_unlock_one(w: World, team_id: str, dealer_id: str) -> None:
    team = w.team(team_id)
    data = catalog.raw_dealers()[dealer_id]
    rule = data["unlock"]
    before = rule["early_deals_with"]
    negotiated = [d for d in team.deals if d.dealer == before and d.negotiated]
    if dealer_id not in team.unlocked and len(negotiated) >= int(rule["early_min_deals"]):
        team.unlocked.append(dealer_id)
        w.emit(
            "level.unlocked",
            {
                "team": team_id,
                "name": team.name,
                "persona": dealer_id,
                "persona_name": data["name"],
                "level": len(team.unlocked),
                "why": f"{len(negotiated)} deals with {before}",
            },
        )
