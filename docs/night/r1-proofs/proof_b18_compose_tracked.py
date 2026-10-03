import io
import urllib.error
import urllib.request

import pytest

from bazaar_agent import sdk


def test_team_bazaar_keeps_the_holdings_hook_once_per_write_and_b18_retries(monkeypatch):
    calls, seen = [], []
    c = sdk.TeamBazaar("http://127.0.0.1:9", "tk", on_write=lambda m, p, ph: seen.append((m, p, ph)))
    assert isinstance(c, sdk.TrackedBazaar) and c.timeout == 4.0 and c.retries == 0

    def refuse(req, timeout):
        calls.append(req.get_method())
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {}, io.BytesIO(b'{"error": "rate_limited"}'))

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(sdk.bazaar_sdk.time, "sleep", lambda s: None)
    with pytest.raises(sdk.BazaarError):
        c.accept(5)
    with pytest.raises(sdk.BazaarError):
        c.me()
    assert calls == ["POST", "GET", "GET"]
    assert seen == [("POST", "/api/offers/5/accept", "before"), ("POST", "/api/offers/5/accept", "after")]
