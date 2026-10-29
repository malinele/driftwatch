"""Report rendering: text, JSON and SARIF 2.1.0.

JSON shape (``--format json``)::

    {
      "version": "<driftwatch version>",
      "findings": [
        {"file": "a.md", "line": 1, "severity": "error", "checker": "path",
         "message": "...", "value": "..."}
      ],
      "summary": {"errors": 0, "warnings": 0, "info": 0}
    }

``findings`` are sorted by (file, line, checker, message); file paths are relative to the
scan root when possible and use POSIX separators.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from driftwatch import __version__
from driftwatch.check import CHECKERS
from driftwatch.models import Finding, Severity

SARIF_VERSION = "2.1.0"
SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
INFORMATION_URI = "https://github.com/nenadmitrovic/driftwatch"

_SARIF_LEVELS = {
    Severity.ERROR: "error",
    Severity.WARNING: "warning",
    Severity.INFO: "note",
}

_RULE_DESCRIPTIONS = {
    "path": "Documented file or directory path does not exist",
    "link": "Internal markdown link points to a missing file",
    "anchor": "Internal link anchor does not match any heading",
    "command": "Documented shell command references something that does not exist",
    "env": "Documented environment variable disagrees with the code that reads it",
}


def display_path(path: Path, root: Path) -> str:
    """Path relative to ``root`` (POSIX separators) when possible, else as given."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _sort_key(finding: Finding, root: Path) -> tuple[str, int, str, str]:
    location = finding.claim.location
    return (display_path(location.file, root), location.line, finding.checker, finding.message)


def _ordered(findings: Iterable[Finding], root: Path) -> list[Finding]:
    return sorted(findings, key=lambda f: _sort_key(f, root))


def _count(findings: Iterable[Finding], severity: Severity) -> int:
    return sum(f.severity is severity for f in findings)


def summary_line(findings: list[Finding], scanned_files: int, suppressed: int = 0) -> str:
    errors = _count(findings, Severity.ERROR)
    warnings = _count(findings, Severity.WARNING)
    note = f", {suppressed} suppressed by baseline" if suppressed else ""
    return f"{errors} error(s), {warnings} warning(s){note} in {scanned_files} file(s) scanned"


def render_text(findings: list[Finding], scanned_files: int, suppressed: int, root: Path) -> str:
    base = root
    lines: list[str] = []
    if scanned_files == 0:
        lines.append("no markdown files found: nothing to check yet")
    for finding in _ordered(findings, base):
        where = f"{display_path(finding.claim.location.file, base)}:{finding.claim.location.line}"
        lines.append(f"{where}: {finding.severity.name} [{finding.checker}] {finding.message}")
    lines.append(summary_line(findings, scanned_files, suppressed))
    return "\n".join(lines)


def render_json(findings: list[Finding], root: Path) -> str:
    base = root
    ordered = _ordered(findings, base)
    payload = {
        "version": __version__,
        "findings": [
            {
                "file": display_path(f.claim.location.file, base),
                "line": f.claim.location.line,
                "severity": f.severity.value,
                "checker": f.checker,
                "message": f.message,
                "value": f.claim.value,
            }
            for f in ordered
        ],
        "summary": {
            "errors": _count(ordered, Severity.ERROR),
            "warnings": _count(ordered, Severity.WARNING),
            "info": _count(ordered, Severity.INFO),
        },
    }
    return json.dumps(payload, indent=2)


def _rules(findings: list[Finding]) -> list[dict[str, object]]:
    ids = sorted(set(CHECKERS) | {f.checker for f in findings})
    return [
        {"id": rule_id, "shortDescription": {"text": _RULE_DESCRIPTIONS.get(rule_id, f"{rule_id} checker")}}
        for rule_id in ids
    ]


def render_sarif(findings: list[Finding], root: Path) -> str:
    results = [
        {
            "ruleId": f.checker,
            "level": _SARIF_LEVELS[f.severity],
            "message": {"text": f.message},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": display_path(f.claim.location.file, root)},
                        "region": {"startLine": f.claim.location.line},
                    }
                }
            ],
        }
        for f in _ordered(findings, root)
    ]
    log = {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "DriftWatch",
                        "informationUri": INFORMATION_URI,
                        "version": __version__,
                        "rules": _rules(findings),
                    }
                },
                "results": results,
            }
        ],
    }
    return json.dumps(log, indent=2)
