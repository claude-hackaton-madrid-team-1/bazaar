"""One re-read after a `429 rate_limited` (`sdk.read_once_more_after_429`) and its use by `duel run`.

Live, Sat ticks 646-650: every service on our key read at the tick boundary, the burst passed 5 req/s and the
duels runner lost tick 646 to `/api/duels refused rate_limited` (an unanswered duel scores 0). The duels read is now
sent once more after the server's wait (else 1.2 s), only while 8 s of the tick's budget are left. Never a loop.
No network and no real sleep.
"""

import pytest
from typer.testing import CliRunner

from bazaar_agent import sdk
from bazaar_agent.sdk import BazaarError, rate_limit_wait_s, read_once_more_after_429
from tests.test_jev_journal import duel_cli  # noqa: F401


def limited(**extra):
    return BazaarError("rate_limited", "more than 5 requests per second", 429, extra)


def reader(*answers):
    calls: list[int] = []

    def read():
        calls.append(1)
        answer = answers[len(calls) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return read, calls


def test_a_429_is_read_once_more_after_the_default_wait():
    read, calls = reader(limited(), {"duels": []})
    slept: list[float] = []
    assert read_once_more_after_429(read, lambda: 20.0, sleep=slept.append) == {"duels": []}
    assert len(calls) == 2 and slept == [1.2]


def test_the_servers_wait_is_used_when_it_names_one():
    assert rate_limit_wait_s(limited(retry_after=0.4)) == 0.4
    assert rate_limit_wait_s(limited(retry_after_s=2)) == 2.0
    for junk in ("soon", -1, 0, float("nan"), True, None):
        assert rate_limit_wait_s(limited(retry_after=junk)) == 1.2
    read, _ = reader(limited(retry_after=0.5), "ok")
    slept: list[float] = []
    assert read_once_more_after_429(read, lambda: 20.0, sleep=slept.append) == "ok" and slept == [0.5]


def test_a_second_429_is_raised_never_a_loop():
    read, calls = reader(limited(), limited(), "never")
    slept: list[float] = []
    with pytest.raises(BazaarError, match="rate_limited"):
        read_once_more_after_429(read, lambda: 20.0, sleep=slept.append)
    assert len(calls) == 2 and slept == [1.2]


@pytest.mark.parametrize(
    "error, left, wait",
    [
        (limited(), 9.1, 1.2),  # 9.1 - 1.2 < 8 s: no room left in the tick
        (limited(retry_after=6), 30.0, 6),  # a long wait: the next tick reads it
        (BazaarError("wait_for_tick", "", 429, {"next_tick": 650}), 30.0, None),  # RULES.md: wait for the tick
        (BazaarError("network", "GET /api/duels: timed out", 0), 30.0, None),
    ],
)
def test_no_re_read_without_room_in_the_tick_or_for_any_other_refusal(error, left, wait):
    read, calls = reader(error, "never")
    slept: list[float] = []
    with pytest.raises(BazaarError) as raised:
        read_once_more_after_429(read, lambda: left, sleep=slept.append)
    assert raised.value is error and len(calls) == 1 and slept == []


def test_duel_run_re_reads_a_429_and_still_plays_the_tick(duel_cli, monkeypatch):  # noqa: F811
    cli, client, _, _ = duel_cli
    real_duels, refusals = client.duels, [limited()]

    def duels(done=False):
        if not done and refusals:
            raise refusals.pop()
        return real_duels(done=done)

    slept: list[float] = []
    monkeypatch.setattr(client, "duels", duels)
    monkeypatch.setattr(sdk.time, "sleep", slept.append)
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    output = " ".join(result.output.split())
    assert "tick 134: /api/duels refused rate_limited, re-read in 1.2 s" in output
    assert "1 live duel(s) logged" in output and slept == [1.2]


def test_duel_run_keeps_the_tick_lost_when_the_re_read_is_refused_too(duel_cli, monkeypatch):  # noqa: F811
    cli, client, _, _ = duel_cli
    real_duels = client.duels

    def duels(done=False):
        if not done:
            raise limited()
        return real_duels(done=done)

    monkeypatch.setattr(client, "duels", duels)
    monkeypatch.setattr(sdk.time, "sleep", lambda s: None)
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    output = " ".join(result.output.split())
    assert output.count("refused rate_limited") == 2 and "live duel(s) logged" not in output and client.sent == []
