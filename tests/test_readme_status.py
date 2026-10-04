"""The README hook stays local, bounded and repeatable, including legacy CI calls."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("readme_status", ROOT / "scripts/readme_status.py")
assert spec and spec.loader
readme_status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(readme_status)


def test_refresh_tracks_project_metadata_without_rewriting_prose(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(readme_status, "ROOT", tmp_path)
    readme = tmp_path / "README.md"
    monkeypatch.setattr(readme_status, "README", readme)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.12"\n[project.scripts]\nzeta = "z:app"\nalpha = "a:app"\n'
    )
    readme.write_text(f"Before\n{readme_status.START}\nold metadata\n{readme_status.END}\nAfter\n")
    original = readme.read_text()
    assert readme_status.main(["--check"]) == 1
    assert readme.read_text() == original
    assert "stale" in capsys.readouterr().err
    assert readme_status.main(["--activity"]) == 0
    refreshed = readme.read_text()
    assert refreshed.startswith("Before\n") and refreshed.endswith("\nAfter\n")
    assert "Python `>=3.12` · CLI entry points: `alpha`, `zeta`." in refreshed
    assert len(refreshed.splitlines()) == 6
    assert readme_status.main(["--activity", "--check"]) == 0
    assert readme_status.main([]) == 0
    assert readme.read_text() == refreshed


@pytest.mark.parametrize(
    "content",
    ["No markers", readme_status.END + readme_status.START, (readme_status.START + readme_status.END) * 2],
)
def test_bad_markers_fail_without_rewriting(content):
    with pytest.raises(SystemExit, match="exactly one ordered status block"):
        readme_status.render(content)


def test_committed_readme_metadata_is_current():
    assert readme_status.main(["--check"]) == 0
