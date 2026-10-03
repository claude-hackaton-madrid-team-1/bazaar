"""Where the simulated world persists, and the database it must never touch (the real `railway` one)."""

import pytest

from bazaar_sim.app import load_world
from bazaar_sim.store import MemoryStore, SqliteStore, StoreRefused, check_sim_database, database_name, open_store
from tests.simkit import QUIET, manual_world


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://postgres:pw@iriguchi.proxy.rlwy.net:28880/railway",
        "postgresql://postgres:pw@postgres.railway.internal:5432/railway?sslmode=require",
        "postgresql://postgres:pw@host:5432/bazaar_sim?dbname=railway",  # a libpq override counts
        "postgresql://postgres:pw@host:5432/bazaar",
        "postgresql://postgres:pw@host:5432/",
    ],
)
def test_the_simulator_refuses_any_database_but_bazaar_sim(url, tmp_path):
    with pytest.raises(StoreRefused) as e:
        open_store(url, tmp_path / "w.sqlite")
    assert "pw" not in str(e.value)


@pytest.mark.parametrize("name", ["bazaar_sim", "bazaar_sim_test"])
def test_bazaar_sim_databases_are_allowed(name):
    assert check_sim_database(f"postgresql://u:p@h:5432/{name}?sslmode=require") == name
    assert database_name(f"postgres://u:p@h/{name}") == name


def test_unknown_store_urls_are_refused(tmp_path):
    with pytest.raises(StoreRefused):
        open_store("mysql://x/bazaar_sim", tmp_path / "w.sqlite")


def test_sqlite_round_trips_the_world_and_a_restart_resumes_at_the_same_tick(tmp_path):
    store = open_store(f"sqlite:///{tmp_path / 'sim' / 'world.sqlite'}", tmp_path / "unused.sqlite")
    assert isinstance(store, SqliteStore) and store.load() is None
    m = manual_world()
    m.step(4)
    store.save(m.world.state.model_dump_json(), m.world.tick)
    store.save(m.world.state.model_dump_json(), m.world.tick)  # an upsert, never a second row
    again = load_world(store, QUIET)
    assert again.tick == 4 and again.state.teams.keys() == m.world.state.teams.keys()
    assert "sqlite" in store.describe()


def test_memory_store_and_default_sqlite(tmp_path):
    assert isinstance(open_store("memory", tmp_path / "x.sqlite"), MemoryStore)
    default = open_store(None, tmp_path / "d" / "world.sqlite")
    assert isinstance(default, SqliteStore) and (tmp_path / "d" / "world.sqlite").exists()


def test_an_unreadable_snapshot_starts_a_new_world():
    store = MemoryStore()
    store.save("{not json", 3)
    assert load_world(store, QUIET).tick == 0
