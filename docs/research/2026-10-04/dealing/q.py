"""Read-only query helper for the sat-dealing research.

Loads DATABASE_URL from the lets-start worktree's .env without printing it and runs
every statement inside a READ ONLY transaction (the server refuses any write).

    uv run python docs/research/2026-10-04/dealing/q.py "SELECT ..."
    uv run python docs/research/2026-10-04/dealing/q.py < query.sql
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg

ENV = Path(
    "/Users/mariusserban/orca/workspaces/bazaar/"
    "lets-start-using-the-real-feed-we-should-have-a-monitor-ready-in-the-code/.env"
)


def url() -> str:
    for line in ENV.read_text().splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip("'\"")
    raise SystemExit("DATABASE_URL not found")


def main() -> None:
    sql = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    with psycopg.connect(url(), options="-c statement_timeout=90000") as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(sql)
            if cur.description is None:
                return
            cols = [d.name for d in cur.description]
            rows = cur.fetchall()
            print("\t".join(cols))
            for r in rows:
                print("\t".join("" if v is None else str(v) for v in r))
            print(f"({len(rows)} rows)", file=sys.stderr)


if __name__ == "__main__":
    main()
