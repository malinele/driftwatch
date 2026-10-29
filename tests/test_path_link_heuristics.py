from pathlib import Path

import pytest

from driftwatch.check import AnchorChecker, LinkChecker, PathChecker
from driftwatch.extract import extract_link_claims, extract_path_claims
from driftwatch.markdown import parse_markdown
from driftwatch.models import Finding, Severity


def _write(root: Path, rel: str, text: str = "") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _path_findings(root: Path, text: str) -> list[Finding]:
    doc = _write(root, "doc.md", text)
    return PathChecker().check(extract_path_claims(parse_markdown(doc, text)), root)


def _link_findings(root: Path, text: str) -> list[Finding]:
    doc = _write(root, "doc.md", text)
    return LinkChecker().check(extract_link_claims(parse_markdown(doc, text)), root)


@pytest.mark.parametrize("token", ['b"HTTP/1.1"', "b'HTTP/1.1'", 'say"a/b.py', "it's/a.py"])
def test_quoted_tokens_are_not_claimed(tmp_path: Path, token: str) -> None:
    text = f"`{token}`\n"
    doc = _write(tmp_path, "doc.md", text)

    assert extract_path_claims(parse_markdown(doc, text)) == []


def test_home_relative_path_is_info_and_not_stat(tmp_path: Path) -> None:
    findings = _path_findings(tmp_path, "Config: `~/.config/app/conf.toml`\n")

    assert [(f.severity, f.message) for f in findings] == [(Severity.INFO, "user-local path, not checked")]


def test_placeholder_name_is_warning(tmp_path: Path) -> None:
    findings = _path_findings(tmp_path, "Edit `path/to/example.py` or `foo.txt`.\n")

    assert [(f.severity, f.message) for f in findings] == [(Severity.WARNING, "placeholder path in docs")] * 2


def test_basename_elsewhere_is_warning_with_found_path(tmp_path: Path) -> None:
    _write(tmp_path, "src/pkg/util.py")

    findings = _path_findings(tmp_path, "See `lib/util.py`.\n")

    assert [(f.severity, f.message) for f in findings] == [
        (Severity.WARNING, "path does not exist at the stated location; found at: src/pkg/util.py")
    ]


def test_basename_fallback_picks_first_match_deterministically(tmp_path: Path) -> None:
    _write(tmp_path, "b/util.py")
    _write(tmp_path, "a/util.py")

    findings = _path_findings(tmp_path, "See `lib/util.py`.\n")

    assert findings[0].message.endswith("found at: a/util.py")


def test_genuinely_missing_path_is_still_error(tmp_path: Path) -> None:
    findings = _path_findings(tmp_path, "See `src/gone.py`.\n")

    assert [(f.severity, f.message) for f in findings] == [(Severity.ERROR, "path does not exist: src/gone.py")]


def test_extensionless_link_to_markdown_file_passes(tmp_path: Path) -> None:
    _write(tmp_path, "guide.md")

    assert _link_findings(tmp_path, "[g](guide)\n") == []


def test_extensionless_link_to_dir_with_index_passes(tmp_path: Path) -> None:
    _write(tmp_path, "docs/api/index.md")

    assert _link_findings(tmp_path, "[a](docs/api)\n") == []


def test_extensionless_link_to_dir_with_readme_passes(tmp_path: Path) -> None:
    _write(tmp_path, "docs/api/README.md")

    assert _link_findings(tmp_path, "[a](docs/api/)\n") == []


def test_missing_extensionless_link_is_error(tmp_path: Path) -> None:
    findings = _link_findings(tmp_path, "[g](nowhere)\n")

    assert [(f.severity, f.message) for f in findings] == [(Severity.ERROR, "link target does not exist: nowhere")]


def test_anchor_checker_resolves_extensionless_target(tmp_path: Path) -> None:
    _write(tmp_path, "guide.md", "# Setup\n")
    text = "[ok](guide#setup) [bad](guide#nope)\n"
    doc = _write(tmp_path, "doc.md", text)

    findings = AnchorChecker().check(extract_link_claims(parse_markdown(doc, text)), tmp_path)

    assert [f.claim.value for f in findings] == ["guide#nope"]
