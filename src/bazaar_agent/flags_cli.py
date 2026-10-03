"""`bazaar flags`: the evidence for switching bad-faith flags on (S1 part B).

`bazaar flags precision` replays the flag rule over every dealer offer in the captured feed (merged with the
live window) and prints how often it would flag, per dealer. `--json` prints Jev's state for
`questions/flags.json` (`enable_bad_faith_flags`):

    uv run bazaar flags precision --json | uv run python -m bazaar_agent.jev judge --state - \\
        --questions questions/flags.json --log

Read-only: public reads only, no key, nothing is sent.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.padding import Padding
from rich.table import Table
from rich.text import Text

from bazaar_agent.agents.flag_evidence import dealer_offers, precision
from bazaar_agent.agents.inspector import CardIndex
from bazaar_agent.config import load_settings
from bazaar_agent.feed import DEFAULT_WINDOW, FeedStore, load_events
from bazaar_agent.guardrails import GuardrailsError, load_guardrails
from bazaar_agent.llm.chooser import HIDING_MARKS
from bazaar_agent.sdk import BazaarError, public_client

WORDS_SHOWN = 1_200  # a would-flag's words in full (the server's cap), for the human who decides on flag_dealers
flags_app = typer.Typer(no_args_is_help=True, help="Bad-faith flags: the flag rule's precision over the feed")
console = Console()
err_console = Console(stderr=True)


def printable(text: str) -> str:
    """A counterparty's words safe for a terminal: only printable characters (no escape sequence, direction
    mark or lone surrogate), the invisible "printable" blanks (braille blank, Hangul fillers) blanked too, and
    whitespace runs collapsed. `words_block` then indents EVERY wrapped line, so nothing can sit at column 0."""
    kept = "".join(ch if ch.isprintable() and ch not in HIDING_MARKS else " " for ch in text)
    return " ".join(kept.split())


def words_block(words: str) -> Padding:
    """A dealer's words as literal text (never markup), dimmed, every wrapped line indented by 4 columns."""
    return Padding(Text("» " + words, style="dim"), (0, 0, 0, 4))


@flags_app.command("precision")
def flags_precision(
    as_json: bool = typer.Option(False, "--json", help="Print Jev's state (questions/flags.json) on stdout"),
    feed_dir: str | None = typer.Option(None, help="A captured feed directory (default: this checkout's)"),
    live: bool = typer.Option(True, help="Merge the live public window (500 events) into the capture"),
) -> None:
    """How often the flag rule fires on every dealer offer we have seen, trusted dealers included."""
    try:
        rules = load_guardrails().rules
    except GuardrailsError as e:
        err_console.print(f"[red]GUARDRAILS.md is invalid: {escape(str(e))}[/red]")
        raise typer.Exit(1) from None
    settings = load_settings()
    client = public_client(settings)
    try:
        window = client.feed_window(DEFAULT_WINDOW) if live else None
        cards = CardIndex.from_catalog(client.catalog())
    except BazaarError as e:
        err_console.print(f"[red]public read refused: {escape(e.code)}[/red]")
        raise typer.Exit(1) from None
    events = load_events(FeedStore(Path(feed_dir) if feed_dir else settings.feed_dir), window)
    evidence = precision(dealer_offers(events), cards, rules.trusted_dealers, rules.flag_dealer_ids)
    if as_json:
        print(json.dumps(evidence.as_state(), sort_keys=True))
        return
    table = Table(title=f"Flag rule over {len(events)} feed events ({evidence.offers} dealer offers)")
    for column in ("dealer", "trusted", "clean", "block", "would flag"):
        table.add_column(column)
    for dealer in evidence.dealers:
        row = evidence.by_dealer[dealer]
        trusted = "yes" if dealer in rules.trusted_dealers else "no"
        table.add_row(escape(dealer), trusted, str(row["clean"]), str(row["block"]), str(row["flag"]))
    console.print(table)
    for i, source in zip(evidence.would_flag, evidence.sources, strict=True):
        console.print(f"would flag message {i.message_id} from {escape(i.dealer)}: {escape(i.reason)}")
        said = printable(source.text or "(no words)")
        words = said[:WORDS_SHOWN] + ("…" if len(said) > WORDS_SHOWN else "")
        console.print(f"  thread {source.thread}, tick {source.tick}, its words:")
        console.print(words_block(words))  # every line indented: never a line of ours
    skipped = evidence.offers - evidence.known_topic - evidence.unreadable
    console.print(f"offers without a known topic (skipped): {skipped}; unreadable: {evidence.unreadable}")
    console.print(f"flag_dealers = {', '.join(sorted(rules.flag_dealer_ids)) or 'none'} (GUARDRAILS.md opt-in)")
    console.print(f"allow_flags = {str(rules.allow_flags).lower()} (GUARDRAILS.md); ask Jev with --json")
