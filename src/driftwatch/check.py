"""Checker framework and the path-existence checker."""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Protocol

from driftwatch.markdown import parse_markdown
from driftwatch.models import Claim, ClaimKind, Finding, Severity


class Checker(Protocol):
    name: str

    def check(self, claims: list[Claim], root: Path) -> list[Finding]: ...


class PathChecker:
    """Verifies that claimed paths exist relative to the repo root or the doc's directory."""

    name = "path"

    def check(self, claims: list[Claim], root: Path) -> list[Finding]:
        findings: list[Finding] = []
        for claim in claims:
            if claim.kind is not ClaimKind.PATH:
                continue
            finding = self._check_one(claim, root)
            if finding is not None:
                findings.append(finding)
        return findings

    def _check_one(self, claim: Claim, root: Path) -> Finding | None:
        if Path(claim.value).is_absolute():
            return Finding(claim, Severity.WARNING, "absolute path in docs is machine-specific", self.name)
        doc_dir = claim.location.file.parent
        if (root / claim.value).exists() or (doc_dir / claim.value).exists():
            return None
        return Finding(claim, Severity.ERROR, f"path does not exist: {claim.value}", self.name)


MAX_SUGGESTIONS = 3
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_NON_SLUG_CHARS = re.compile(r"[^\w\- ]")


def slugify(text: str) -> str:
    """GitHub-style heading slug: lowercase, punctuation dropped (hyphens kept), spaces to hyphens."""
    text = _MD_LINK.sub(r"\1", text).strip().lower()
    return _NON_SLUG_CHARS.sub("", text).replace(" ", "-")


def heading_slugs(headings: list[str]) -> list[str]:
    """Slugs for ``headings`` in order; repeated slugs get ``-1``, ``-2`` ... suffixes like GitHub."""
    used: set[str] = set()
    counts: dict[str, int] = {}
    slugs: list[str] = []
    for text in headings:
        base = slugify(text)
        slug = base
        while slug in used:
            counts[base] = counts.get(base, 0) + 1
            slug = f"{base}-{counts[base]}"
        used.add(slug)
        slugs.append(slug)
    return slugs


def _resolve(file_part: str, doc_file: Path, root: Path) -> Path | None:
    """Resolve a link target: doc-relative first, then repo root (a leading ``/`` means repo root)."""
    if file_part.startswith("/"):
        candidates = [root / file_part.lstrip("/")]
    else:
        candidates = [doc_file.parent / file_part, root / file_part]
    return next((c for c in candidates if c.exists()), None)


class LinkChecker:
    """Verifies that relative link and image targets exist."""

    name = "link"

    def check(self, claims: list[Claim], root: Path) -> list[Finding]:
        return [
            Finding(claim, Severity.ERROR, f"link target does not exist: {claim.value}", self.name)
            for claim in claims
            if claim.kind is ClaimKind.LINK and _resolve(claim.value, claim.location.file, root) is None
        ]


class AnchorChecker:
    """Verifies that ``path#anchor`` fragments match a heading slug in the target markdown document."""

    name = "anchor"

    def check(self, claims: list[Claim], root: Path) -> list[Finding]:
        findings: list[Finding] = []
        cache: dict[Path, list[str] | None] = {}
        for claim in claims:
            if claim.kind is not ClaimKind.ANCHOR:
                continue
            finding = self._check_one(claim, root, cache)
            if finding is not None:
                findings.append(finding)
        return findings

    def _check_one(self, claim: Claim, root: Path, cache: dict[Path, list[str] | None]) -> Finding | None:
        file_part, _, anchor = claim.value.partition("#")
        target = claim.location.file if not file_part else _resolve(file_part, claim.location.file, root)
        # A missing file is the LinkChecker's finding; non-markdown targets cannot be verified.
        if target is None or not target.is_file() or target.suffix.lower() != ".md":
            return None
        if target not in cache:
            cache[target] = self._slugs(target)
        slugs = cache[target]
        if slugs is None or anchor.lower() in slugs:
            return None
        return Finding(claim, Severity.ERROR, self._message(claim.value, slugs), self.name)

    @staticmethod
    def _slugs(target: Path) -> list[str] | None:
        try:
            doc = parse_markdown(target, target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return None
        return heading_slugs([h.text for h in doc.headings])

    @staticmethod
    def _message(value: str, slugs: list[str]) -> str:
        anchor = value.partition("#")[2].lower()
        suggestions = difflib.get_close_matches(anchor, slugs, n=MAX_SUGGESTIONS, cutoff=0.4)
        suggestions += [s for s in slugs if s not in suggestions][: MAX_SUGGESTIONS - len(suggestions)]
        message = f"anchor does not match any heading: {value}"
        if suggestions:
            message += f" (available: {', '.join('#' + s for s in suggestions)})"
        return message


CHECKERS: dict[str, Checker] = {
    PathChecker.name: PathChecker(),
    LinkChecker.name: LinkChecker(),
    AnchorChecker.name: AnchorChecker(),
}
