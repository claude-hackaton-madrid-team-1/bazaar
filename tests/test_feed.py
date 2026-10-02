from bazaar_agent.feed import FeedStore, load_events


def ev(i, kind="thread.message"):
    return {"id": i, "tick": i // 10, "type": kind, "payload": {}}


def test_append_dedupes_orders_and_persists(tmp_path):
    store = FeedStore(tmp_path)
    r1 = store.append([ev(3), ev(1), ev(2)], window_limit=500)
    r2 = store.append([ev(2), ev(3), ev(4)], window_limit=500)
    assert (r1.new, r2.new, r2.newest_id) == (3, 1, 4)
    assert [e["id"] for e in FeedStore(tmp_path).events()] == [1, 2, 3, 4]


def test_gap_is_flagged_when_a_full_window_no_longer_reaches_our_history(tmp_path):
    store = FeedStore(tmp_path)
    store.append([ev(1), ev(2)], window_limit=2)
    assert not store.append([ev(2), ev(3)], window_limit=2).gap_possible
    assert store.append([ev(10), ev(11)], window_limit=2).gap_possible


def test_partial_window_is_never_a_gap(tmp_path):
    store = FeedStore(tmp_path)
    store.append([ev(1)], window_limit=500)
    assert not store.append([ev(50)], window_limit=500).gap_possible


def test_load_events_merges_live_window(tmp_path):
    store = FeedStore(tmp_path)
    store.append([ev(1), ev(2)], window_limit=500)
    assert [e["id"] for e in load_events(store, [ev(2), ev(3)])] == [1, 2, 3]
