"""PR #131 proof: against a dealer whose price moves only on our structured step (RULES: "their prices come
from their own rules"; "prompt injection ... changes what they say, never their prices"), every bluff scores
the same as the plain control arm, so neither the no-gain rule nor UCB1 ever stops the bluffs: Chato receives
every bluff tactic repeatedly, all day, with zero possible upside and only cooloff downside."""

from collections import Counter

from bazaar_agent.agents.bluff import TacticBook, Counterparty
from bazaar_agent.agents.tactics import BY_ID


def test_bluffs_keep_going_to_a_price_only_dealer():
    book = TacticBook(env={}, seed=1, us="t01")
    chato = Counterparty.dealer("chato")
    sent = Counter()
    tick, ask = 100, 40
    for thread in range(10):  # ten threads in one game day
        conv = f"thread:{thread}"
        ask, bid = 40, 20
        for step in range(6):
            book.begin_tick(tick, 1)
            c = book.choose(chato, "buy", conv, step, bid, their_price=ask)
            book.sent(c, their_price=ask, their_offer=tick, tick=tick)
            sent[c.tactic] += 1
            tick += 1
            ask -= 2  # the dealer concedes because our PRICE stepped, whatever the words
            bid += 2
            book.observe(conv, their_price=ask, their_offer=tick, tick=tick)
        book.dropped(conv)
    bluffs = {t: n for t, n in sent.items() if t in BY_ID and BY_ID[t].family == "bluff"}
    print(dict(sent))
    assert sum(bluffs.values()) >= 20  # FAILS once bluffs are off by default for dealers (the fix)
