"""Extract verifiable claims from parsed markdown documents."""

from __future__ import annotations

import re

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
