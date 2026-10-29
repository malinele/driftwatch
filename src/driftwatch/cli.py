"""Command-line interface for DriftWatch."""

import argparse

from driftwatch import __version__

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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "scan":
        print(f"driftwatch {__version__}: nothing to check yet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
