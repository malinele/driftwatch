from pathlib import Path

from driftwatch.config import Config
from driftwatch.models import Finding, Severity
from driftwatch.scanner import is_historical, scan


def _write(root: Path, rel: str, text: str = "") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _scan(root: Path, config: Config | None = None) -> list[Finding]:
    return scan(root, [root], config)


def _by(findings: list[Finding], checker: str) -> list[Finding]:
    return [f for f in findings if f.checker == checker]


# (1) historical files are matched by base name anywhere in the tree


def test_nested_release_notes_skip_path_claims_but_check_links(tmp_path: Path) -> None:
    _write(tmp_path, "docs/en/docs/release-notes.md", "Removed `src/gone.py`.\n\nSee [old](missing.md).\n")
    _write(tmp_path, "src/keep.py")
    findings = _scan(tmp_path)
    assert {f.checker for f in findings} == {"link"}
    assert [f.severity for f in findings] == [Severity.ERROR]


def test_historical_basenames_case_insensitive_anywhere(tmp_path: Path) -> None:
    names = ("a/release_notes_2.md", "b/c/CHANGES.md", "x/News.md", "deep/tree/history.md", "y/changelog-v1.md")
    assert all(is_historical(tmp_path / name, tmp_path) for name in names)
    assert not is_historical(tmp_path / "docs/guide.md", tmp_path)


# (2) a .env-style definition makes the variable user-defined for the whole document


def test_dotenv_fence_definitions_cover_whole_document(tmp_path: Path) -> None:
    text = (
        '```bash title=".env"\nDEBUG=True\nDATABASE_URL=postgres://u@h/db\nSECRET_KEY=abc\n```\n\n'
        "Later, `echo $DATABASE_URL` and `export SECRET_KEY=xyz`.\n"
    )
    _write(tmp_path, "README.md", text)
    assert _by(_scan(tmp_path), "env") == []


def test_compose_list_definition_covers_mentions(tmp_path: Path) -> None:
    text = "```yaml\nenvironment:\n  - DATABASE_URL=postgres://u@h/db\n```\n\nThen `echo $DATABASE_URL`.\n"
    _write(tmp_path, "README.md", text)
    assert _by(_scan(tmp_path), "env") == []


def test_export_anywhere_in_prose_defines_for_document(tmp_path: Path) -> None:
    text = "First run `export APP_SECRET=x` in your shell.\n\nThe app reads `$APP_SECRET`.\n"
    _write(tmp_path, "README.md", text)
    assert _by(_scan(tmp_path), "env") == []


def test_definition_in_other_document_does_not_count(tmp_path: Path) -> None:
    _write(tmp_path, "docs/setup.md", "```ini\nDATABASE_URL=sqlite:///x\n```\n")
    _write(tmp_path, "README.md", "Run `echo $DATABASE_URL`.\n")
    assert [f.severity for f in _by(_scan(tmp_path), "env")] == [Severity.ERROR]


# (3) external-tool prefixes are INFO


def test_tool_prefix_var_is_info(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "Set `GEDIT_X=1` and `KUBECONFIG=~/.kube/c`.\n")
    findings = _by(_scan(tmp_path), "env")
    assert [f.severity for f in findings] == [Severity.INFO, Severity.INFO]
    assert all("external tool" in f.message for f in findings)


def test_tool_prefix_extra_from_config(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "Set `ACME_TOKEN=1`.\n")
    assert [f.severity for f in _by(_scan(tmp_path), "env")] == [Severity.ERROR]
    config = Config(env_tool_prefixes_extra=("ACME_",))
    assert [f.severity for f in _by(_scan(tmp_path, config), "env")] == [Severity.INFO]


# (4) a link target found under the root by its full relative suffix is a WARNING


def test_link_suffix_found_elsewhere_is_warning(tmp_path: Path) -> None:
    _write(tmp_path, "themes/mkdocs/img/favicon.ico")
    _write(tmp_path, "guide.md", "![icon](img/favicon.ico)\n")
    findings = _by(_scan(tmp_path), "link")
    assert [(f.severity, f.message) for f in findings] == [
        (
            Severity.WARNING,
            "link target not found at the stated location; found at: themes/mkdocs/img/favicon.ico",
        )
    ]


def test_link_basename_only_match_stays_error(tmp_path: Path) -> None:
    _write(tmp_path, "themes/mkdocs/icons/favicon.ico")
    _write(tmp_path, "guide.md", "![icon](img/favicon.ico)\n")
    assert [f.severity for f in _by(_scan(tmp_path), "link")] == [Severity.ERROR]


# (5) python -m of a module the document itself packages


def test_python_module_with_packaging_context_is_info(tmp_path: Path) -> None:
    text = (
        '```toml title="pyproject.toml"\n[project]\nname = "rick-portal"\n```\n\n'
        "```bash\npython -m rick_portal\n```\n"
    )
    _write(tmp_path, "README.md", text)
    findings = _by(_scan(tmp_path), "command")
    assert [f.severity for f in findings] == [Severity.INFO]
    assert findings[0].message.startswith("module built by this tutorial")


def test_python_module_with_matching_pip_install_is_info(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "```bash\npip install rick-portal\npython -m rick_portal\n```\n")
    assert [f.severity for f in _by(_scan(tmp_path), "command")] == [Severity.INFO]


def test_python_module_without_packaging_context_still_errors(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "```bash\npip install other-thing\npython -m rick_portal\n```\n")
    findings = _by(_scan(tmp_path), "command")
    assert [(f.severity, f.message) for f in findings] == [
        (Severity.ERROR, "documented python module not found: rick_portal")
    ]


# (6) one finding per file:line:value even when two checkers or two paths claim it


def _pydantic_repo(root: Path) -> None:
    _write(root, "pyproject.toml")
    _write(root, "tests/test_a.py")


def test_double_emit_link_and_path_collapse_to_one(tmp_path: Path) -> None:
    _pydantic_repo(tmp_path)
    _write(tmp_path, "docs/a.md", "Run `pydantic-core` checks in [`tests/pydantic-core/`](tests/pydantic-core/).\n")
    hits = [f for f in _scan(tmp_path) if f.claim.value == "tests/pydantic-core/"]
    assert [(f.claim.location.line, f.severity) for f in hits] == [(1, Severity.ERROR)]


def test_double_emit_second_inline_code_collapses_to_one(tmp_path: Path) -> None:
    _pydantic_repo(tmp_path)
    _write(tmp_path, "docs/a.md", "Use [`pydantic-core`](tests/pydantic-core/) and `tests/pydantic-core/`.\n")
    hits = [f for f in _scan(tmp_path) if f.claim.value == "tests/pydantic-core/"]
    assert len(hits) == 1
    assert hits[0].severity is Severity.ERROR


def test_double_emit_symlinked_doc_collapses_to_one(tmp_path: Path) -> None:
    _pydantic_repo(tmp_path)
    _write(tmp_path, "CONTRIBUTING.md", "Edit `pydantic-core` and `tests/pydantic-core/`.\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "contributing.md").symlink_to(tmp_path / "CONTRIBUTING.md")
    hits = [f for f in _scan(tmp_path) if f.claim.value == "tests/pydantic-core/"]
    assert len(hits) == 1


# (7) generated search index


def test_search_index_json_is_info(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "The site ships `search_index.json`.\n")
    findings = _by(_scan(tmp_path), "path")
    assert [(f.severity, f.message) for f in findings] == [
        (Severity.INFO, "common ecosystem file, not present in this repo")
    ]
