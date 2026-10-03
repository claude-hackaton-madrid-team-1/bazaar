"""A simulated Bazaar: an HTTP API that behaves like https://bazaar.causaprima.ai for testing our agents.

Run it with `uv run bazaar-sim serve`. It never talks to the real game and refuses any team key that
does not start with `sim-` (see `bazaar_sim.auth`).
"""

__version__ = "0.1.0"
