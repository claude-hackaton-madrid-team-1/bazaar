# Arbitrage study, ticks 0–149

## The board

| venue | side | public plain offers |
|---|---|---:|
| rastro | ask | 462 |
| rastro | bid | 174 |

| side | left the board by | offers |
|---|---|---:|
| ask | cancelled | 43 |
| ask | expired | 356 |
| ask | filled | 28 |
| ask | open | 35 |
| bid | cancelled | 69 |
| bid | expired | 82 |
| bid | filled | 15 |
| bid | open | 8 |

Bids standing at least k ticks: ≥1 ticks 99%, ≥2 ticks 81%, ≥3 ticks 66%

## Crossings (ask + bid for one card, different makers, same tick; net ≥ 3 P)

| fees | overlapping pairs | gross > 0 | net ≥ min | executable | fit 1 accept/tick | net P |
|---|---:|---:|---:|---:|---:|---:|
| fees as charged | 67 | 2 | 0 | 0 | 0 | 0 |
| every fee at 0 (a 0 bps team venue) | 67 | 2 | 0 | 0 | 0 | 0 |

Tape exits (not executable, a ceiling): 1 asks cost less, fee included, than the most a team had paid for the card before; 1 P of margin in all.

## Duplicate buys (value of one more copy − ask − fee ≥ 3 P), per affinity tier

| tier | copy | asks | ≥ min | surplus P | best common | best uncommon | best rare |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1.6 | 2 | 462 | 0 | 0 | -2 | -14 | -42 |
| 1.6 | 3 | 462 | 0 | 0 | -4.4 | -20 | -58.8 |
| 1.3 | 2 | 462 | 0 | 0 | -2.8 | -15.9 | -47.2 |
| 1.3 | 3 | 462 | 0 | 0 | -4.7 | -20.8 | -60.9 |
| 1.1 | 2 | 462 | 0 | 0 | -3.2 | -17.1 | -50.8 |
| 1.1 | 3 | 462 | 0 | 0 | -4.9 | -21.2 | -62.3 |
| 0.9 | 2 | 462 | 0 | 0 | -3.8 | -18.4 | -54.2 |
| 0.9 | 3 | 462 | 0 | 0 | -5.1 | -21.8 | -63.7 |
| 0.7 | 2 | 462 | 0 | 0 | -4.2 | -19.6 | -57.8 |
| 0.7 | 3 | 462 | 0 | 0 | -5.3 | -22.2 | -65.1 |
| 0.5 | 2 | 462 | 0 | 0 | -4.8 | -20.9 | -61.2 |
| 0.5 | 3 | 462 | 0 | 0 | -5.5 | -22.8 | -66.5 |
