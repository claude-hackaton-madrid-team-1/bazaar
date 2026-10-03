"""`bazaar-sim reset` sends the admin token only to a simulator: https, or this machine; never the real host."""

import pytest
from typer.testing import CliRunner

from bazaar_sim import cli


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",  # plain http off this machine
        "https://bazaar.causaprima.ai",
        "https://BAZAAR.causaprima.ai",
        "https://bazaar.causaprima.ai.",  # the same host with its trailing dot
        "https://user@bazaar.causaprima.ai",
    ],
)
def test_reset_refuses_to_send_the_token_anywhere_but_a_simulator(url, monkeypatch):
    monkeypatch.setenv("SIM_ADMIN_TOKEN", "t" * 32)
    sent = []
    monkeypatch.setattr(cli.urllib.request, "urlopen", lambda *a, **k: sent.append(a))
    result = CliRunner().invoke(cli.app, ["reset", "--url", url])
    assert result.exit_code == 2 and "refusing" in result.output
    assert sent == []


def test_reset_needs_the_token(monkeypatch):
    monkeypatch.delenv("SIM_ADMIN_TOKEN", raising=False)
    result = CliRunner().invoke(cli.app, ["reset", "--url", "https://bazaar-sim-production-1d48.up.railway.app"])
    assert result.exit_code == 2 and "SIM_ADMIN_TOKEN is not set" in result.output
