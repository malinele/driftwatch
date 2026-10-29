from pathlib import Path

import pytest

from driftwatch.check import CHECKERS, AnchorChecker, LinkChecker, heading_slugs, slugify
from driftwatch.extract import extract_link_claims
from driftwatch.markdown import parse_markdown
from driftwatch.models import ClaimKind, Finding, Severity


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _run(root: Path, text: str) -> tuple[list[tuple[ClaimKind, str]], list[Finding]]:
    doc_path = _write(root, "README.md", text)
    claims = extract_link_claims(parse_markdown(doc_path, text))
    findings = LinkChecker().check(claims, root) + AnchorChecker().check(claims, root)
    return [(c.kind, c.value) for c in claims], findings


def test_slugify_strips_punctuation_and_lowercases() -> None:
    assert slugify("Hello, World!") == "hello-world"


@pytest.mark.parametrize(
    ("text", "slug"),
    [
        ("Already-hyphenated Title", "already-hyphenated-title"),
        ("`code` and [link](http://x.io)", "code-and-link"),
        ("A  B", "a--b"),
        ("snake_case?", "snake_case"),
    ],
)
def test_slugify_cases(text: str, slug: str) -> None:
    assert slugify(text) == slug


def test_heading_slugs_number_duplicates() -> None:
    assert heading_slugs(["X", "Y", "X", "X"]) == ["x", "y", "x-1", "x-2"]


def test_checkers_registered() -> None:
    assert isinstance(CHECKERS["link"], LinkChecker)
    assert isinstance(CHECKERS["anchor"], AnchorChecker)


def test_extracts_link_and_anchor_claims(tmp_path: Path) -> None:
    claims, _ = _run(tmp_path, "[a](docs/x.md) [b](#top) [c](docs/x.md#sec)\n")

    assert claims == [
        (ClaimKind.LINK, "docs/x.md"),
        (ClaimKind.ANCHOR, "#top"),
        (ClaimKind.LINK, "docs/x.md"),
        (ClaimKind.ANCHOR, "docs/x.md#sec"),
    ]


def test_external_links_are_ignored(tmp_path: Path) -> None:
    claims, findings = _run(
        tmp_path, "[a](https://e.com/x#y) [b](http://e.com) [c](mailto:a@b.io) [d](//cdn.io/x)\n"
    )

    assert claims == []
    assert findings == []


def test_valid_relative_link(tmp_path: Path) -> None:
    _write(tmp_path, "docs/x.md", "# X\n")

    _, findings = _run(tmp_path, "[x](docs/x.md)\n")

    assert findings == []


def test_broken_relative_link_is_error(tmp_path: Path) -> None:
    _, findings = _run(tmp_path, "intro\n\n[x](docs/missing.md)\n")

    assert [(f.severity, f.claim.location.line, f.checker) for f in findings] == [(Severity.ERROR, 3, "link")]
    assert "docs/missing.md" in findings[0].message


def test_doc_relative_link_resolves(tmp_path: Path) -> None:
    _write(tmp_path, "docs/other.md", "# O\n")
    text = "[o](other.md)\n"
    doc = _write(tmp_path, "docs/guide.md", text)

    claims = extract_link_claims(parse_markdown(doc, text))

    assert LinkChecker().check(claims, tmp_path) == []


def test_image_links_are_checked_as_files(tmp_path: Path) -> None:
    _write(tmp_path, "img/ok.png", "x")

    _, findings = _run(tmp_path, "![ok](img/ok.png)\n\n![bad](img/bad.png)\n")

    assert [f.claim.value for f in findings] == ["img/bad.png"]


def test_same_doc_anchors(tmp_path: Path) -> None:
    _, findings = _run(tmp_path, "# Intro\n\n[ok](#intro) [bad](#nope)\n")

    assert [(f.claim.value, f.severity, f.checker) for f in findings] == [("#nope", Severity.ERROR, "anchor")]


def test_missing_anchor_suggests_up_to_three_headings(tmp_path: Path) -> None:
    _, findings = _run(tmp_path, "# One\n## Two\n## Three\n## Four\n\n[x](#nope)\n")

    message = findings[0].message
    assert message.count("#") == 4  # the claimed "#nope" plus three suggestions
    assert "#one" in message


def test_cross_doc_anchor_valid_and_broken(tmp_path: Path) -> None:
    _write(tmp_path, "docs/x.md", "# X\n\n## Install Steps\n")

    _, findings = _run(tmp_path, "[ok](docs/x.md#install-steps) [bad](docs/x.md#usage)\n")

    assert [f.claim.value for f in findings] == ["docs/x.md#usage"]
    assert "#install-steps" in findings[0].message


def test_duplicate_heading_anchors(tmp_path: Path) -> None:
    text = "# X\n\n# X\n\n[a](#x) [b](#x-1) [c](#x-2)\n"

    _, findings = _run(tmp_path, text)

    assert [f.claim.value for f in findings] == ["#x-2"]


def test_anchor_into_missing_file_only_reports_link(tmp_path: Path) -> None:
    _, findings = _run(tmp_path, "[x](gone.md#sec)\n")

    assert [f.checker for f in findings] == ["link"]
