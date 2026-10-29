"""Command-line interface for DriftWatch."""

import argparse
import sys
from pathlib import Path

from driftwatch import __version__
from driftwatch.baseline import (
    DEFAULT_BASELINE,
    BaselineError,
    filter_baseline,
    load_baseline,
    write_baseline,
)
from driftwatch.config import ConfigError, load_config, severity_at_least
from driftwatch.models import Finding
from driftwatch.report import render_json, render_sarif, render_text, summary_line
from driftwatch.scanner import discover_markdown, scan_files

FORMATS = ("text", "json", "sarif")


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
    scan.add_argument(
        "--output",
        metavar="PATH",
        default=None,
        help="write the report to this file instead of stdout (summary still goes to stderr)",
    )
    scan.add_argument(
        "--baseline",
        metavar="PATH",
        default=None,
        help=f"suppress findings recorded in this baseline file (default: {DEFAULT_BASELINE} if it exists)",
    )
    scan.add_argument(
        "--write-baseline",
        action="store_true",
        help="write current findings to the baseline file and exit 0",
    )
    return parser


def _render(args: argparse.Namespace, findings: list[Finding], file_count: int, suppressed: int, root: Path) -> str:
    if args.format == "json":
        return render_json(findings, root)
    if args.format == "sarif":
        return render_sarif(findings, root)
    return render_text(findings, file_count, suppressed, root)


def _run_scan(args: argparse.Namespace) -> int:
    root = Path.cwd()
    paths = [Path(p) for p in args.paths]
    missing = [p for p in paths if not p.exists()]
    if missing:
        print(f"driftwatch: path not found: {missing[0]}", file=sys.stderr)
        return 2
    try:
        config = load_config(root)
    except ConfigError as exc:
        print(f"driftwatch: {exc}", file=sys.stderr)
        return 2
    files = discover_markdown(paths)
    findings = scan_files(root, files, config)

    default_path = root / DEFAULT_BASELINE
    baseline_path = Path(args.baseline) if args.baseline else default_path
    if args.write_baseline:
        write_baseline(findings, baseline_path, root)
        print(f"wrote baseline with {len(findings)} finding(s) to {baseline_path}")
        return 0

    suppressed = 0
    if args.baseline or baseline_path.is_file():
        try:
            result = filter_baseline(findings, load_baseline(baseline_path), root)
        except BaselineError as exc:
            print(f"driftwatch: {exc}", file=sys.stderr)
            return 2
        findings, suppressed = result.remaining, result.suppressed

    report = _render(args, findings, len(files), suppressed, root)
    if args.output:
        try:
            Path(args.output).write_text(report + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"driftwatch: cannot write {args.output}: {exc}", file=sys.stderr)
            return 2
        print(summary_line(findings, len(files), suppressed), file=sys.stderr)
    else:
        print(report)
    return 1 if any(severity_at_least(f.severity, config.fail_on) for f in findings) else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "scan":
        return _run_scan(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
