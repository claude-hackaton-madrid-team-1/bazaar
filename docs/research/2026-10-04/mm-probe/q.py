"""Read-only query helper for the shared game ledger.

Usage: uv run python docs/research/2026-10-04/mm-probe/q.py "SELECT ..."
DATABASE_URL is read from the lets-start worktree's .env and never printed;
every session is READ ONLY (default_transaction_read_only=on).
"""

import sys
from pathlib import Path

import psycopg

ENVF = Path(
    "/Users/mariusserban/orca/workspaces/bazaar/"
    "lets-start-using-the-real-feed-we-should-have-a-monitor-ready-in-the-code/.env"
)


def dsn() -> str:
    for line in ENVF.read_text().splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip("'\"")
    raise SystemExit("no DATABASE_URL")


def main() -> None:
    sql = sys.argv[1]
    with psycopg.connect(dsn(), options="-c default_transaction_read_only=on") as conn:
        cur = conn.execute(sql)
        if cur.description is None:
            return
        cols = [c.name for c in cur.description]
        print("\t".join(cols))
        for row in cur.fetchall():
            print("\t".join("" if v is None else str(v) for v in row))


if __name__ == "__main__":
    main()
