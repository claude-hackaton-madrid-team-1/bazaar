from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bazaar_agent.guardrails import Ledger
from bazaar_agent.ledger_pg import FallbackLedger, LedgerUnavailable, PgLedger


def _pg(rows):
    conn = MagicMock()
    conn.closed = False
    conn.execute.return_value.fetchall.return_value = rows
    return PgLedger(lambda: conn, "maker")


def test_pg_ledger_reads_hands_off_ids():
    assert _pg([("hands-off:42",), ("hands-off:x",)]).hands_off_ids() == {42}


def test_fallback_ledger_reads_hands_off_ids(tmp_path: Path):
    file = Ledger(tmp_path / "ledger.jsonl")
    file.record("listing", 1, 0.1, 10, "hands-off:7")
    down = PgLedger(lambda: (_ for _ in ()).throw(OSError("down")), "maker")
    assert FallbackLedger(down, file).hands_off_ids() == {7}
