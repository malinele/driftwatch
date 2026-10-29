import json
from pathlib import Path

import pytest

from driftwatch import __version__
from driftwatch.cli import main
from driftwatch.models import Claim, ClaimKind, Finding, Location, Severity
from driftwatch.report import render_json, render_sarif, render_text
from driftwatch.scanner import scan_files


def _finding(root: Path, rel: str, line: int, severity: Severity, checker: str = "path") -> Finding:
    claim = Claim(ClaimKind.PATH, f"v-{rel}-{line}", Location(root / rel, line), "ctx")
    return Finding(claim, severity, f"msg {rel}:{line}", checker)


def _sample(root: Path) -> list[Finding]:
    return [
        _finding(root, "b.md", 2, Severity.WARNING, "link"),
        _finding(root, "docs/a.md", 9, Severity.INFO),
        _finding(root, "a.md", 5, Severity.ERROR),
        _finding(root, "a.md", 1, Severity.ERROR, "env"),
    ]


def test_sarif_has_required_structure(tmp_path: Path) -> None:
    findings = _sample(tmp_path)

    doc = json.loads(render_sarif(findings, tmp_path))

    assert doc["version"] == "2.1.0"
    driver = doc["runs"][0]["tool"]["driver"]
    assert driver["name"] == "DriftWatch"
    assert driver["informationUri"]
    rule_ids = {r["id"] for r in driver["rules"]}
    assert {"path", "link", "env"} <= rule_ids
    assert all(r["shortDescription"]["text"] for r in driver["rules"])
    assert len(doc["runs"][0]["results"]) == len(findings)


def test_sarif_levels_and_relative_uris(tmp_path: Path) -> None:
    doc = json.loads(render_sarif(_sample(tmp_path), tmp_path))

    results = doc["runs"][0]["results"]
    levels = {r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]: r["level"] for r in results}
    assert levels == {"a.md": "error", "b.md": "warning", "docs/a.md": "note"}
    for result in results:
        physical = result["locations"][0]["physicalLocation"]
        assert not physical["artifactLocation"]["uri"].startswith("/")
        assert physical["region"]["startLine"] >= 1
        assert result["message"]["text"]
    assert [r["ruleId"] for r in results][:2] == ["env", "path"]


def test_json_shape_and_sorting(tmp_path: Path) -> None:
    doc = json.loads(render_json(_sample(tmp_path), tmp_path))

    assert set(doc) == {"version", "findings", "summary"}
    assert doc["version"] == __version__
    assert [(f["file"], f["line"]) for f in doc["findings"]] == [
        ("a.md", 1),
        ("a.md", 5),
        ("b.md", 2),
        ("docs/a.md", 9),
    ]
    assert set(doc["findings"][0]) == {"file", "line", "severity", "checker", "message", "value"}
    assert doc["summary"] == {"errors": 2, "warnings": 1, "info": 1}


def test_text_is_deterministic_and_ordered(tmp_path: Path) -> None:
    findings = _sample(tmp_path)

    first = render_text(findings, 3, 0, tmp_path)
    second = render_text(list(reversed(findings)), 3, 0, tmp_path)

    assert first == second
    lines = first.splitlines()
    assert lines[0].startswith("a.md:1: ERROR [env]")
    assert lines[-1] == "2 error(s), 1 warning(s) in 3 file(s) scanned"


def test_text_summary_mentions_suppressed(tmp_path: Path) -> None:
    assert "4 suppressed by baseline" in render_text([], 1, 4, tmp_path)


def test_cli_sarif_output_file_keeps_summary_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "a.md").write_text("`gone/one.py`\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    code = main(["scan", ".", "--format", "sarif", "--output", "out.sarif"])

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "1 error(s), 0 warning(s) in 1 file(s) scanned" in captured.err
    doc = json.loads((tmp_path / "out.sarif").read_text(encoding="utf-8"))
    assert doc["runs"][0]["results"][0]["ruleId"] == "path"


def test_cli_output_unwritable_is_usage_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a.md").write_text("hi\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert main(["scan", ".", "--output", "no/such/dir/out.txt"]) == 2


def test_scan_reports_repeated_inline_path_once(tmp_path: Path) -> None:
    doc = tmp_path / "README.md"
    doc.write_text("See `missing/thing.py` and again `missing/thing.py`.\n", encoding="utf-8")

    findings = scan_files(tmp_path, [doc])

    assert len(findings) == 1
    assert findings[0].claim.value == "missing/thing.py"
