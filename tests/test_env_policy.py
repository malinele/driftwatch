from pathlib import Path

from driftwatch.config import Config
from driftwatch.models import Finding, Severity
from driftwatch.scanner import scan


def _write(root: Path, rel: str, text: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _env(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.checker == "env"]


def test_ambient_documented_not_read_is_info(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "Honours `HTTP_PROXY=x`.\n")
    findings = _env(scan(tmp_path, [tmp_path]))
    assert [f.severity for f in findings] == [Severity.INFO]
    assert "ambient environment variable" in findings[0].message


def test_non_ambient_documented_not_read_is_still_error(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "Set `MY_APP_TOKEN=x`.\n")
    assert [f.severity for f in _env(scan(tmp_path, [tmp_path]))] == [Severity.ERROR]


def test_ambient_read_by_code_never_warned_undocumented(tmp_path: Path) -> None:
    _write(tmp_path, "app.py", 'import os\nos.environ.get("SSL_CERT_FILE")\nos.environ.get("OWN_VAR")\n')
    _write(tmp_path, "README.md", "Docs mention nothing.\n")
    messages = [f.message for f in _env(scan(tmp_path, [tmp_path]))]
    assert messages == ["env var read by code but undocumented: OWN_VAR"]


def test_ambient_extra_honored(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "Uses `CORP_PROXY_URL=x`.\n")
    assert [f.severity for f in _env(scan(tmp_path, [tmp_path]))] == [Severity.ERROR]
    config = Config(ambient_extra=("CORP_PROXY_URL",))
    assert [f.severity for f in _env(scan(tmp_path, [tmp_path], config))] == [Severity.INFO]


def _changelog_repo(root: Path) -> None:
    _write(
        root,
        "CHANGELOG.md",
        "# Changes\n\nRemoved `OLD_SETTING=x`.\n\n```bash\nnonexistent-tool --go\n```\n\nSee [gone](missing.md).\n",
    )


def test_changelog_skips_env_and_command_but_keeps_links(tmp_path: Path) -> None:
    _changelog_repo(tmp_path)
    findings = scan(tmp_path, [tmp_path])
    assert {f.checker for f in findings} == {"link"}
    assert all(f.severity is Severity.ERROR for f in findings)


def test_historical_is_case_insensitive_and_covers_release_notes(tmp_path: Path) -> None:
    _write(tmp_path, "docs/Release-Notes-1.md", "Dropped `GONE_VAR=x`.\n")
    _write(tmp_path, "history.md", "Dropped `OTHER_GONE_VAR=x`.\n")
    assert scan(tmp_path, [tmp_path]) == []


def test_historical_extra_pattern(tmp_path: Path) -> None:
    _write(tmp_path, "UPDATES.md", "Dropped `GONE_VAR=x`.\n")
    assert _env(scan(tmp_path, [tmp_path]))
    assert not _env(scan(tmp_path, [tmp_path], Config(historical=("UPDATES.md",))))


def test_news_is_historical_by_default(tmp_path: Path) -> None:
    _write(tmp_path, "docs/sub/NEWS.md", "Dropped `GONE_VAR=x`.\n")
    assert scan(tmp_path, [tmp_path]) == []


def test_historical_full_scan_restores_checking(tmp_path: Path) -> None:
    _changelog_repo(tmp_path)
    findings = scan(tmp_path, [tmp_path], Config(historical_full_scan=True))
    assert {"env", "command", "link"} <= {f.checker for f in findings}
