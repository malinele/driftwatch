"""Checker framework and the path-existence checker."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

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


CHECKERS: dict[str, Checker] = {PathChecker.name: PathChecker()}
