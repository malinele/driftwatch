"""Command-line interface for DriftWatch."""

import argparse
import json
import sys
from pathlib import Path

from driftwatch import __version__
from driftwatch.models import Finding, Severity
from driftwatch.scanner import discover_markdown, scan_files

FORMATS = ("text", "json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="driftwatch",
        description="Docs that lie are bugs. DriftWatch checks markdown claims against repository reality.",
    )
    parser.add_argument("--version", action="version", version=f"driftwatch {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="scan markdown files for claims that disagree with the repository")
    scan.add_argument("paths", metavar="PATHS", nargs="*", default=["."], help="files or directories to scan")
    scan.add_argument("--ci", action="store_true", help="CI mode")
    scan.add_argument("--format", choices=FORMATS, default="text", help="output format (default: text)")
    return parser


def _display(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _sort_key(finding: Finding) -> tuple[str, int]:
    return (str(finding.claim.location.file), finding.claim.location.line)


def _as_dict(finding: Finding, root: Path) -> dict[str, object]:
    return {
        "file": _display(finding.claim.location.file, root),
        "line": finding.claim.location.line,
        "severity": finding.severity.value,
        "checker": finding.checker,
        "message": finding.message,
        "value": finding.claim.value,
    }


def _print_text(findings: list[Finding], file_count: int, root: Path) -> None:
    if file_count == 0:
        print("no markdown files found: nothing to check yet")
    for finding in findings:
        where = f"{_display(finding.claim.location.file, root)}:{finding.claim.location.line}"
        print(f"{where}: {finding.severity.name} [{finding.checker}] {finding.message}")
    errors = sum(f.severity is Severity.ERROR for f in findings)
    warnings = sum(f.severity is Severity.WARNING for f in findings)
    print(f"{errors} error(s), {warnings} warning(s) in {file_count} file(s) scanned")


def _run_scan(args: argparse.Namespace) -> int:
    root = Path.cwd()
    paths = [Path(p) for p in args.paths]
    missing = [p for p in paths if not p.exists()]
    if missing:
        print(f"driftwatch: path not found: {missing[0]}", file=sys.stderr)
        return 2
    files = discover_markdown(paths)
    findings = sorted(scan_files(root, files), key=_sort_key)
    if args.format == "json":
        print(json.dumps([_as_dict(f, root) for f in findings], indent=2))
    else:
        _print_text(findings, len(files), root)
    return 1 if any(f.severity is Severity.ERROR for f in findings) else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "scan":
        return _run_scan(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
