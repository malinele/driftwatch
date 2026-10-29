from pathlib import Path

from driftwatch.config import Config
from driftwatch.models import Severity
from driftwatch.scanner import LOCALE_FALLBACK_MESSAGE, locale_of, scan


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _broken_tree(root: Path) -> None:
    for code in ("en", "de", "es"):
        _write(root, f"docs/{code}/guide.md", "# Guide\n\nSee [x](missing.md).\n")


def test_shared_broken_link_yields_one_error_and_one_summary(tmp_path: Path) -> None:
    _broken_tree(tmp_path)
    findings = scan(tmp_path, [tmp_path])
    errors = [f for f in findings if f.severity is Severity.ERROR]
    infos = [f for f in findings if f.severity is Severity.INFO]
    assert [f.claim.location.file.relative_to(tmp_path).as_posix() for f in errors] == ["docs/en/guide.md"]
    assert len(infos) == 1
    assert infos[0].message == "same finding repeated in 2 locale copies (de, es)"


def test_translation_only_link_existing_in_source_is_warning(tmp_path: Path) -> None:
    _write(tmp_path, "docs/en/guide.md", "# Guide\n")
    _write(tmp_path, "docs/en/other.md", "# Other\n")
    _write(tmp_path, "docs/de/guide.md", "# Guide\n\nSee [o](other.md).\n")
    findings = scan(tmp_path, [tmp_path])
    assert [(f.severity, f.message) for f in findings] == [(Severity.WARNING, LOCALE_FALLBACK_MESSAGE)]


def test_locales_false_restores_raw_findings(tmp_path: Path) -> None:
    _broken_tree(tmp_path)
    findings = scan(tmp_path, [tmp_path], Config(locales=False))
    assert [f.severity for f in findings] == [Severity.ERROR] * 3


def test_bare_docs_tree_is_source_when_no_en(tmp_path: Path) -> None:
    _write(tmp_path, "docs/guide.md", "See [x](missing.md).\n")
    _write(tmp_path, "docs/fr/guide.md", "See [x](missing.md).\n")
    findings = scan(tmp_path, [tmp_path])
    assert {f.severity for f in findings} == {Severity.ERROR, Severity.INFO}
    assert len(findings) == 2


def test_locale_detection() -> None:
    assert locale_of("docs/pt-BR/a.md") is not None
    assert locale_of("i18n/de/a.md") is not None
    info = locale_of("docs/ui/a.md")
    assert info is not None and info.locale is None
