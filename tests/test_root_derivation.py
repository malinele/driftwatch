"""The scan root comes from the scan target, never from the current working directory."""

import json
from pathlib import Path
from typing import Any

import pytest

from driftwatch.check import ESCAPE_MESSAGE
from driftwatch.cli import derive_root, main

DECOY = "DECOY_ENV_VAR"


def _make_repo(base: Path) -> Path:
    repo = base / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    (repo / "src" / "app.py").write_text('import os\nos.environ["REPO_TOKEN"]\n', encoding="utf-8")
    (repo / "docs" / "guide.md").write_text("Set `REPO_TOKEN` and see `missing/file.txt`.\n", encoding="utf-8")
    return repo


def _decoy_dir(base: Path) -> Path:
    other = base / "elsewhere"
    other.mkdir()
    (other / "decoy.py").write_text(f'import os\nos.getenv("{DECOY}")\n', encoding="utf-8")
    return other


def _scan_json(capsys: pytest.CaptureFixture[str], target: Path) -> dict[str, Any]:
    main(["scan", str(target), "--format", "json"])
    result: dict[str, Any] = json.loads(capsys.readouterr().out)
    return result


def test_scan_from_other_cwd_matches_scan_from_inside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _make_repo(tmp_path)
    other = _decoy_dir(tmp_path)

    monkeypatch.chdir(repo)
    inside = _scan_json(capsys, Path("."))
    monkeypatch.chdir(other)
    outside = _scan_json(capsys, repo)

    assert outside["findings"] == inside["findings"]
    assert DECOY not in json.dumps(outside)
    files = [f["file"] for f in outside["findings"]]
    assert files
    assert all(not f.startswith(("/", "private/")) for f in files)


def test_root_is_repo_when_scanning_subdirectory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _make_repo(tmp_path)
    monkeypatch.chdir(_decoy_dir(tmp_path))

    assert derive_root([(repo / "docs").resolve()]) == repo.resolve()
    findings = _scan_json(capsys, repo / "docs")["findings"]

    # REPO_TOKEN is read in src/, outside the scanned docs/, but the root is the repo, so it is honored.
    assert [f for f in findings if f["checker"] == "env"] == []
    assert any(f["file"] == "docs/guide.md" and f["value"] == "missing/file.txt" for f in findings)


def test_root_falls_back_to_first_directory_or_parent(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    note = plain / "note.md"
    note.write_text("x\n", encoding="utf-8")
    (plain / "driftwatch.toml").write_text("", encoding="utf-8")

    assert derive_root([note.resolve()]) == plain.resolve()
    assert derive_root([plain.resolve()]) == plain.resolve()


def test_escaping_claim_warns_without_touching_outside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "a" / "b" / "repo"
    repo.mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    (repo / "README.md").write_text("See `../../outside.txt`.\n", encoding="utf-8")
    (tmp_path / "a" / "outside.txt").write_text("secret\n", encoding="utf-8")  # exists; must not matter

    monkeypatch.chdir(tmp_path)
    findings = _scan_json(capsys, repo)["findings"]

    assert [f["message"] for f in findings] == [f"{ESCAPE_MESSAGE}: ../../outside.txt"]
    assert findings[0]["severity"] == "warning"
