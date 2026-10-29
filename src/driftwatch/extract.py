"""Extract verifiable claims from parsed markdown documents."""

from __future__ import annotations

import re
from urllib.parse import unquote

from driftwatch.markdown import MarkdownDocument
from driftwatch.models import Claim, ClaimKind, Location

PATH_EXTENSIONS = (
    ".py", ".md", ".toml", ".yaml", ".yml", ".json", ".txt", ".sh",
    ".cfg", ".ini", ".html", ".css", ".js", ".ts", ".png", ".lock",
)  # fmt: skip
MAX_TOKEN_LENGTH = 200
TRAILING_PUNCTUATION = ".,;:!?)]}'\""
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:(//|[^/\s]*@)")
_WHITESPACE = re.compile(r"\s")
_ANY_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*:|//)")


def _looks_like_path(token: str) -> bool:
    if not token or len(token) >= MAX_TOKEN_LENGTH:
        return False
    if _WHITESPACE.search(token) or token.startswith("-"):
        return False
    if "*" in token or "{" in token or _SCHEME.match(token):
        return False
    return "/" in token or token.lower().endswith(PATH_EXTENSIONS)


def _source_lines(doc: MarkdownDocument) -> list[str]:
    try:
        return doc.path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []


def extract_path_claims(doc: MarkdownDocument) -> list[Claim]:
    """Return a PATH claim for every inline code span that looks like a repo path."""
    lines = _source_lines(doc)
    claims: list[Claim] = []
    for code in doc.inline_codes:
        token = code.text.strip().rstrip(TRAILING_PUNCTUATION)
        if not _looks_like_path(token):
            continue
        context = lines[code.line - 1] if 0 < code.line <= len(lines) else ""
        claims.append(
            Claim(
                kind=ClaimKind.PATH,
                value=token,
                location=Location(file=doc.path, line=code.line),
                context=context,
            )
        )
    return claims


def _split_link_target(target: str) -> tuple[str, str] | None:
    """Split a markdown link target into ``(file, anchor)``; None for external or empty targets."""
    target = target.strip()
    if not target or _ANY_SCHEME.match(target):
        return None
    file_part, _, anchor = target.partition("#")
    file_part = unquote(file_part.partition("?")[0])
    return file_part, unquote(anchor)


def extract_link_claims(doc: MarkdownDocument) -> list[Claim]:
    """Return LINK claims for relative file targets and ANCHOR claims (``path#anchor``) for fragments.

    External links (http/https/mailto and any other scheme) are out of v1 scope and skipped.
    """
    lines = _source_lines(doc)
    claims: list[Claim] = []
    for link in doc.links:
        parts = _split_link_target(link.target)
        if parts is None:
            continue
        file_part, anchor = parts
        location = Location(file=doc.path, line=link.line)
        context = lines[link.line - 1] if 0 < link.line <= len(lines) else ""
        if file_part:
            claims.append(Claim(kind=ClaimKind.LINK, value=file_part, location=location, context=context))
        if anchor:
            claims.append(
                Claim(kind=ClaimKind.ANCHOR, value=f"{file_part}#{anchor}", location=location, context=context)
            )
    return claims
