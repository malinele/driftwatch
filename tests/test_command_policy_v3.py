"""Command policy v3: ERROR only for missing repo-defined commands and missing ./ paths under real dirs."""

from pathlib import Path

from driftwatch.check import CommandChecker
from driftwatch.extract import extract_command_claims
from driftwatch.markdown import parse_markdown
from driftwatch.models import Finding, Severity


def _write(root: Path, rel: str, text: str = "") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _scan(root: Path, text: str) -> list[Finding]:
    doc = _write(root, "README.md", text)
    claims = extract_command_claims(parse_markdown(doc, text))
    return CommandChecker().check(claims, root)


def _claimed(root: Path, text: str) -> list[str]:
    doc = _write(root, "README.md", text)
    return [c.value for c in extract_command_claims(parse_markdown(doc, text))]


def _fence(body: str, lang: str = "bash") -> str:
    return f"# Doc\n\n```{lang}\n{body}\n```\n"


def test_doc_defined_cli_has_no_finding(tmp_path: Path) -> None:
    text = "# Tutorial\n\n```bash\npip install rick-portal-gun\n```\n\nLater:\n\n```bash\nrick-portal-gun open\n```\n"
    assert _scan(tmp_path, text) == []


def test_doc_defined_via_entry_point_marker(tmp_path: Path) -> None:
    text = '# T\n\n```toml\n[project.scripts]\nawesome-cli = "pkg:app"\n```\n\n```bash\nawesome-cli run\n```\n'
    assert _scan(tmp_path, text) == []


def test_unrelated_cli_without_marker_is_info(tmp_path: Path) -> None:
    findings = _scan(tmp_path, _fence("rick-portal-gun open"))
    assert [f.severity for f in findings] == [Severity.INFO]


def test_downloaded_binary_at_repo_root_is_info(tmp_path: Path) -> None:
    findings = _scan(tmp_path, _fence("./traefik --configfile=x.toml"))
    assert [f.severity for f in findings] == [Severity.INFO]


def test_missing_script_in_existing_directory_is_error(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    findings = _scan(tmp_path, _fence("./scripts/missing.sh"))
    assert [(f.severity, f.message) for f in findings] == [
        (Severity.ERROR, "documented command not found: ./scripts/missing.sh")
    ]


def test_missing_script_in_missing_directory_is_info(tmp_path: Path) -> None:
    findings = _scan(tmp_path, _fence("./nowhere/missing.sh"))
    assert [f.severity for f in findings] == [Severity.INFO]


def test_repo_defined_console_script_with_missing_target_is_error(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", '[project]\nname = "x"\n[project.scripts]\nmycli = "ghostpkg.cli:main"\n')
    findings = _scan(tmp_path, _fence("mycli --version"))
    assert [(f.severity, f.message) for f in findings] == [(Severity.ERROR, "documented command not found: mycli")]


def test_repo_defined_console_script_with_existing_target_passes(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", '[project]\nname = "x"\n[project.scripts]\nmycli = "realpkg.cli:main"\n')
    _write(tmp_path, "src/realpkg/cli.py")
    assert _scan(tmp_path, _fence("mycli --version")) == []


def test_flag_line_is_not_a_claim(tmp_path: Path) -> None:
    assert _claimed(tmp_path, _fence("--help\n--install-completion")) == []


def test_comment_and_url_lines_are_not_claims(tmp_path: Path) -> None:
    body = "// comment\nhttps://example.com/path\ngit status"
    assert _claimed(tmp_path, _fence(body)) == ["git"]


def test_zsh_config_idioms_are_not_claims(tmp_path: Path) -> None:
    body = "fpath+=(~/.zfunc)\nzstyle ':completion:*' menu select\nautoload -Uz compinit\ncompinit"
    assert _claimed(tmp_path, _fence(body, "zsh")) == []


def test_output_guards_apply_to_prompted_console_lines(tmp_path: Path) -> None:
    assert _claimed(tmp_path, _fence("$ --help\n$ fpath+=(x)\n$ git status", "console")) == ["git"]
