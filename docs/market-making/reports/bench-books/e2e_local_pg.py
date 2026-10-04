"""Replay captured `GET /api/broker/book` snapshots through the real `BenchBooks` writer into a THROWAWAY local
Postgres, then read them back. Never point this at the shared DB: it refuses any host but localhost.

    docker run -d --rm --name bench-books-pg -e POSTGRES_PASSWORD=x -p 127.0.0.1:55439:5432 postgres:18
    LOCAL_PG_URL=postgresql://postgres:x@127.0.0.1:55439/postgres \
      uv run python docs/market-making/reports/bench-books/e2e_local_pg.py <dir of book_*.json> <stats dir>
    docker stop bench-books-pg
"""

import glob
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import psycopg

from bazaar_agent import db
from bazaar_agent.agents.bench_capture import BenchBooks
from bazaar_agent.agents.matcher import BrokerBook

URL = os.environ["LOCAL_PG_URL"]
if urlsplit(URL).hostname not in ("127.0.0.1", "localhost"):
    sys.exit("refusing: LOCAL_PG_URL must be a local throwaway Postgres")

with psycopg.connect(URL) as conn:
    db.init_schema(conn)
logs: list[str] = []
books = BenchBooks(
    lambda: psycopg.connect(URL, application_name="bazaar-bench-books", connect_timeout=3),
    Path(sys.argv[2]),
    logs.append,
    world="real",
    venue="v19",
)
for i, path in enumerate(sorted(glob.glob(sys.argv[1] + "/*.json"))):
    book = BrokerBook.model_validate(json.loads(Path(path).read_text()))
    books.record(680 + i, book.bench_offers, book.fee_bps, book.fee_per_card)
    time.sleep(0.05)  # real ticks are 15 s apart; back to back would overflow the 8-batch queue
time.sleep(2)
print("log lines:", logs, "rows stored:", books.stored)
with psycopg.connect(URL) as conn:
    print(conn.execute("select count(*), count(distinct offer_id), min(tick), max(tick) from bench_books").fetchone())
