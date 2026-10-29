"""Checker framework and the path-existence checker."""

from __future__ import annotations

import difflib
import re
import shlex
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from driftwatch.envscan import scan_code_env_reads
from driftwatch.markdown import parse_markdown
from driftwatch.models import Claim, ClaimKind, Finding, Location, Severity


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


KNOWN_COMMON = frozenset({"git", "docker", "make", "curl", "pip", "npm", "node", "uvicorn", "pytest"})
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _is_python(token: str) -> bool:
    return token in ("python", "python3") or token.endswith("/python")


def _python_module(claim: Claim) -> str | None:
    """The MODULE in ``python -m MODULE`` taken from the claim's source line, if present."""
    try:
        tokens = shlex.split(claim.context)
    except ValueError:
        tokens = claim.context.split()
    tokens = [t for t in tokens if not _ASSIGNMENT.match(t)]
    if len(tokens) >= 3 and tokens[0] == claim.value and tokens[1] == "-m":
        return tokens[2]
    return None


def _module_in_repo(module: str, root: Path) -> bool:
    rel = Path(*module.split("."))
    for base in (root, root / "src"):
        if (base / rel).with_suffix(".py").is_file() or (base / rel / "__init__.py").is_file():
            return True
    return False


def _module_known(module: str, root: Path) -> bool:
    """Repo module, or a stdlib / well-known tool module (resolved statically, never imported)."""
    top = module.split(".")[0]
    return _module_in_repo(module, root) or top in sys.stdlib_module_names or top in KNOWN_COMMON


class CommandChecker:
    """Statically verifies documented shell commands; nothing is ever executed."""

    name = "command"

    def check(self, claims: list[Claim], root: Path) -> list[Finding]:
        findings: list[Finding] = []
        for claim in claims:
            if claim.kind is not ClaimKind.COMMAND:
                continue
            finding = self._check_one(claim, root)
            if finding is not None:
                findings.append(finding)
        return findings

    def _check_one(self, claim: Claim, root: Path) -> Finding | None:
        token = claim.value
        if _is_python(token):
            module = _python_module(claim)
            if module is not None and not _module_known(module, root):
                return Finding(claim, Severity.ERROR, f"documented python module not found: {module}", self.name)
        if (
            (root / token).exists()
            or shutil.which(token) is not None
            or _is_python(token)
            or token in KNOWN_COMMON
        ):
            return None
        return Finding(claim, Severity.ERROR, f"documented command not found: {token}", self.name)


_ENV_WORD = re.compile(r"(?<![A-Za-z0-9_])[A-Z][A-Z0-9_]{2,}(?![A-Za-z0-9_])")


def _mentioned_words(docs: Sequence[Path]) -> set[str]:
    """Every upper-case word in the raw text of ``docs`` (a looser test than the claim heuristics)."""
    words: set[str] = set()
    for doc in docs:
        try:
            words.update(_ENV_WORD.findall(doc.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            continue
    return words


class EnvChecker:
    """Bidirectional env var check between documented ``ENV_VAR`` claims and what the code reads.

    The code-read set is built lazily from ``root`` on each call. ``docs`` is the scanned doc corpus;
    without it, only the claims count as documentation and no undocumented warnings are raised.
    """

    name = "env"

    def check(self, claims: list[Claim], root: Path, docs: Sequence[Path] = ()) -> list[Finding]:
        reads = scan_code_env_reads(root)
        env_claims = [c for c in claims if c.kind is ClaimKind.ENV_VAR]
        findings = [
            Finding(c, Severity.ERROR, f"documented env var is not read by the code: {c.value}", self.name)
            for c in env_claims
            if c.value not in reads
        ]
        if docs:
            findings.extend(self._undocumented(reads, env_claims, root, docs))
        return findings

    def _undocumented(
        self, reads: set[str], claims: list[Claim], root: Path, docs: Sequence[Path]
    ) -> list[Finding]:
        mentioned = _mentioned_words(docs) | {c.value for c in claims}
        readme = root / "README.md"
        anchor_file = readme if readme.is_file() else docs[0]
        return [
            Finding(
                Claim(ClaimKind.ENV_VAR, name, Location(file=anchor_file, line=1), context=""),
                Severity.WARNING,
                f"env var read by code but undocumented: {name}",
                self.name,
            )
            for name in sorted(reads - mentioned)
        ]


CHECKERS: dict[str, Checker] = {
    PathChecker.name: PathChecker(),
    LinkChecker.name: LinkChecker(),
    AnchorChecker.name: AnchorChecker(),
    CommandChecker.name: CommandChecker(),
    EnvChecker.name: EnvChecker(),
}
