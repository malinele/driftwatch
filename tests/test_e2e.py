"""End-to-end proof: the real CLI pipeline against a fixture repository with known drift."""

import re
import shutil
from pathlib import Path

import pytest

from driftwatch.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "demo-repo"
TEMPLATE_SUFFIX = ".md.in"
FINDING = re.compile(r"^(?P<file>\S+):(?P<line>\d+): (?P<severity>[A-Z]+) \[(?P<checker>\w+)\]", re.MULTILINE)

EXPECTED_ERRORS = {
    ("README.md", 3, "path"),
    ("docs/setup.md", 5, "path"),
    ("docs/setup.md", 9, "env"),
}
EXPECTED_WARNINGS = {("README.md", 1, "env")}
IGNORED_LINE = ("docs/setup.md", 14, "path")

# (file, old, new): each applies once to turn the drifted copy into a consistent one.
FIXES = (
    ("README.md", "`scripts/old_run.sh`", "`scripts/run.sh`"),
    ("README.md", "docs/missing.md", "docs/setup.md"),
    ("README.md", "#nope", "#installation"),
    ("README.md", "./scripts/deploy.sh", "./scripts/run.sh"),
    ("README.md", "is gone.\n", "is gone. Optionally set `DEMO_FLAG`.\n"),
    ("docs/setup.md", "`config/settings.yaml`", "`app.py`"),
    ("docs/setup.md", "APP_SECRET_KEY=changeme", "DEMO_FLAG=1"),
    ("docs/setup.md", "<!-- driftwatch: ignore -->\n", ""),
    ("docs/setup.md", "`docs/legacy.md`", "`docs/setup.md`"),
)


def _materialize(destination: Path) -> Path:
    """Copy the fixture, restoring the real ``.md`` names (see fixtures/demo-repo/NOTE.txt)."""
    for source in FIXTURE.rglob("*"):
        if not source.is_file() or source.name == "NOTE.txt":
            continue
        target = destination / source.relative_to(FIXTURE)
        if source.name.endswith(TEMPLATE_SUFFIX):
            target = target.with_name(source.name.removesuffix(".in"))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return destination


def _findings(output: str, severity: str) -> set[tuple[str, int, str]]:
    return {
        (m["file"], int(m["line"]), m["checker"]) for m in FINDING.finditer(output) if m["severity"] == severity
    }


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = _materialize(tmp_path / "demo-repo")
    monkeypatch.chdir(root)  # the CLI treats the current directory as the scan root
    return root


def test_fixture_reports_expected_findings_and_exits_one(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["scan", "."])

    out = capsys.readouterr().out
    assert code == 1
    assert _findings(out, "ERROR") == EXPECTED_ERRORS
    assert _findings(out, "WARNING") == EXPECTED_WARNINGS
    assert IGNORED_LINE not in _findings(out, "ERROR")
    assert "docs/legacy.md" not in out
    assert "3 error(s), 1 warning(s)" in out


def test_baseline_suppresses_existing_findings(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scan", ".", "--write-baseline"]) == 0
    capsys.readouterr()

    code = main(["scan", "."])

    out = capsys.readouterr().out
    assert code == 0
    assert "0 error(s), 0 warning(s), 4 suppressed by baseline" in out


def test_fully_fixed_copy_is_clean(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for name, old, new in FIXES:
        path = repo / name
        text = path.read_text(encoding="utf-8")
        assert text.count(old) == 1, f"{name}: expected one occurrence of {old!r}"
        path.write_text(text.replace(old, new), encoding="utf-8")

    code = main(["scan", "."])

    assert code == 0
    assert "0 error(s), 0 warning(s) in 2 file(s) scanned" in capsys.readouterr().out


@pytest.mark.xfail(
    strict=True,
    reason="scanner.scan_files only runs extract_path_claims/extract_env_claims: link, anchor and command "
    "claims are never extracted, so those checkers cannot fire in the real pipeline",
)
def test_link_anchor_and_command_drift_is_detected(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["scan", "."])

    checkers = {checker for _, _, checker in _findings(capsys.readouterr().out, "ERROR")}
    assert {"link", "anchor", "command"} <= checkers
