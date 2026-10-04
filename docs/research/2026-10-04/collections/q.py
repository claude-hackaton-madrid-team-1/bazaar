"""Read-only query helper: uv run python q.py "SELECT ..." (env DATABASE_URL loaded by caller, never printed)."""
import os
import sys

import psycopg

sql = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
assert sql.lstrip().lower().startswith(("select", "with")), "read-only: SELECT/WITH only"
with psycopg.connect(os.environ["DATABASE_URL"], options="-c default_transaction_read_only=on") as conn:
    with conn.cursor() as cur:
        cur.execute(sql)
        cols = [d.name for d in cur.description]
        print("\t".join(cols))
        for row in cur.fetchall():
            print("\t".join("" if v is None else str(v) for v in row))
