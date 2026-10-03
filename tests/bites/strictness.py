"""Strict xfails only on demand: `BITES_STRICT=1 uv run pytest tests/bites` turns a fixed bite (XPASS) into a
failure, so its marker gets dropped. Without it the suite stays green on any branch, including after #60, #62
and #72 are merged (their fixes make some of these tests XPASS)."""

import os

STRICT = os.environ.get("BITES_STRICT") == "1"
