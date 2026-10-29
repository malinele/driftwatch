"""Baseline files: accept today's findings and only fail on new ones.

Entries are keyed on (file, line, checker, value). The message text is deliberately excluded so that
rewording a message does not invalidate a baseline. The line number *is* part of the key, so editing a
document above a baselined finding shifts it and makes it reappear. That line-sensitivity is an
intentional v1 trade-off: it keeps keys simple and never hides a finding that moved somewhere new.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from driftwatch.models import Finding

DEFAULT_BASELINE = ".driftwatch-baseline.json"
BASELINE_VERSION = 1

BaselineKey = tuple[str, int, str, str]


class BaselineError(Exception):
    """Raised when a baseline file cannot be read or has the wrong shape."""


@dataclass(frozen=True)
class BaselineResult:
    remaining: list[Finding]
    suppressed: int


def display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def finding_key(finding: Finding, root: Path) -> BaselineKey:
    claim = finding.claim
    return (display_path(claim.location.file, root), claim.location.line, finding.checker, claim.value)


def write_baseline(findings: list[Finding], path: Path, root: Path) -> None:
    keys = sorted({finding_key(f, root) for f in findings})
    entries = [{"file": k[0], "line": k[1], "checker": k[2], "value": k[3]} for k in keys]
    payload = {"version": BASELINE_VERSION, "findings": entries}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_baseline(path: Path) -> set[BaselineKey]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data["findings"]
        return {(str(e["file"]), int(e["line"]), str(e["checker"]), str(e["value"])) for e in entries}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise BaselineError(f"cannot read baseline {path}: {exc}") from exc


def filter_baseline(findings: list[Finding], baseline: set[BaselineKey], root: Path) -> BaselineResult:
    remaining = [f for f in findings if finding_key(f, root) not in baseline]
    return BaselineResult(remaining, len(findings) - len(remaining))
