"""Regression tests: scan_code_env_reads finds env reads across common repo layouts."""

from pathlib import Path

from driftwatch.envscan import scan_code_env_reads


def _write(root: Path, rel: str, content: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_detects_read_in_src_layout(tmp_path: Path) -> None:
    _write(tmp_path, "src/pkg/mod.py", "import os\nvalue = os.environ['VAR_SRC']\n")

    assert "VAR_SRC" in scan_code_env_reads(tmp_path)


def test_detects_read_in_flat_package_layout(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/mod.py", "import os\nvalue = os.environ.get('VAR_FLAT')\n")

    assert "VAR_FLAT" in scan_code_env_reads(tmp_path)


def test_detects_reads_in_top_level_script_and_shell_script(tmp_path: Path) -> None:
    _write(tmp_path, "tool.py", "import os\nvalue = os.getenv('VAR_TOP')\n")
    _write(tmp_path, "scripts/x.sh", "#!/bin/sh\necho ${VAR_SH}\n")

    reads = scan_code_env_reads(tmp_path)

    assert {"VAR_TOP", "VAR_SH"} <= reads


def test_ignores_reads_inside_venv(tmp_path: Path) -> None:
    _write(tmp_path, ".venv/lib/x.py", "import os\nvalue = os.environ['VAR_VENV']\n")
    _write(tmp_path, "pkg/mod.py", "import os\nvalue = os.environ.get('VAR_FLAT')\n")

    reads = scan_code_env_reads(tmp_path)

    assert "VAR_VENV" not in reads
    assert "VAR_FLAT" in reads
