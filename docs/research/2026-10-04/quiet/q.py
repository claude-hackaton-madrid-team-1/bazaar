"""Read-only SQL runner for the Saturday 'quiet' review.

Usage: DATABASE_URL=... uv run python q.py "SELECT ..."   (or -f file.sql)
Opens the session READ ONLY; prints a plain table. Never writes.
"""
import os
import sys

import psycopg

sql = open(sys.argv[2]).read() if sys.argv[1] == "-f" else sys.argv[1]
with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=15) as conn:
    conn.read_only = True
    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = '120s'")
        for stmt in [s for s in sql.split(";\n") if s.strip()]:
            cur.execute(stmt)
            if cur.description:
                cols = [d.name for d in cur.description]
                print(" | ".join(cols))
                for row in cur.fetchall():
                    print(" | ".join("" if v is None else str(v) for v in row))
                print()
