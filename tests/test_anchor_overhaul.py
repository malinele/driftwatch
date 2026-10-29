from pathlib import Path

from driftwatch.check import DYNAMIC_ANCHOR_MESSAGE, AnchorChecker, slugify
from driftwatch.extract import extract_link_claims
from driftwatch.markdown import parse_markdown
from driftwatch.models import Finding, Severity


def _check(root: Path, target_text: str, fragment: str) -> list[Finding]:
    (root / "target.md").write_text(target_text, encoding="utf-8")
    source = root / "README.md"
    text = f"[x](target.md#{fragment})\n"
    source.write_text(text, encoding="utf-8")
    return AnchorChecker().check(extract_link_claims(parse_markdown(source, text)), root)


def test_attr_list_heading_id_registers_id_not_raw_slug(tmp_path: Path) -> None:
    page = "## Installation { #installation }\n"

    assert _check(tmp_path, page, "installation") == []
    assert slugify("Installation { #installation }") == "installation"
    assert _check(tmp_path, page, "installation--installation-")[0].severity is Severity.ERROR


def test_attr_list_custom_id_differs_from_heading_text(tmp_path: Path) -> None:
    assert _check(tmp_path, "## Setup { #custom-id }\n", "custom-id") == []


def test_standalone_attr_list_ids_register(tmp_path: Path) -> None:
    page = "[](){#typeddict}\n\n::: pkg.TypedDict\n\n{#field-after-validator}\nText\n"

    assert _check(tmp_path, page, "typeddict") == []
    assert _check(tmp_path, page, "field-after-validator") == []


def test_html_anchor_id_and_name_register_anywhere(tmp_path: Path) -> None:
    page = '# T\n\ntext <a id="x"></a>\n\n<a name="implementing_get_pydantic_core_schema"></a>\n'

    assert _check(tmp_path, page, "x") == []
    assert _check(tmp_path, page, "implementing_get_pydantic_core_schema") == []


def test_heading_with_html_tags_is_slugged_without_tags(tmp_path: Path) -> None:
    page = '## <a name="foo"></a>Using the <code>Model</code> class\n'

    assert _check(tmp_path, page, "using-the-model-class") == []
    assert _check(tmp_path, page, "foo") == []


def test_emphasis_markers_stripped_from_slug(tmp_path: Path) -> None:
    assert _check(tmp_path, "### How _Black_ wraps lines\n", "how-black-wraps-lines") == []
    assert slugify("A **bold** move") == "a-bold-move"
    assert slugify("snake_case name") == "snake_case-name"


def test_entities_and_apostrophes_dropped_from_slug() -> None:
    assert slugify("Tips &amp; tricks") == "tips--tricks"
    assert slugify("Don't") == "dont"
    assert slugify("It’s here") == "its-here"


def test_broken_anchor_into_dynamic_page_is_info(tmp_path: Path) -> None:
    for marker in ("::: pkg.module\n", "Hello {{ name }}\n", '--8<-- "snippet.md"\n'):
        findings = _check(tmp_path, f"# Title\n\n{marker}", "gone")

        assert [(f.severity, f.message) for f in findings] == [(Severity.INFO, DYNAMIC_ANCHOR_MESSAGE)]


def test_broken_anchor_in_static_page_is_error_with_suggestions(tmp_path: Path) -> None:
    findings = _check(tmp_path, "## Exit codes\n\n## Usage\n", "exit-code")

    assert len(findings) == 1
    assert findings[0].severity is Severity.ERROR
    assert "#exit-codes" in findings[0].message
