from pathlib import Path

from driftwatch.models import Severity
from driftwatch.scanner import LOCALE_FALLBACK_MESSAGE, scan


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_folded_anchor_matches_accented_heading(tmp_path: Path) -> None:
    _write(tmp_path, "guide.md", "# Duración de la respuesta\n\nSee [r](#duracion-de-la-respuesta).\n")
    assert scan(tmp_path, [tmp_path]) == []


def test_accented_anchor_still_matches_itself(tmp_path: Path) -> None:
    _write(tmp_path, "guide.md", "# Duración\n\nSee [r](#duración).\n")
    assert scan(tmp_path, [tmp_path]) == []


def test_unrelated_anchor_still_errors(tmp_path: Path) -> None:
    _write(tmp_path, "guide.md", "# Duración\n\nSee [r](#otra-cosa).\n")
    assert [f.severity for f in scan(tmp_path, [tmp_path])] == [Severity.ERROR]


def test_locale_missing_file_is_warning_in_every_locale(tmp_path: Path) -> None:
    _write(tmp_path, "docs/en/other.md", "# Other\n")
    for code in ("en", "de", "es"):
        _write(tmp_path, f"docs/{code}/guide.md", "# Guide\n")
    _write(tmp_path, "docs/de/guide.md", "# Guide\n\nSee [o](other.md).\n")
    _write(tmp_path, "docs/es/guide.md", "# Guide\n\nSee [o](other.md).\n")
    findings = scan(tmp_path, [tmp_path])
    assert len(findings) == 2
    assert {(f.severity, f.message) for f in findings} == {(Severity.WARNING, LOCALE_FALLBACK_MESSAGE)}


def test_locale_fallback_with_subtag_and_encoded_target(tmp_path: Path) -> None:
    _write(tmp_path, "docs/en/my doc.md", "# Doc\n")
    _write(tmp_path, "docs/zh-Hans/guide.md", "# Guide\n\nSee [d](my%20doc.md).\n")
    findings = scan(tmp_path, [tmp_path])
    assert [(f.severity, f.message) for f in findings] == [(Severity.WARNING, LOCALE_FALLBACK_MESSAGE)]


def test_allcaps_placeholder_target_is_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "# T\n\nRead [english](ENGLISH_VERSION_URL).\n")
    assert scan(tmp_path, [tmp_path]) == []


def test_bare_url_under_scripts_is_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "scripts/template.md", "# T\n\nGo to [site](url).\n")
    assert scan(tmp_path, [tmp_path]) == []


def test_root_readme_link_into_docs_is_warning(tmp_path: Path) -> None:
    _write(tmp_path, "docs/guide.md", "# Guide\n")
    _write(tmp_path, "README.md", "# R\n\nSee [g](guide.md).\n")
    findings = scan(tmp_path, [tmp_path / "README.md"])
    assert [f.severity for f in findings] == [Severity.WARNING]
    assert "docs site" in findings[0].message and "GitHub/PyPI" in findings[0].message


def test_root_readme_link_broken_everywhere_stays_error(tmp_path: Path) -> None:
    _write(tmp_path, "docs/guide.md", "# Guide\n")
    _write(tmp_path, "README.md", "# R\n\nSee [g](nowhere.md).\n")
    assert [f.severity for f in scan(tmp_path, [tmp_path / "README.md"])] == [Severity.ERROR]
