"""One retry of a duel send after a `network` error, inside the same tick and never twice."""

import io
import signal
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from types import SimpleNamespace

import pytest

from bazaar_agent.agents import duelist
from bazaar_agent.agents.duelist import RETRY_MIN_LEFT_S, send_with_one_retry
from bazaar_agent.sdk import BazaarError, TeamBazaar

TIME = lambda: 10.0  # noqa: E731
LATE = lambda: RETRY_MIN_LEFT_S - 0.1  # noqa: E731


@pytest.fixture
def client():
    return SimpleNamespace(timeout=4.0)


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


def test_a_network_error_is_retried_once_and_the_retry_answers(client):
    call, calls = flaky(err("network"), {"ok": True})
    assert send_with_one_retry(call, TIME, client=client) == ({"ok": True}, True) and len(calls) == 2


def test_no_retry_when_the_first_send_works(client):
    call, calls = flaky({"ok": True})
    assert send_with_one_retry(call, TIME, client=client) == ({"ok": True}, False) and len(calls) == 1


@pytest.mark.parametrize(
    "code, status",
    [
        ("duel_closed", 400),
        ("rate_limited", 429),
        ("http_502", 502),
        ("bad_response", 0),
        ("network", 400),
        ("network", 503),
    ],
)
def test_only_a_network_error_is_retried(code, status, client):
    call, calls = flaky(err(code, status), {"ok": True})
    with pytest.raises(BazaarError) as e:
        send_with_one_retry(call, TIME, client=client)
    assert e.value.code == code and len(calls) == 1


def test_wait_for_tick_on_the_retry_means_the_first_landed_and_there_is_no_third_try(client):
    call, calls = flaky(err("network"), err("wait_for_tick", 429), {"ok": True})
    assert send_with_one_retry(call, TIME, client=client) == (None, True) and len(calls) == 2


def test_another_failure_of_the_retry_raises_the_first_error(client):
    call, calls = flaky(err("network"), err("duel_closed", 400))
    with pytest.raises(BazaarError) as e:
        send_with_one_retry(call, TIME, client=client)
    assert e.value.code == "network" and len(calls) == 2
    call, calls = flaky(err("network"), err("network"), {"ok": True})
    with pytest.raises(BazaarError):
        send_with_one_retry(call, TIME, client=client)
    assert len(calls) == 2  # exactly one retry


def test_no_retry_without_time_left_in_the_tick(client):
    call, calls = flaky(err("network"), {"ok": True})
    with pytest.raises(BazaarError):
        send_with_one_retry(call, LATE, client=client)
    assert len(calls) == 1


@pytest.mark.parametrize("elapsed", [7.6, 4.1])
def test_no_retry_when_first_attempt_uses_the_tick_or_retry_margin(client, elapsed):
    now = [0.0]
    attempts = []

    def call():
        attempts.append(1)
        now[0] = elapsed
        raise err("network")

    # Planned tick ends at 7.5; its guarded send deadline remains 5.5 even after the tick changes.
    with pytest.raises(BazaarError, match="network"):
        send_with_one_retry(call, lambda: 5.5 - now[0], client=client)
    assert attempts == [1]
    assert client.timeout == 4.0


def test_real_sdk_retry_timeout_cannot_reach_the_next_tick(monkeypatch):
    now = [0.0]
    timeouts, applied_ticks = [], []
    client = TeamBazaar("http://localhost", "test-key", on_write=None)

    def transport(request, *, timeout):
        timeouts.append(timeout)
        if len(timeouts) == 1:
            applied_ticks.append(134)
            now[0] += 4.0
            raise TimeoutError("response lost")
        # This retry would arrive at t=7.6 in tick 135 with the old four-second timeout.
        if timeout < 3.6:
            now[0] += timeout
            raise TimeoutError("retry deadline")
        now[0] += 3.6
        applied_ticks.append(135)
        return io.BytesIO(b'{"ok": true}')

    monkeypatch.setattr(urllib.request, "urlopen", transport)
    with pytest.raises(BazaarError, match="response lost"):
        send_with_one_retry(lambda: client.duel_say(95, "offer", price=110), lambda: 5.5 - now[0], client=client)
    assert timeouts == [4.0, 1.5]
    assert applied_ticks == [134]
    assert now[0] == 5.5
    assert client.timeout == 4.0


@pytest.mark.parametrize("second", [err("duel_closed", 400), err("http_503", 503), err("network")])
def test_second_refusal_keeps_original_uncertain_outcome_and_restores_timeout(client, second):
    first = err("network")
    call, calls = flaky(first, second)
    with pytest.raises(BazaarError) as caught:
        send_with_one_retry(call, lambda: 2.0, client=client)
    assert caught.value is first
    assert len(calls) == 2
    assert client.timeout == 4.0


def test_retry_skips_a_worker_thread(client):
    first = err("network")
    call, calls = flaky(first, {"ok": True})
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(send_with_one_retry, call, TIME, client=client)
        with pytest.raises(BazaarError) as caught:
            future.result()
    assert caught.value is first
    assert len(calls) == 1


def test_retry_skips_when_interval_timers_are_unsupported(monkeypatch, client):
    first = err("network")
    call, calls = flaky(first, {"ok": True})
    monkeypatch.delattr(signal, "setitimer")
    with pytest.raises(BazaarError) as caught:
        send_with_one_retry(call, TIME, client=client)
    assert caught.value is first
    assert len(calls) == 1


def test_retry_rechecks_original_deadline_after_timer_setup(client):
    first = err("network")
    call, calls = flaky(first, {"ok": True})
    remaining = iter([10.0, 0.0])
    handler = signal.getsignal(signal.SIGALRM)
    with pytest.raises(BazaarError) as caught:
        send_with_one_retry(call, lambda: next(remaining), client=client)
    assert caught.value is first
    assert len(calls) == 1
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    assert signal.getsignal(signal.SIGALRM) == handler
    assert client.timeout == 4.0


def test_retry_does_not_replace_an_existing_alarm(client):
    first = err("network")
    call, calls = flaky(first, {"ok": True})
    handler = signal.getsignal(signal.SIGALRM)
    signal.setitimer(signal.ITIMER_REAL, 60)
    try:
        with pytest.raises(BazaarError) as caught:
            send_with_one_retry(call, TIME, client=client)
        assert caught.value is first
        assert len(calls) == 1
        assert signal.getsignal(signal.SIGALRM) == handler
        assert signal.getitimer(signal.ITIMER_REAL)[0] > 59
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def test_retry_alarm_uses_original_budget_and_restores_handler(monkeypatch, client):
    handler = signal.getsignal(signal.SIGALRM)
    timers = []
    first = err("network")
    attempts = []

    def call():
        attempts.append(1)
        if len(attempts) == 1:
            raise first
        signal.getsignal(signal.SIGALRM)(signal.SIGALRM, None)
        pytest.fail("deadline handler returned")

    monkeypatch.setattr(signal, "setitimer", lambda kind, seconds: timers.append((kind, seconds)))
    with pytest.raises(BazaarError) as caught:
        send_with_one_retry(call, lambda: 2.0, client=client)
    assert caught.value is first
    assert timers == [(signal.ITIMER_REAL, 2.0), (signal.ITIMER_REAL, 0)]
    assert signal.getsignal(signal.SIGALRM) == handler
    assert client.timeout == 4.0


@pytest.mark.parametrize("delay_in", ["hook", "transport"])
def test_real_alarm_aborts_retry_before_a_delayed_send(monkeypatch, delay_in):
    attempts, completed = [], []
    handler = signal.getsignal(signal.SIGALRM)

    def hook(method, path, phase):
        if delay_in == "hook" and phase == "before" and attempts:
            with suppress(Exception):  # the real tracking hook also swallows ordinary exceptions
                time.sleep(0.1)

    def transport(request, *, timeout):
        attempts.append(1)
        if len(attempts) == 1:
            raise TimeoutError("first response lost")
        if delay_in == "transport":
            time.sleep(0.1)  # DNS/connection phases do not share one urllib timeout
        completed.append(1)
        return io.BytesIO(b'{"ok": true}')

    client = TeamBazaar("http://localhost", "test-key", on_write=hook)
    monkeypatch.setattr(duelist, "RETRY_MIN_LEFT_S", 0.001)
    monkeypatch.setattr(urllib.request, "urlopen", transport)
    with pytest.raises(BazaarError, match="first response lost"):
        send_with_one_retry(lambda: client.duel_say(95, "offer", price=110), lambda: 0.02, client=client)
    assert not completed
    assert len(attempts) == (1 if delay_in == "hook" else 2)
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    assert signal.getsignal(signal.SIGALRM) == handler
    assert client.timeout == 4.0
