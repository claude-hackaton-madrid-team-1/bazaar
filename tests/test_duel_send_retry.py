"""One retry of a duel send after a `network` error, inside the same tick and never twice."""

import pytest

from bazaar_agent.agents.duelist import RETRY_MIN_LEFT_S, send_with_one_retry
from bazaar_agent.sdk import BazaarError

TIME = lambda: 10.0  # noqa: E731
LATE = lambda: RETRY_MIN_LEFT_S - 0.1  # noqa: E731


def flaky(*outcomes):
    calls = []

    def call():
        calls.append(1)
        out = outcomes[len(calls) - 1]
        if isinstance(out, BazaarError):
            raise out
        return out

    return call, calls


def err(code, status=0):
    return BazaarError(code, "x", status)


def test_a_network_error_is_retried_once_and_the_retry_answers():
    call, calls = flaky(err("network"), {"ok": True})
    assert send_with_one_retry(call, TIME) == ({"ok": True}, True) and len(calls) == 2


def test_no_retry_when_the_first_send_works():
    call, calls = flaky({"ok": True})
    assert send_with_one_retry(call, TIME) == ({"ok": True}, False) and len(calls) == 1


@pytest.mark.parametrize(
    "code, status", [("duel_closed", 400), ("rate_limited", 429), ("http_502", 502), ("bad_response", 0)]
)
def test_only_a_network_error_is_retried(code, status):
    call, calls = flaky(err(code, status), {"ok": True})
    with pytest.raises(BazaarError) as e:
        send_with_one_retry(call, TIME)
    assert e.value.code == code and len(calls) == 1


def test_wait_for_tick_on_the_retry_means_the_first_landed_and_there_is_no_third_try():
    call, calls = flaky(err("network"), err("wait_for_tick", 429), {"ok": True})
    assert send_with_one_retry(call, TIME) == (None, True) and len(calls) == 2


def test_another_failure_of_the_retry_raises_the_first_error():
    call, calls = flaky(err("network"), err("duel_closed", 400))
    with pytest.raises(BazaarError) as e:
        send_with_one_retry(call, TIME)
    assert e.value.code == "network" and len(calls) == 2
    call, calls = flaky(err("network"), err("network"), {"ok": True})
    with pytest.raises(BazaarError):
        send_with_one_retry(call, TIME)
    assert len(calls) == 2  # exactly one retry


def test_no_retry_without_time_left_in_the_tick():
    call, calls = flaky(err("network"), {"ok": True})
    with pytest.raises(BazaarError):
        send_with_one_retry(call, LATE)
    assert len(calls) == 1
