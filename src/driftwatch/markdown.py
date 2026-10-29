"""Markdown extraction layer built on markdown-it-py."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.token import Token


@dataclass(frozen=True)
class CodeFence:
    language: str
    content: str
    start_line: int  # 1-based line of the opening fence
    title: str = ""  # file name the fence is labelled with (``title="x.py"`` or ``python x.py``)


@dataclass(frozen=True)
class InlineCode:
    text: str
    line: int


@dataclass(frozen=True)
class MdLink:
    target: str
    line: int
    is_image: bool = False


@dataclass(frozen=True)
class Heading:
    text: str
    level: int
    line: int


@dataclass(frozen=True)
class MarkdownDocument:
    path: Path
    code_fences: list[CodeFence]
    inline_codes: list[InlineCode]
    links: list[MdLink]
    headings: list[Heading]
    anchor_ids: list[str] = field(default_factory=list)  # attr_list ``{#id}`` and HTML ``id=``/``name=`` values
    dynamic: bool = False  # content generated or included by plugins (``:::``, ``{{ }}``, ``--8<--``)


_ATTR_ID = re.compile(r"\{:?[^{}\n]*?#([\w:.\-]+)[^{}\n]*\}")
_HTML_ID = re.compile(r"""<[A-Za-z][^<>]*?\s(?:id|name)\s*=\s*(?:"([^"]+)"|'([^']+)')""")
_DYNAMIC = re.compile(r"^\s*:::\s|(?<!\$)\{\{|--8<--", re.MULTILINE)
_TITLE_ATTR = re.compile(r"""\btitle\s*=\s*(?:"([^"]*)"|'([^']*)')""")


def fence_title(info: str) -> str:
    """File name a fence info string labels the fence with, or ``""``.

    Recognises ``title="NAME"`` (mkdocs/material) and a bare filename token after the language
    (```` ```python main.py ````); a token counts as a filename when it has no ``=``, ``{`` or ``:``.
    """
    match = _TITLE_ATTR.search(info)
    if match:
        return (match.group(1) or match.group(2) or "").strip()
    tokens = info.split()
    for token in tokens[1:]:
        if "." in token.strip(".") and not any(c in token for c in "={:"):
            return token
    return ""


def _first_line(token: Token) -> int:
    """1-based start line of a block token (``token.map`` is 0-based)."""
    return token.map[0] + 1 if token.map else 1


def _walk_inline(
    token: Token,
    codes: list[InlineCode],
    links: list[MdLink],
) -> None:
    line = _first_line(token)
    for child in token.children or []:
        if child.type in ("softbreak", "hardbreak"):
            line += 1
        elif child.type == "code_inline":
            codes.append(InlineCode(text=child.content, line=line))
        elif child.type == "link_open":
            href = child.attrGet("href")
            if isinstance(href, str):
                links.append(MdLink(target=href, line=line))
        elif child.type == "image":
            src = child.attrGet("src")
            if isinstance(src, str):
                links.append(MdLink(target=src, line=line, is_image=True))


def parse_markdown(path: Path, text: str) -> MarkdownDocument:
    """Parse ``text`` (the contents of ``path``) into a :class:`MarkdownDocument`.

    All line numbers are 1-based. Fences nested in lists or blockquotes are
    included. Indented code blocks are not treated as fences.

    v1 scope: ``[text](target)`` links and ``![alt](src)`` images are the
    supported syntax. markdown-it resolves reference-style links
    (``[text][ref]``) to their definition's target, so they are reported at
    the line of the usage; the definition line itself is not a link. Autolinks
    (``<http://...>``) are not extracted. Code nested inside link text is
    reported as inline code; code inside image alt text is not.
    """
    tokens = MarkdownIt("commonmark").enable("table").parse(text)
    fences: list[CodeFence] = []
    codes: list[InlineCode] = []
    links: list[MdLink] = []
    headings: list[Heading] = []

    for index, token in enumerate(tokens):
        if token.type == "fence":
            info = token.info.strip()
            language = info.split(maxsplit=1)[0] if info else ""
            fences.append(
                CodeFence(
                    language=language,
                    content=token.content,
                    start_line=_first_line(token),
                    title=fence_title(info),
                )
            )
        elif token.type == "heading_open":
            inline = tokens[index + 1]
            headings.append(Heading(text=inline.content, level=int(token.tag[1:]), line=_first_line(token)))
        elif token.type == "inline":
            _walk_inline(token, codes, links)

    anchor_ids = [m.group(1) for m in _ATTR_ID.finditer(text)]
    anchor_ids += [m.group(1) or m.group(2) for m in _HTML_ID.finditer(text)]
    return MarkdownDocument(
        path=path,
        code_fences=fences,
        inline_codes=codes,
        links=links,
        headings=headings,
        anchor_ids=anchor_ids,
        dynamic=_DYNAMIC.search(text) is not None,
    )
