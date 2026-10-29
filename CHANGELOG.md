# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.3.0] - 2026-10-30

### Added

- mkdocs mode: directory-URL link and anchor resolution, enabled automatically when `mkdocs.yml` is present and via a mkdocs config key.

### Changed

- Environment-variable extraction ignores program-output fences: bare fences claim only prompt-prefixed lines, and error/traceback lines in shell fences are skipped.

Validated on a real-world repository: scan precision reached 7/7 true findings (from 70 findings with 91 percent noise in 0.1.0).

## [0.2.0] - 2026-10-30

### Added

- Ambient environment variable policy: documented variables the OS or stdlib reads on its own are reported as INFO instead of ERROR; extend the list with `ambient_extra`.
- Historical-docs policy: CHANGELOG and HISTORY files run link and anchor checks only (configurable).
- Path heuristics: string literals are excluded, `~/` paths are INFO, placeholder names are WARNING, and a missing path suggests where the same basename exists elsewhere.
- mkdocs-style extensionless link resolution (`page` resolves to `page.md`, `page/index.md` or `page/README.md`).

### Changed

- The scan root is derived from the target instead of the current directory, and claim resolution is contained within it.
- The link, anchor and command checkers are wired into real scans.
- The env scan covers flat package layouts.
- Bare code fences claim only prompt-prefixed lines.

### Fixed

- Same-document anchors stopped being checked after extensionless resolution was introduced; they are checked again.

All of these were driven by a real-world scan of a major open-source project: findings went from 70 errors to 9, six of them confirmed real documentation rot.

## [0.1.0] - 2026-10-29

### Added

- Initial release.
- Four static checkers: paths, internal links and anchors, shell commands, and bidirectional environment variables.
- Inline ignores.
- TOML configuration.
- Baseline workflow for adopting DriftWatch on existing repositories.
- Text, JSON and SARIF output.
- Strict typing and 120+ tests.
