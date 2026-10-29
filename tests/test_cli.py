from pathlib import Path

import pytest

from driftwatch.cli import main


def test_version_flag_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])

    assert excinfo.value.code == 0
    assert "0.3.0" in capsys.readouterr().out


def test_scan_empty_directory_returns_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scan", str(tmp_path)]) == 0
    assert "nothing to check yet" in capsys.readouterr().out


def test_unknown_flag_exits_with_code_two() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["scan", "--bogus"])

    assert excinfo.value.code == 2
