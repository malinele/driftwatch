from pathlib import Path

import pytest

from driftwatch.baseline import DEFAULT_BASELINE, filter_baseline, load_baseline, write_baseline
from driftwatch.cli import main
from driftwatch.config import Config, ConfigError, load_config
from driftwatch.models import Severity
from driftwatch.scanner import scan


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- configuration ---------------------------------------------------------------------------


def test_defaults_without_any_config_file(tmp_path: Path) -> None:
    config = load_config(tmp_path)

    assert config == Config()
    assert config.fail_on is Severity.ERROR
    assert {"path", "link", "anchor", "command", "env"} <= config.enabled_checkers


def test_pyproject_tool_table_is_read(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pyproject.toml",
        '[tool.driftwatch]\nexclude = ["docs/*.md"]\nfail_on = "warning"\nenv_stoplist_extra = ["FOO_BAR"]\n'
        'enabled_checkers = ["path"]\n',
    )

    config = load_config(tmp_path)

    assert config.exclude == ("docs/*.md",)
    assert config.fail_on is Severity.WARNING
    assert config.env_stoplist_extra == ("FOO_BAR",)
    assert config.enabled_checkers == frozenset({"path"})


def test_driftwatch_toml_overrides_pyproject(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", '[tool.driftwatch]\nfail_on = "warning"\nexclude = ["a.md"]\n')
    _write(tmp_path, "driftwatch.toml", 'fail_on = "info"\n')

    config = load_config(tmp_path)

    assert config.fail_on is Severity.INFO
    assert config.exclude == ("a.md",)  # keys not overridden still come from pyproject


def test_unknown_key_raises_config_error(tmp_path: Path) -> None:
    _write(tmp_path, "driftwatch.toml", 'fail_onn = "error"\n')

    with pytest.raises(ConfigError, match="fail_onn"):
        load_config(tmp_path)


def test_unknown_checker_and_bad_severity_raise(tmp_path: Path) -> None:
    _write(tmp_path, "driftwatch.toml", 'enabled_checkers = ["nope"]\n')
    with pytest.raises(ConfigError, match="nope"):
        load_config(tmp_path)

    _write(tmp_path, "driftwatch.toml", 'fail_on = "fatal"\n')
    with pytest.raises(ConfigError, match="fail_on"):
        load_config(tmp_path)


def test_cli_exits_two_on_bad_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, "driftwatch.toml", "bogus = 1\n")
    monkeypatch.chdir(tmp_path)

    assert main(["scan", "."]) == 2
    assert "bogus" in capsys.readouterr().err


def test_exclude_glob_skips_file(tmp_path: Path) -> None:
    _write(tmp_path, "keep.md", "`missing/a.py`\n")
    _write(tmp_path, "docs/skip.md", "`missing/b.py`\n")

    findings = scan(tmp_path, [tmp_path], Config(exclude=("docs/*.md",)))

    assert [f.claim.location.file.name for f in findings] == ["keep.md"]


def test_enabled_checkers_are_honored(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "`missing/a.py` and `UNREAD_VAR`\n")

    findings = scan(tmp_path, [tmp_path], Config(enabled_checkers=frozenset({"path"})))

    assert {f.checker for f in findings} == {"path"}


def test_fail_on_warning_makes_warnings_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, "README.md", "`/etc/passwd`\n")  # absolute path: warning only
    monkeypatch.chdir(tmp_path)
    assert main(["scan", "."]) == 0

    _write(tmp_path, "driftwatch.toml", 'fail_on = "warning"\n')

    assert main(["scan", "."]) == 1


# --- inline ignores --------------------------------------------------------------------------


def test_ignore_comment_on_same_line(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "`missing/a.py` <!-- driftwatch: ignore -->\n")

    assert scan(tmp_path, [tmp_path]) == []


def test_ignore_comment_covers_next_line_only(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "<!-- driftwatch: ignore -->\n`missing/a.py`\n\n`missing/b.py`\n")

    findings = scan(tmp_path, [tmp_path])

    assert [f.claim.value for f in findings] == ["missing/b.py"]


def test_per_checker_ignore_suppresses_only_named_checkers(tmp_path: Path) -> None:
    line = "`missing/a.py` and `UNREAD_VAR`"
    _write(tmp_path, "README.md", f"{line} <!-- driftwatch: ignore=path -->\n")
    assert {f.checker for f in scan(tmp_path, [tmp_path])} == {"env"}

    _write(tmp_path, "README.md", f"{line} <!-- driftwatch: ignore=path,env -->\n")
    assert scan(tmp_path, [tmp_path]) == []


def test_per_checker_ignore_on_previous_line(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "<!-- driftwatch: ignore=path -->\n`missing/a.py` and `UNREAD_VAR`\n")

    assert {f.checker for f in scan(tmp_path, [tmp_path])} == {"env"}


# --- baseline --------------------------------------------------------------------------------


def test_write_baseline_then_rerun_suppresses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, "README.md", "`missing/a.py`\n")
    monkeypatch.chdir(tmp_path)
    assert main(["scan", "."]) == 1

    assert main(["scan", ".", "--write-baseline"]) == 0
    assert (tmp_path / DEFAULT_BASELINE).is_file()
    capsys.readouterr()

    assert main(["scan", "."]) == 0
    assert "1 suppressed by baseline" in capsys.readouterr().out


def test_baseline_entries_exclude_message_text(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "`missing/a.py`\n")
    findings = scan(tmp_path, [tmp_path])
    path = tmp_path / "base.json"

    write_baseline(findings, path, tmp_path)

    text = path.read_text(encoding="utf-8")
    assert "does not exist" not in text
    assert load_baseline(path) == {("README.md", 1, "path", "missing/a.py")}


def test_new_findings_are_not_suppressed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, "README.md", "`missing/a.py`\n")
    monkeypatch.chdir(tmp_path)
    assert main(["scan", ".", "--write-baseline"]) == 0

    _write(tmp_path, "README.md", "`missing/a.py`\n`missing/new.py`\n")

    assert main(["scan", "."]) == 1


def test_edited_doc_shifting_line_escapes_baseline(tmp_path: Path) -> None:
    """Line-sensitivity is intentional in v1: the key includes the line, so a shifted finding resurfaces."""
    _write(tmp_path, "README.md", "`missing/a.py`\n")
    baseline = tmp_path / "base.json"
    write_baseline(scan(tmp_path, [tmp_path]), baseline, tmp_path)

    _write(tmp_path, "README.md", "intro line\n`missing/a.py`\n")
    result = filter_baseline(scan(tmp_path, [tmp_path]), load_baseline(baseline), tmp_path)

    assert result.suppressed == 0
    assert len(result.remaining) == 1


def test_explicit_baseline_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, "README.md", "`missing/a.py`\n")
    monkeypatch.chdir(tmp_path)
    assert main(["scan", ".", "--write-baseline", "--baseline", "custom.json"]) == 0

    assert main(["scan", ".", "--baseline", "custom.json"]) == 0
    assert not (tmp_path / DEFAULT_BASELINE).exists()


def test_corrupt_baseline_exits_two(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, "README.md", "ok\n")
    _write(tmp_path, DEFAULT_BASELINE, "not json")
    monkeypatch.chdir(tmp_path)

    assert main(["scan", "."]) == 2
