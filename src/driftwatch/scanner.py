"""Discover markdown files, extract claims and run the registered checkers."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from driftwatch.check import CHECKERS
from driftwatch.extract import extract_path_claims
from driftwatch.markdown import parse_markdown
from driftwatch.models import Finding

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


def scan_files(root: Path, files: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for file in files:
        text = file.read_text(encoding="utf-8", errors="replace")
        claims = extract_path_claims(parse_markdown(file, text))
        for checker in CHECKERS.values():
            findings.extend(checker.check(claims, root))
    return findings


def scan(root: Path, paths: list[Path]) -> list[Finding]:
    return scan_files(root, discover_markdown(paths))
