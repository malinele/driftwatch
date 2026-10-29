from __future__ import annotations

from pathlib import Path

from driftwatch.markdown import CodeFence, Heading, InlineCode, MdLink, parse_markdown

PATH = Path("README.md")

SAMPLE = """\
# Title

Run `make test` or `make lint` here.
See [the guide](docs/guide.md) and ![logo](img/logo.png).

## Install

```bash
pip install driftwatch
```

- item one
  ```python
  print("hi")
  ```
- item two with `code`

Multi line paragraph
with `second` and [link](a.md#x)
end.

### Deep ``a `tick` b``
"""


def test_headings_have_exact_lines() -> None:
    doc = parse_markdown(PATH, SAMPLE)
    assert doc.headings == [
        Heading("Title", 1, 1),
        Heading("Install", 2, 6),
        Heading("Deep ``a `tick` b``", 3, 22),
    ]


def test_code_fences_language_content_and_line() -> None:
    doc = parse_markdown(PATH, SAMPLE)
    assert doc.code_fences == [
        CodeFence("bash", "pip install driftwatch\n", 8),
        CodeFence("python", 'print("hi")\n', 13),
    ]


def test_inline_codes_multiple_per_line_and_multiline_paragraph() -> None:
    doc = parse_markdown(PATH, SAMPLE)
    assert doc.inline_codes == [
        InlineCode("make test", 3),
        InlineCode("make lint", 3),
        InlineCode("code", 16),
        InlineCode("second", 19),
        InlineCode("a `tick` b", 22),
    ]


def test_links_and_images_flagged() -> None:
    doc = parse_markdown(PATH, SAMPLE)
    assert doc.links == [
        MdLink("docs/guide.md", 4, False),
        MdLink("img/logo.png", 4, True),
        MdLink("a.md#x", 19, False),
    ]


def test_inline_code_with_backticks() -> None:
    doc = parse_markdown(PATH, "x ``a ` b`` y\n")
    assert doc.inline_codes == [InlineCode("a ` b", 1)]


def test_fence_without_language() -> None:
    doc = parse_markdown(PATH, "```\nplain\n```\n")
    assert doc.code_fences == [CodeFence("", "plain\n", 1)]


def test_fence_info_string_keeps_only_language() -> None:
    doc = parse_markdown(PATH, "text\n\n~~~python title=x\npass\n~~~\n")
    assert doc.code_fences == [CodeFence("python", "pass\n", 3)]


def test_fence_inside_blockquote() -> None:
    doc = parse_markdown(PATH, "> quote\n>\n> ```sh\n> ls\n> ```\n")
    assert doc.code_fences == [CodeFence("sh", "ls\n", 3)]


def test_indented_code_block_is_not_a_fence() -> None:
    doc = parse_markdown(PATH, "para\n\n    indented\n")
    assert doc.code_fences == []


def test_code_inside_fence_is_not_inline_code() -> None:
    doc = parse_markdown(PATH, "```\n`not inline` [no](link)\n```\n")
    assert doc.inline_codes == []
    assert doc.links == []


def test_reference_style_links_resolve_to_their_target() -> None:
    doc = parse_markdown(PATH, "See [text][ref].\n\n[ref]: http://example.com\n")
    assert doc.links == [MdLink("http://example.com", 1, False)]


def test_link_in_table_cell() -> None:
    doc = parse_markdown(PATH, "| a | b |\n|---|---|\n| `x` | [l](y.md) |\n")
    assert doc.inline_codes == [InlineCode("x", 3)]
    assert doc.links == [MdLink("y.md", 3, False)]


def test_empty_document() -> None:
    doc = parse_markdown(PATH, "")
    assert doc.path == PATH
    assert doc.code_fences == []
    assert doc.inline_codes == []
    assert doc.links == []
    assert doc.headings == []


def test_prose_only_document() -> None:
    doc = parse_markdown(PATH, "Just some words.\n\nAnd more words.\n")
    assert doc.code_fences == []
    assert doc.inline_codes == []
    assert doc.links == []
    assert doc.headings == []
