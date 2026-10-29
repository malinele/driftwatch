import json
from pathlib import Path

import pytest

from driftwatch import __version__
from driftwatch.check import CHECKERS, PathChecker
from driftwatch.cli import main
from driftwatch.extract import extract_path_claims
from driftwatch.markdown import parse_markdown
from driftwatch.models import ClaimKind, Severity
from driftwatch.scanner import scan


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _claims(tmp_path: Path, text: str) -> list[str]:
    doc_path = _write(tmp_path, "doc.md", text)
    return [c.value for c in extract_path_claims(parse_markdown(doc_path, text))]


def test_extracts_paths_with_slash_or_known_extension(tmp_path: Path) -> None:
    values = _claims(tmp_path, "See `src/app.py`, `README.md`, `docs/` and `Cargo.lock`.\n")

    assert values == ["src/app.py", "README.md", "docs/", "Cargo.lock"]


def test_claims_have_path_kind_and_correct_line(tmp_path: Path) -> None:
    text = "intro\n\nuse `a/b.py` here\n"
    doc_path = _write(tmp_path, "doc.md", text)

    claims = extract_path_claims(parse_markdown(doc_path, text))

    assert [(c.kind, c.location.line, c.location.file) for c in claims] == [(ClaimKind.PATH, 3, doc_path)]
    assert claims[0].context == "use `a/b.py` here"


def test_strips_trailing_punctuation(tmp_path: Path) -> None:
    assert _claims(tmp_path, "`src/app.py.` and `notes.md,`\n") == ["src/app.py", "notes.md"]


@pytest.mark.parametrize(
    "token",
    [
        "https://example.com/a/b.md",
        "http://x.io/y",
        "mailto:me@example.com",
        "--config=a/b.toml",
        "-x",
        "src/*.py",
        "src/{a,b}.py",
        "two words/file.py",
        "plain",
        "x" * 200 + ".py",
    ],
)
def test_non_path_tokens_are_not_claimed(tmp_path: Path, token: str) -> None:
    assert _claims(tmp_path, f"`{token}`\n") == []


def test_existing_path_has_no_findings(tmp_path: Path) -> None:
    _write(tmp_path, "src/app.py", "")
    _write(tmp_path, "README.md", "Run `src/app.py` and read `README.md`.\n")

    assert scan(tmp_path, [tmp_path]) == []


def test_missing_path_is_error_with_line(tmp_path: Path) -> None:
    _write(tmp_path, "src/keep.py", "")  # v3: ERROR needs an existing parent directory
    _write(tmp_path, "README.md", "ok\n\nSee `src/gone.py`.\n")

    findings = scan(tmp_path, [tmp_path])

    assert len(findings) == 1
    assert findings[0].severity is Severity.ERROR
    assert findings[0].checker == "path"
    assert findings[0].message == "path does not exist: src/gone.py"
    assert findings[0].claim.location.line == 3


@pytest.mark.parametrize("name", ["conf.toml", "a.yaml", "a.yml", "a.json", "a.txt", "a.sh", "a.cfg", "a.ini"])
def test_missing_file_by_extension_is_error(tmp_path: Path, name: str) -> None:
    _write(tmp_path, "docs/keep.md", "")  # v3: ERROR needs an existing parent directory
    _write(tmp_path, "README.md", f"`docs/{name}`\n")

    findings = scan(tmp_path, [tmp_path])

    assert [f.severity for f in findings] == [Severity.ERROR]


def test_doc_relative_resolution(tmp_path: Path) -> None:
    _write(tmp_path, "docs/img/logo.png", "")
    _write(tmp_path, "docs/guide.md", "Logo: `img/logo.png`\n")

    assert scan(tmp_path, [tmp_path]) == []


def test_root_relative_resolution_from_nested_doc(tmp_path: Path) -> None:
    _write(tmp_path, "src/app.py", "")
    _write(tmp_path, "docs/guide.md", "Code: `src/app.py`\n")

    assert scan(tmp_path, [tmp_path]) == []


def test_absolute_path_warns_without_existence_check(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "`/etc/passwd` and `/no/such/place`\n")

    findings = scan(tmp_path, [tmp_path])

    assert [f.severity for f in findings] == [Severity.WARNING, Severity.WARNING]
    assert {f.message for f in findings} == {"absolute path in docs is machine-specific"}


def test_scan_skips_ignored_and_hidden_directories(tmp_path: Path) -> None:
    for skipped in (".git", ".venv", "venv", "node_modules", "dist", "__pycache__", ".hidden"):
        _write(tmp_path, f"{skipped}/x.md", "`missing/file.py`\n")

    assert scan(tmp_path, [tmp_path]) == []


def test_scan_accepts_single_file(tmp_path: Path) -> None:
    doc = _write(tmp_path, "one.md", "`nope/x.py`\n")

    assert len(scan(tmp_path, [doc])) == 1


def test_registry_contains_path_checker() -> None:
    assert isinstance(CHECKERS["path"], PathChecker)


def test_cli_exit_zero_when_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, "src/app.py", "")
    _write(tmp_path, "README.md", "`src/app.py`\n")
    monkeypatch.chdir(tmp_path)

    assert main(["scan", "."]) == 0
    assert "0 error(s), 0 warning(s) in 1 file(s) scanned" in capsys.readouterr().out


def test_cli_exit_one_and_output_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, "gone/keep.txt", "")  # v3: ERROR needs an existing parent directory
    _write(tmp_path, "b.md", "`/abs/path`\n")
    _write(tmp_path, "a.md", "x\n`gone/one.py`\n\n`gone/two.py`\n")
    monkeypatch.chdir(tmp_path)

    assert main(["scan", "."]) == 1

    assert capsys.readouterr().out.splitlines() == [
        "a.md:2: ERROR [path] path does not exist: gone/one.py",
        "a.md:4: ERROR [path] path does not exist: gone/two.py",
        "b.md:1: WARNING [path] absolute path in docs is machine-specific",
        "2 error(s), 1 warning(s) in 2 file(s) scanned",
    ]


def test_cli_warnings_only_exit_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, "a.md", "`/abs/path`\n")
    monkeypatch.chdir(tmp_path)

    assert main(["scan", "."]) == 0


def test_cli_json_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, "gone/keep.txt", "")  # v3: ERROR needs an existing parent directory
    _write(tmp_path, "a.md", "`gone/one.py`\n")
    monkeypatch.chdir(tmp_path)

    code = main(["scan", ".", "--format", "json"])

    assert code == 1
    assert json.loads(capsys.readouterr().out) == {
        "version": __version__,
        "findings": [
            {
                "file": "a.md",
                "line": 1,
                "severity": "error",
                "checker": "path",
                "message": "path does not exist: gone/one.py",
                "value": "gone/one.py",
            }
        ],
        "summary": {"errors": 1, "warnings": 0, "info": 0},
    }


def test_cli_missing_scan_path_is_usage_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["scan", "does-not-exist"]) == 2
