from pathlib import Path

from driftwatch.config import Config, load_config
from driftwatch.markdown import fence_title
from driftwatch.models import Finding, Severity
from driftwatch.scanner import scan


def _scan(root: Path, config: Config | None = None) -> list[Finding]:
    return [f for f in scan(root, [root], config) if f.checker == "path"]


def _write(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_create_a_file_instruction_is_info(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "Create a file named `.github/workflows/black.yml` with:\n")
    findings = _scan(tmp_path)
    assert [(f.severity, f.message) for f in findings] == [
        (Severity.INFO, "file the reader is asked to create")
    ]


def test_authoring_phrase_on_previous_line_is_info(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "Next, SAVE IT AS the following:\n`requirements.txt`\n")
    assert [f.severity for f in _scan(tmp_path)] == [Severity.INFO]


def test_authoring_phrase_two_lines_up_is_info(tmp_path: Path) -> None:
    # v3: the look-back window grew from one previous line to two.
    _write(tmp_path, "docs/index.md", "")
    _write(tmp_path, "README.md", "Name it well.\n\nSee `docs/missing.md` for more.\n")
    assert [f.severity for f in _scan(tmp_path)] == [Severity.INFO]


def test_authoring_phrase_three_lines_up_is_still_error(tmp_path: Path) -> None:
    _write(tmp_path, "docs/index.md", "")
    _write(tmp_path, "README.md", "Name it well.\n\n\nSee `docs/missing.md` for more.\n")
    assert [f.severity for f in _scan(tmp_path)] == [Severity.ERROR]


def test_existing_path_in_instruction_has_no_finding(tmp_path: Path) -> None:
    _write(tmp_path, "app.py", "")
    _write(tmp_path, "README.md", "Create a file like `app.py`.\n")
    assert _scan(tmp_path) == []


def test_fence_title_is_not_claimed(tmp_path: Path) -> None:
    text = 'Here is `main.py`:\n\n```python title="main.py"\nprint(1)\n```\n'
    _write(tmp_path, "docs/guide.md", text)
    assert _scan(tmp_path) == []


def test_fence_filename_token_is_not_claimed(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "```python myapp.py\nx = 1\n```\n\nEdit `myapp.py`.\n")
    assert _scan(tmp_path) == []


def test_fence_title_parsing() -> None:
    assert fence_title('python title="main.py"') == "main.py"
    assert fence_title("python title='a/b.py' hl_lines=\"1\"") == "a/b.py"
    assert fence_title("python index.html") == "index.html"
    assert fence_title("python {1,3}") == ""
    assert fence_title("python") == ""


def test_markdown_under_tests_is_skipped_by_default(tmp_path: Path) -> None:
    _write(tmp_path, "tests/assets/page.md", "See `docs/nowhere.md`.\n")
    _write(tmp_path, "fixtures/page.md", "See `docs/nowhere.md`.\n")
    assert _scan(tmp_path) == []


def test_markdown_under_tests_is_scanned_with_scan_tests(tmp_path: Path) -> None:
    _write(tmp_path, "docs/index.md", "")  # v3: ERROR needs an existing parent directory
    _write(tmp_path, "tests/assets/page.md", "See `docs/nowhere.md`.\n")
    _write(tmp_path, "driftwatch.toml", "scan_tests = true\n")
    findings = _scan(tmp_path, load_config(tmp_path))
    assert [f.severity for f in findings] == [Severity.ERROR]


def test_gitignored_path_is_info(tmp_path: Path) -> None:
    _write(tmp_path, ".gitignore", "# build\nout/\n*.min.js\ncoverage/report.txt\n")
    _write(
        tmp_path,
        "README.md",
        "Use `out/axios.min.js`, `lib/app.min.js` and `coverage/report.txt`.\n",
    )
    findings = _scan(tmp_path)
    assert [f.severity for f in findings] == [Severity.INFO] * 3
    assert {f.message for f in findings} == {"generated or ignored artifact"}


def test_negated_gitignore_lines_are_not_supported(tmp_path: Path) -> None:
    _write(tmp_path, ".gitignore", "*.json\n!keep.json\n")
    _write(tmp_path, "README.md", "See `keep.json`.\n")
    assert [f.severity for f in _scan(tmp_path)] == [Severity.INFO]


def test_generated_dirs_are_info_without_gitignore(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "Open `dist/axios.min.js` or `docs/build/html/index.html`.\n")
    assert [f.severity for f in _scan(tmp_path)] == [Severity.INFO, Severity.INFO]


def test_normal_missing_tracked_path_is_still_error(tmp_path: Path) -> None:
    _write(tmp_path, ".gitignore", "dist/\n")
    _write(tmp_path, "src/__init__.py", "")  # v3: ERROR needs an existing parent directory
    _write(tmp_path, "README.md", "See `src/missing_module.py`.\n")
    findings = _scan(tmp_path)
    assert [(f.severity, f.message) for f in findings] == [
        (Severity.ERROR, "path does not exist: src/missing_module.py")
    ]
