from pathlib import Path

from driftwatch.config import Config
from driftwatch.models import Finding
from driftwatch.scanner import scan


def _write(root: Path, rel: str, text: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _repo(root: Path, *, mkdocs_yml: bool = True) -> None:
    if mkdocs_yml:
        _write(root, "mkdocs.yml", "site_name: x\n")
    _write(root, "docs/async.md", "# Async\n\nSee [transports](../advanced/transports).\n")
    _write(root, "docs/advanced/transports.md", "# Transports\n\n## Retries\n")


def _errors(root: Path, config: Config | None = None) -> list[Finding]:
    return [f for f in scan(root, [root], config) if f.checker in {"link", "anchor"}]


def test_directory_url_link_passes_in_mkdocs_mode(tmp_path: Path) -> None:
    _repo(tmp_path)
    assert _errors(tmp_path) == []


def test_same_link_errors_without_mkdocs_yml(tmp_path: Path) -> None:
    _repo(tmp_path, mkdocs_yml=False)
    findings = _errors(tmp_path)
    assert [f.claim.value for f in findings] == ["../advanced/transports"]


def test_missing_target_errors_in_both_modes(tmp_path: Path) -> None:
    _repo(tmp_path)
    _write(tmp_path, "docs/async.md", "# Async\n\n[gone](../advanced/missing)\n")
    assert len(_errors(tmp_path)) == 1
    (tmp_path / "mkdocs.yml").unlink()
    assert len(_errors(tmp_path)) == 1


def test_nested_page_resolves_sibling_directory(tmp_path: Path) -> None:
    _repo(tmp_path)
    _write(tmp_path, "docs/a/b.md", "# B\n\n[d](../c/d)\n")
    _write(tmp_path, "docs/c/d.md", "# D\n")
    assert _errors(tmp_path) == []


def test_index_page_maps_to_parent_directory(tmp_path: Path) -> None:
    _repo(tmp_path)
    _write(tmp_path, "docs/a/index.md", "# A\n\n[d](c/d)\n")
    _write(tmp_path, "docs/a/c/d.md", "# D\n")
    assert _errors(tmp_path) == []


def test_config_false_disables_mode(tmp_path: Path) -> None:
    _repo(tmp_path)
    findings = _errors(tmp_path, Config(mkdocs=False))
    assert [f.claim.value for f in findings] == ["../advanced/transports"]


def test_docs_dir_is_read_from_mkdocs_yml(tmp_path: Path) -> None:
    _write(tmp_path, "mkdocs.yml", "site_name: x\ndocs_dir: 'site-src'  # comment\n")
    _write(tmp_path, "site-src/async.md", "[t](../advanced/transports)\n")
    _write(tmp_path, "site-src/advanced/transports.md", "# T\n")
    assert _errors(tmp_path) == []


def test_anchor_follows_mkdocs_target_mapping(tmp_path: Path) -> None:
    _repo(tmp_path)
    _write(tmp_path, "docs/async.md", "[ok](../advanced/transports#retries) [bad](../advanced/transports#nope)\n")
    findings = _errors(tmp_path)
    assert [f.claim.value for f in findings] == ["../advanced/transports#nope"]
