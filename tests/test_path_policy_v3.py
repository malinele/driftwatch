"""Path policy v3: tree diagrams, creation phrases, severity ladder, link text, workflow names."""

from pathlib import Path

from driftwatch.models import Finding, Severity
from driftwatch.scanner import scan

TREE_FENCE = (
    "```\n"
    "myapp/\n"
    "├── app/\n"
    "│   ├── routers/\n"
    "│   └── main.py\n"
    "└── routes.toml\n"
    "```\n"
)


def _write(root: Path, rel: str, text: str = "") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _scan(root: Path) -> list[Finding]:
    return [f for f in scan(root, [root]) if f.checker == "path"]


def test_tree_diagram_entries_are_not_claimed(tmp_path: Path) -> None:
    _write(tmp_path, "docs/keep.md")
    text = f"Project layout:\n\n{TREE_FENCE}\nPut handlers in `app/routers/` and edit `routes.toml`.\n"
    _write(tmp_path, "README.md", text)
    assert _scan(tmp_path) == []


def test_directory_listing_fence_is_a_tree(tmp_path: Path) -> None:
    text = "```\napp/\nroutes/\n```\n\nOpen `app/`.\n"
    _write(tmp_path, "README.md", text)
    assert _scan(tmp_path) == []


def test_non_tree_fence_does_not_hide_claims(tmp_path: Path) -> None:
    _write(tmp_path, "docs/keep.md")
    _write(tmp_path, "README.md", "```\nhello\n```\n\nSee `docs/missing.md`.\n")
    assert [f.severity for f in _scan(tmp_path)] == [Severity.ERROR]


def test_your_directory_phrase_is_info(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "Open your editor in your `app/` directory.\n")
    assert [(f.severity, f.message) for f in _scan(tmp_path)] == [
        (Severity.INFO, "file the reader is asked to create")
    ]


def test_creation_phrase_in_previous_two_lines_is_info(tmp_path: Path) -> None:
    _write(tmp_path, "docs/keep.md")
    _write(tmp_path, "README.md", "For example, a module named like this:\n\n`docs/extra.md`\n")
    assert [f.severity for f in _scan(tmp_path)] == [Severity.INFO]


def test_ecosystem_file_is_info(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "Install dependencies from `requirements.txt`.\n")
    assert [f.severity for f in _scan(tmp_path)] == [Severity.INFO]


def test_missing_path_without_existing_parent_is_warning(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "Edit `config/settings.yaml`.\n")
    assert [f.severity for f in _scan(tmp_path)] == [Severity.WARNING]


def test_missing_bare_file_is_warning(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "Edit `settings.yaml`.\n")
    assert [f.severity for f in _scan(tmp_path)] == [Severity.WARNING]


def test_missing_path_with_existing_parent_is_still_error(tmp_path: Path) -> None:
    _write(tmp_path, "src/real.py")
    _write(tmp_path, "README.md", "See `src/gone.py`.\n")
    assert [(f.severity, f.message) for f in _scan(tmp_path)] == [
        (Severity.ERROR, "path does not exist: src/gone.py")
    ]


def test_http_link_text_is_not_claimed(tmp_path: Path) -> None:
    text = "Read [`llms.txt`](https://example.com) and [`app/main.py`](https://example.com/x).\n"
    _write(tmp_path, "README.md", text)
    assert _scan(tmp_path) == []


def test_relative_link_text_is_still_claimed(tmp_path: Path) -> None:
    _write(tmp_path, "docs/keep.md")
    _write(tmp_path, "README.md", "Read [`docs/gone.md`](docs/keep.md).\n")
    assert [f.severity for f in _scan(tmp_path)] == [Severity.ERROR]


def test_workflow_basename_resolves_with_found_at_warning(tmp_path: Path) -> None:
    _write(tmp_path, ".github/workflows/publish.yml")
    _write(tmp_path, "README.md", "The release job lives in `publish.yml`.\n")
    assert [(f.severity, f.message) for f in _scan(tmp_path)] == [
        (
            Severity.WARNING,
            "path does not exist at the stated location; found at: .github/workflows/publish.yml",
        )
    ]
