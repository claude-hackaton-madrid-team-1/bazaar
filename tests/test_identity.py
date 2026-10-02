"""Who "us" is: BAZAAR_TEAM_ID, then the cache, then /api/me once. Every source is validated."""

import pytest
from pydantic import ValidationError

from bazaar_agent.config import load_settings
from bazaar_agent.identity import cached_team_id, resolve_team_id
from bazaar_agent.sdk import BazaarError


def me_reader(answer, calls):
    def read():
        calls.append(1)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return read


def test_the_override_wins_and_needs_no_read(tmp_path):
    calls: list[int] = []
    assert resolve_team_id("t07", tmp_path, me_reader({"id": "t01"}, calls)) == "t07"
    assert calls == [] and cached_team_id(tmp_path) is None


def test_me_is_read_once_then_the_cache_answers(tmp_path):
    calls: list[int] = []
    read = me_reader({"id": "t01", "name": "Team 1"}, calls)
    assert resolve_team_id(None, tmp_path, read) == "t01"
    assert resolve_team_id(None, tmp_path, read) == "t01"
    assert calls == [1] and cached_team_id(tmp_path) == "t01"


@pytest.mark.parametrize("bad", ["", "T01", "t01; drop table", "team1", "t"])
def test_malformed_ids_are_never_trusted(tmp_path, bad):
    warnings: list[str] = []
    (tmp_path / "team_id").write_text(bad)
    assert resolve_team_id(bad, tmp_path, me_reader({"id": bad}, []), warnings.append) is None
    assert warnings == ["our team id is unknown: /api/me carried no valid `id` (set BAZAAR_TEAM_ID)"]


def test_a_refused_me_warns_with_the_code_and_returns_none(tmp_path):
    warnings: list[str] = []
    read = me_reader(BazaarError("bad_key", "wrong key", 401), [])
    assert resolve_team_id(None, tmp_path, read, warnings.append) is None
    assert warnings == ["our team id is unknown: /api/me refused bad_key (set BAZAAR_TEAM_ID to skip the read)"]


def test_without_a_key_and_a_cache_the_id_is_unknown(tmp_path):
    assert resolve_team_id(None, tmp_path, None) is None


def test_bazaar_team_id_comes_from_the_env_and_is_validated(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZAAR_TEAM_ID", "t01")
    assert load_settings(tmp_path / "none.env").team_id == "t01"
    monkeypatch.setenv("BAZAAR_TEAM_ID", "t01 or 1=1")
    with pytest.raises(ValidationError):
        load_settings(tmp_path / "none.env")
