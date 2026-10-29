"""Discover markdown files, extract claims and run the registered checkers."""

from __future__ import annotations

import fnmatch
import os
import re
from collections.abc import Iterator
from pathlib import Path

from driftwatch.check import CHECKERS, AnchorChecker, EnvChecker, LinkChecker
from driftwatch.config import Config
from driftwatch.extract import (
    extract_command_claims,
    extract_env_claims,
    extract_link_claims,
    extract_myst_labels,
    extract_path_claims,
)
from driftwatch.markdown import parse_markdown
from driftwatch.models import Claim, Finding

SKIP_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "dist", "__pycache__"})


def _is_skipped_dir(name: str) -> bool:
    return name in SKIP_DIRS or name.startswith(".")


def discover_markdown(paths: list[Path]) -> list[Path]:
    """Return a sorted, de-duplicated list of ``*.md`` files under ``paths``."""
    found: set[Path] = set()
    for path in paths:
        if path.is_file():
            if path.suffix == ".md":
                found.add(path)
            continue
        found.update(_walk(path))
    return sorted(found)


def _walk(directory: Path) -> Iterator[Path]:
    for current, dirnames, filenames in os.walk(directory):
        dirnames[:] = [d for d in dirnames if not _is_skipped_dir(d)]
        for filename in filenames:
            if filename.endswith(".md"):
                yield Path(current) / filename


# Matches `<!-- driftwatch: ignore -->` and `<!-- driftwatch: ignore=a,b -->`.
_IGNORE_COMMENT = re.compile(r"<!--\s*driftwatch:\s*ignore(?:\s*=\s*([\w,\s-]+?))?\s*-->")

# line number -> checker names to suppress (``None`` means every checker)
IgnoreMap = dict[int, frozenset[str] | None]


def _merge_scope(current: frozenset[str] | None, new: frozenset[str] | None) -> frozenset[str] | None:
    if current is None or new is None:
        return None
    return current | new


def parse_ignores(text: str) -> IgnoreMap:
    """Map each line covered by an ignore comment (its own and the next) to the checkers it silences."""
    ignores: IgnoreMap = {}
    for number, line in enumerate(text.splitlines(), start=1):
        for match in _IGNORE_COMMENT.finditer(line):
            names = match.group(1)
            scope = frozenset(n.strip() for n in names.split(",") if n.strip()) if names else None
            for covered in (number, number + 1):
                ignores[covered] = _merge_scope(ignores[covered], scope) if covered in ignores else scope
    return ignores


def _is_ignored(finding: Finding, ignores: dict[Path, IgnoreMap]) -> bool:
    location = finding.claim.location
    file_ignores = ignores.get(location.file)
    if file_ignores is None or location.line not in file_ignores:
        return False
    scope = file_ignores[location.line]
    return scope is None or finding.checker in scope


def is_excluded(file: Path, root: Path, patterns: tuple[str, ...]) -> bool:
    try:
        relative = file.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        relative = file.as_posix()
    return any(fnmatch.fnmatch(relative, p) or fnmatch.fnmatch(file.name, p) for p in patterns)


# Changelog-type files describe the past, so only their links and anchors are checked.
HISTORICAL_PATTERNS = ("CHANGELOG*.md", "HISTORY*.md", "CHANGES*.md", "RELEASES*.md", "docs/release-notes*.md")
HISTORICAL_CHECKERS = frozenset({"link", "anchor"})


def _relative_posix(file: Path, root: Path) -> str:
    try:
        return file.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return file.as_posix()


def is_historical(file: Path, root: Path, extra: tuple[str, ...] = ()) -> bool:
    """True for changelog-type files (built-in patterns plus ``extra``), matched case-insensitively."""
    relative = _relative_posix(file, root).lower()
    name = file.name.lower()
    for pattern in (*HISTORICAL_PATTERNS, *extra):
        lowered = pattern.lower()
        if "/" in lowered:
            if fnmatch.fnmatchcase(relative, lowered):
                return True
        elif fnmatch.fnmatchcase(name, lowered) or fnmatch.fnmatchcase(relative, lowered):
            return True
    return False


TEST_DIRS = frozenset({"tests", "test", "fixtures", "testdata"})


def is_test_content(file: Path, root: Path) -> bool:
    """True if ``file`` sits under a tests/test/fixtures/testdata directory inside ``root``."""
    try:
        relative = file.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return any(part in TEST_DIRS for part in relative.parts[:-1])


def scan_files(root: Path, files: list[Path], config: Config | None = None) -> list[Finding]:
    cfg = config if config is not None else Config()
    files = [f for f in files if not is_excluded(f, root, cfg.exclude)]
    if not cfg.scan_tests:
        files = [f for f in files if not is_test_content(f, root)]
    findings: list[Finding] = []
    env_claims: list[Claim] = []
    ignores: dict[Path, IgnoreMap] = {}
    labels = frozenset[str]().union(
        *(extract_myst_labels(f.read_text(encoding="utf-8", errors="replace")) for f in files)
    )
    for file in files:
        text = file.read_text(encoding="utf-8", errors="replace")
        ignores[file] = parse_ignores(text)
        doc = parse_markdown(file, text)
        historical = not cfg.historical_full_scan and is_historical(file, root, cfg.historical)
        if historical:
            claims = extract_link_claims(doc)
        else:
            claims = [*extract_path_claims(doc), *extract_link_claims(doc), *extract_command_claims(doc)]
            env_claims.extend(extract_env_claims(doc))
        for name, checker in CHECKERS.items():
            if historical and name not in HISTORICAL_CHECKERS:
                continue
            if name not in cfg.enabled_checkers or isinstance(checker, EnvChecker):
                continue
            if isinstance(checker, LinkChecker):
                findings.extend(checker.check(claims, root, cfg.mkdocs, labels))
            elif isinstance(checker, AnchorChecker):
                findings.extend(checker.check(claims, root, cfg.mkdocs))
            else:
                findings.extend(checker.check(claims, root))
    # The env checker is bidirectional, so it needs every claim and the whole doc corpus at once.
    if files:
        extra = set(cfg.env_stoplist_extra)
        for name, checker in CHECKERS.items():
            if name in cfg.enabled_checkers and isinstance(checker, EnvChecker):
                checked = checker.check(
                    [c for c in env_claims if c.value not in extra], root, files, cfg.ambient_extra
                )
                findings.extend(f for f in checked if f.claim.value not in extra)
    return [f for f in findings if not _is_ignored(f, ignores)]


def scan(root: Path, paths: list[Path], config: Config | None = None) -> list[Finding]:
    return scan_files(root, discover_markdown(paths), config)
