"""The supply map in Postgres (N14b), in a throwaway schema: skipped when Postgres is unreachable."""

import pytest

from bazaar_agent import supply
from bazaar_agent.supply_db import ScanStore, load_scan, save_map, save_scan
from tests import test_db
from tests.test_strategy import CATALOG, ME
from tests.test_supply import FEED, SCAN

pytestmark = pytest.mark.integration
database_url, schema, conn = test_db.database_url, test_db.schema, test_db.conn  # the throwaway-schema fixtures


def test_a_scan_round_trips_and_an_older_tick_never_overwrites_a_newer_one(conn):
    assert save_scan(conn, SCAN, tick=40) == 3
    assert [r["id"] for r in load_scan(conn)] == [5, 47, 200]
    moved = [{**SCAN[0], "owner": "t09"}]  # asset 47
    save_scan(conn, moved, tick=30)  # older: ignored
    assert next(r for r in load_scan(conn) if r["id"] == 47)["owner"] == "a team"
    save_scan(conn, moved, tick=41)
    assert next(r for r in load_scan(conn) if r["id"] == 47)["owner"] == "t09"


def test_the_map_is_stored_per_card_and_per_set(conn):
    sm = supply.supply_map(CATALOG, ME, FEED, SCAN)
    assert save_map(conn, sm, tick=40) == len(sm.cards)
    with conn.cursor() as cur:
        cur.execute("select minted, holders, others from supply_cards where ref = 'LAV-09'")
        assert cur.fetchone() == (1, {"t04": 1}, 1)
        cur.execute("select pages_possible, bottleneck, packs_opened from supply_sets where set_code = 'LAV'")
        assert cur.fetchone() == (0, ["LAV-10"], 2)


def test_the_agents_read_the_stored_scan_back(conn, database_url, schema, tmp_path):
    save_scan(conn, SCAN, tick=40)
    store = ScanStore(tmp_path, lambda: test_db.open_in(database_url, schema))
    assert [r["ref"] for r in store.rows(100)] == ["LAT-09", "LAV-09", "LAT-09"]
