"""Checker framework and the path-existence checker."""

from __future__ import annotations

import difflib
import os
import re
import shlex
import shutil
import sys
import tomllib
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Protocol

from driftwatch.envscan import scan_code_env_reads
from driftwatch.markdown import parse_markdown
from driftwatch.models import Claim, ClaimKind, Finding, Location, Severity


class Checker(Protocol):
    name: str

    def check(self, claims: list[Claim], root: Path) -> list[Finding]: ...


def is_within(candidate: Path, root: Path) -> bool:
    """True if ``candidate`` stays inside ``root``, lexically and after symlink resolution.

    The lexical test runs first and touches no filesystem entry, so a ``../..`` claim is rejected
    without ever being stat-ed; the realpath test then catches symlinks that point out of the root.
    """
    lexical = Path(os.path.abspath(candidate))
    base = Path(os.path.abspath(root))
    if not lexical.is_relative_to(base):
        return False
    return Path(os.path.realpath(lexical)).is_relative_to(Path(os.path.realpath(base)))


ESCAPE_MESSAGE = "path escapes the scanned repository"
PLACEHOLDER_STEMS = frozenset({"foo", "bar", "baz", "example", "sample", "myfile", "yourfile", "placeholder", "test"})
_INDEX_SKIP_DIRS = frozenset({"venv", "node_modules", "dist", "__pycache__"})


def _basename_index(root: Path) -> dict[str, str]:
    """Map each file basename under ``root`` to its first root-relative POSIX path (sorted walk)."""
    index: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in _INDEX_SKIP_DIRS)
        for filename in sorted(filenames):
            if filename not in index:
                index[filename] = (Path(dirpath) / filename).relative_to(root).as_posix()
    return index


class PathChecker:
    """Verifies that claimed paths exist relative to the repo root or the doc's directory."""

    name = "path"

    def check(self, claims: list[Claim], root: Path) -> list[Finding]:
        findings: list[Finding] = []
        index: dict[str, str] | None = None
        for claim in claims:
            if claim.kind is not ClaimKind.PATH:
                continue
            finding = self._check_one(claim, root)
            if finding is not None and finding.severity is Severity.ERROR:
                if index is None:
                    index = _basename_index(root)
                finding = self._with_fallbacks(finding, index)
            if finding is not None:
                findings.append(finding)
        return findings

    def _with_fallbacks(self, finding: Finding, index: dict[str, str]) -> Finding:
        """Downgrade a missing-path ERROR to a WARNING for placeholder names or a path found elsewhere."""
        claim = finding.claim
        basename = Path(claim.value.rstrip("/")).name
        if Path(basename).stem.lower() in PLACEHOLDER_STEMS:
            return Finding(claim, Severity.WARNING, "placeholder path in docs", self.name)
        found = index.get(basename)
        if found is not None:
            message = f"path does not exist at the stated location; found at: {found}"
            return Finding(claim, Severity.WARNING, message, self.name)
        return finding

    def _check_one(self, claim: Claim, root: Path) -> Finding | None:
        if claim.value.startswith("~/"):
            return Finding(claim, Severity.INFO, "user-local path, not checked", self.name)
        if Path(claim.value).is_absolute():
            return Finding(claim, Severity.WARNING, "absolute path in docs is machine-specific", self.name)
        doc_dir = claim.location.file.parent
        candidates = [root / claim.value, doc_dir / claim.value]
        inside = [c for c in candidates if is_within(c, root)]
        if not inside:
            return Finding(claim, Severity.WARNING, f"{ESCAPE_MESSAGE}: {claim.value}", self.name)
        if any(c.exists() for c in inside):
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
        bases = [root / file_part.lstrip("/")]
    else:
        bases = [doc_file.parent / file_part, root / file_part]
    candidates = [c for base in bases for c in _with_conventions(base, file_part)]
    return next((c for c in candidates if is_within(c, root) and c.exists()), None)


def _with_conventions(base: Path, file_part: str) -> list[Path]:
    """``base`` plus, for extensionless targets, the mkdocs-style ``.md`` / ``index.md`` / ``README.md`` forms."""
    if Path(file_part.rstrip("/")).suffix:
        return [base]
    return [base, base.with_name(base.name + ".md"), base / "index.md", base / "README.md"]


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


_REQUIREMENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


def _declared_names(root: Path) -> tuple[frozenset[str], frozenset[str]]:
    """Console-script names and dependency names declared in ``pyproject.toml`` (read statically)."""
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8")).get("project", {})
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return frozenset(), frozenset()
    requirements = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        requirements.extend(extra)
    names = {m.group(0).lower().replace("-", "_") for r in requirements if (m := _REQUIREMENT_NAME.match(r))}
    return frozenset(project.get("scripts", {})), frozenset(names)


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
        if not is_within(base / rel, root):
            continue
        if (base / rel).with_suffix(".py").is_file() or (base / rel / "__init__.py").is_file():
            return True
    return False


def _module_known(module: str, root: Path) -> bool:
    """Repo module, or a stdlib / well-known tool module (resolved statically, never imported)."""
    top = module.split(".")[0]
    return (
        _module_in_repo(module, root)
        or top in sys.stdlib_module_names
        or top in KNOWN_COMMON
        or top.lower() in _declared_names(root)[1]
    )


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
            (is_within(root / token, root) and (root / token).exists())
            or shutil.which(token) is not None
            or _is_python(token)
            or token in KNOWN_COMMON
            or token in _declared_names(root)[0]
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


# Variables the OS, shell or stdlib/HTTP libraries read on their own; docs mention them without the code reading them.
AMBIENT_ENV = frozenset({
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "PATH", "HOME", "USER", "SHELL", "TMPDIR",
    "TEMP", "TMP", "LANG", "LC_ALL", "TZ", "TERM", "SSLKEYLOGFILE", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "PYTHONPATH", "VIRTUAL_ENV", "CI", "EDITOR", "PAGER",
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
})  # fmt: skip


class EnvChecker:
    """Bidirectional env var check between documented ``ENV_VAR`` claims and what the code reads.

    The code-read set is built lazily from ``root`` on each call. ``docs`` is the scanned doc corpus;
    without it, only the claims count as documentation and no undocumented warnings are raised.
    """

    name = "env"

    def check(
        self,
        claims: list[Claim],
        root: Path,
        docs: Sequence[Path] = (),
        ambient_extra: Collection[str] = (),
    ) -> list[Finding]:
        reads = scan_code_env_reads(root)
        ambient = AMBIENT_ENV | frozenset(ambient_extra)
        env_claims = [c for c in claims if c.kind is ClaimKind.ENV_VAR]
        findings = [self._unread(c, ambient) for c in env_claims if c.value not in reads]
        if docs:
            findings.extend(self._undocumented(reads - ambient, env_claims, root, docs))
        return findings

    def _unread(self, claim: Claim, ambient: frozenset[str]) -> Finding:
        if claim.value in ambient:
            message = f"ambient environment variable (read by the OS/stdlib, not this codebase): {claim.value}"
            return Finding(claim, Severity.INFO, message, self.name)
        return Finding(claim, Severity.ERROR, f"documented env var is not read by the code: {claim.value}", self.name)

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
