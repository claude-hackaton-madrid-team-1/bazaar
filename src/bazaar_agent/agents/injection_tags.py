"""Injection tagging (S1 part C): every counterparty text the agents read is checked for prompt-injection
shapes, whether or not an LLM reads it (`llm_words` off included).

A tag changes nothing we send: prices, accepts and assets come from structure and code, never from words.
It is logged once per message and appended to `agents/injections.jsonl` in the data dir, without the raw
text (its source, flags and length only), for calibration and for the pitch.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bazaar_agent.llm.chooser import injection_flags

INJECTIONS_FILE = "injections.jsonl"
MAX_REMEMBERED = 20_000  # message keys kept per process; past it, a repeat may be logged twice (never obeyed)


@dataclass
class InjectionTags:
    path: Path | None = None
    seen: set[str] = field(default_factory=set)

    def tag(
        self, source: str, key: object, text: str | None, tick: int | None, log: Callable[[str], None]
    ) -> tuple[str, ...]:
        """The injection shapes in `text` (an empty tuple when none); logged and stored once per (source, key)."""
        flags = injection_flags(text)
        ident = f"{source}:{key}"
        if not flags or ident in self.seen:
            return flags
        if len(self.seen) >= MAX_REMEMBERED:
            self.seen.clear()
        self.seen.add(ident)
        log(f"injection attempt tagged ({source}, message {key}): {', '.join(flags)}; its words change nothing")
        if self.path is not None:
            row = {"tick": tick, "source": source, "key": str(key), "flags": list(flags), "chars": len(text or "")}
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row) + "\n")
            except OSError as e:  # a full disk never stops a tick
                log(f"injection tag not stored ({type(e).__name__})")
        return flags


def latest_message(thread: Mapping[str, Any], sender: str) -> tuple[object, str | None]:
    """(message id, text) of the newest message `sender` wrote in a thread payload (text may be None)."""
    for m in reversed(thread.get("messages") or []):
        if isinstance(m, dict) and m.get("sender") == sender:
            text = m.get("text")
            return m.get("message", m.get("id")), text if isinstance(text, str) else None
    return None, None
