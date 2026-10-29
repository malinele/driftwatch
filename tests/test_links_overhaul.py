from pathlib import Path

from driftwatch.check import LinkChecker
from driftwatch.extract import extract_link_claims, extract_myst_labels
from driftwatch.markdown import parse_markdown
from driftwatch.models import Finding
from driftwatch.scanner import scan


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _links(root: Path, rel: str, text: str) -> list[Finding]:
    doc = _write(root, rel, text)
    return LinkChecker().check(extract_link_claims(parse_markdown(doc, text)), root)


def test_site_absolute_resolves_under_docs_dir(tmp_path: Path) -> None:
    _write(tmp_path, "docs/guide/intro.md", "# Intro\n")
    assert _links(tmp_path, "docs/index.md", "[i](/guide/intro)\n") == []


def test_site_absolute_resolves_against_scan_root_with_extension(tmp_path: Path) -> None:
    _write(tmp_path, "integrations/index.md", "# I\n")
    assert _links(tmp_path, "README.md", "[i](/integrations/index.md)\n") == []


def test_site_absolute_resolves_against_docs_tree_top(tmp_path: Path) -> None:
    _write(tmp_path, "manual/pages/a.md", "# A\n")
    assert _links(tmp_path, "manual/start/b.md", "[a](/pages/a)\n") == []


def test_site_absolute_missing_is_error(tmp_path: Path) -> None:
    _write(tmp_path, "docs/guide/intro.md", "# Intro\n")
    findings = _links(tmp_path, "docs/index.md", "[m](/missing/page)\n")
    assert [f.claim.value for f in findings] == ["/missing/page"]


def test_myst_label_target_resolves(tmp_path: Path) -> None:
    _write(tmp_path, "docs/policy.md", "(labels/x)=\n# Policy\n")
    assert _links(tmp_path, "README.md", "[t](labels/x)\n") == []


def test_link_without_myst_label_is_error(tmp_path: Path) -> None:
    _write(tmp_path, "docs/policy.md", "# Policy\n")
    findings = _links(tmp_path, "README.md", "[t](labels/x)\n")
    assert [f.claim.value for f in findings] == ["labels/x"]


def test_myst_label_detected_without_conf_py_in_full_scan(tmp_path: Path) -> None:
    _write(tmp_path, "docs/policy.md", "(labels/x)=\n# Policy\n")
    _write(tmp_path, "README.md", "[t](labels/x)\n")
    assert [f for f in scan(tmp_path, [tmp_path]) if f.checker == "link"] == []


def test_extract_myst_labels_matches_only_target_lines() -> None:
    text = "(a/b)=\n  (not)=\ntext (c)= more\n(d)=  \n"
    assert extract_myst_labels(text) == frozenset({"a/b", "d"})
