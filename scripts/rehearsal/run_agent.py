"""Run one `bazaar` command against the rehearsal's simulator.

Only two things differ from `uv run bazaar ...`:
- `BAZAAR_SIM=local` points at REHEARSAL_SIM_URL (a free localhost port) instead of 127.0.0.1:8765,
  which another worker holds tonight;
- REHEARSAL_GUARDRAILS, when set, is the GUARDRAILS.md copy every reader uses (only
  `venue_open_after_game_hours` differs from the committed file, so the venue opens within the run).
sitecustomize from scripts/sim_guard is first on PYTHONPATH: any non-loopback connection raises.
"""

import os
import sys
from pathlib import Path

import bazaar_agent.config as config

url = os.environ["REHEARSAL_SIM_URL"]
assert url.startswith(("http://127.0.0.1:", "http://[::1]:")), url
config.LOCAL_SIM_URL = url

rules_copy = os.environ.get("REHEARSAL_GUARDRAILS")
if rules_copy:
    import bazaar_agent.guardrails as gr

    path = Path(rules_copy)
    gr.GUARDRAILS_FILE = path
    gr.load_guardrails.__defaults__ = (path,)
    gr.parse_guardrails.__defaults__ = (path,)

from bazaar_agent.cli import app  # noqa: E402

sys.argv = ["bazaar", *sys.argv[1:]]
app()
