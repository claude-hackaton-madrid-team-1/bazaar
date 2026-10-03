# W3 simulator end-to-end harnesses

These need the #55 simulator (`origin/ogarciarevett/feat-bazaar-sim`), which this repo does not ship.
To run them, make a scratch checkout with the simulator merged, copy these files into its `tests/`, and
run them with pytest. The numbers in `docs/night/w3-ladder.md` came from these runs.

- `test_w3_e2e.py`: `bazaar dealer buy --live` with the W3 plans. Run: `W3_E2E_OUT=out.json`.
- `test_w3_taker.py`: the desk with `ladder_floor_quantile`. Run: `W3_Q=0.5|0 W3_MERGED=<feed jsonl> W3_OUT=… W3_LOG=…`.
- `test_w3_chato.py`: `dealer buy --dealer chato` under `dealer_price_caps`. Run: `W3_CAPS=chato:uncommon=31|none W3_OUT=…`.
- `test_w3_desk_chato.py`: the desk with Chato first (`ladder_level_deals` 3, `chato:uncommon=31`). Run: `W3_MERGED=… W3_OUT=… W3_LOG=…`.

`W3_MERGED` is a public feed capture (JSONL), loaded so the desk has Friday's floors.
