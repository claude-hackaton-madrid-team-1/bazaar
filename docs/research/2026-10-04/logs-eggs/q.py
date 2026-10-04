"""Read-only query helper for the shared Postgres (sat-logs-eggs research).

Loads DATABASE_URL from the lets-start worktree .env without printing it, opens a READ ONLY
transaction with a 60 s statement timeout, prints rows tab-separated.
Usage: uv run python docs/research/2026-10-04/logs-eggs/q.py "SELECT ..."
"""
import sys
from pathlib import Path

import psycopg

ENV = Path("/Users/mariusserban/orca/workspaces/bazaar/"
           "lets-start-using-the-real-feed-we-should-have-a-monitor-ready-in-the-code/.env")
url = next(l.split("=", 1)[1].strip().strip("'\"") for l in ENV.read_text().splitlines()
           if l.startswith("DATABASE_URL="))
with psycopg.connect(url, options="-c default_transaction_read_only=on -c statement_timeout=60000") as conn:
    conn.read_only = True
    with conn.cursor() as cur:
        cur.execute(sys.argv[1])
        if cur.description:
            print("\t".join(d.name for d in cur.description))
            for row in cur.fetchall():
                print("\t".join("" if v is None else str(v) for v in row))
