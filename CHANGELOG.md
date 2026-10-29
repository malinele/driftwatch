# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.6.0] - 2026-10-30

A noise-reduction release targeting the last diagnosed false-positive classes. Across 13 popular repositories the error count went from 3,857 (0.1.0) to 493 (0.4.0) to 98 (0.5.0), with hand-verified precision rising from under 2 percent toward a large majority of true findings.

### Added

- Historical docs are matched by basename anywhere in the tree, covering nested `docs/<locale>/docs` trees.
- User-defined environment variables from `.env`-style and `export` definitions suppress findings document-wide.
- Environment variables with external-tool prefixes are downgraded to INFO; the prefixes are configurable.
- Link targets that exist elsewhere in the tree are reported as found-at warnings.

### Changed

- Python modules built in a tutorial are downgraded, with packaging context in the message.
- `search_index.json` is treated as a generated file.

### Fixed

- Duplicate findings are no longer reported.

### Verified results

- Final campaign result for 0.6.0: across the 13 pinned repositories the final count is 67 errors, of which 65 verified true, 1 true-but-low and 1 false positive (~97% precision of reported errors; recall not measured). Evidence (pinned commits, raw outputs, per-finding verdicts) is in `benchmark/2026-10-campaign/`.

## [0.5.0] - 2026-10-30

A noise-reduction release targeting the remaining path, command and environment-variable false-positive classes.

### Added

- Path policy v3: tree-diagram fences are skipped, creation phrasing is recognized more broadly, and workflow basenames are resolved.
- Command policy v3: doc-defined CLI names are recognized, and output guards suppress commands shown as output.
- Environment policy v3: click `auto_envvar_prefix` is supported, and `docs_src`/examples directories are scanned as code.
- Anchor matching folds diacritics.

### Changed

- Identical findings are now deduplicated.
- Directory claims with a trailing slash regain their recall.
- Path severity ladder: ERROR only when the parent directory exists; ecosystem filenames are reported as INFO.
- Link text of external URLs is no longer claimed as a path.
- Command severity is consistent: ERROR only for repo-defined or repo-path commands.
- Environment variables defined in a doc-local `.env` or inline are treated as user-set, and variables set in CI workflows are suppressed.
- Locale fallbacks are applied uniformly.
- Link targets that are template placeholders are skipped.
- Findings in mirror READMEs are downgraded to WARNING, with a note about site versus GitHub rendering.

Validation: hand-verification of the previous release across 13 repositories measured 70 true findings among 493 errors (14.2% precision), concentrated in anchors and links; this release targets the remaining path, command and environment noise classes.

## [0.4.0] - 2026-10-30

A campaign-driven precision release.

### Added

- Command checker policy: commands defined by the repository itself (`console_scripts` entry points, `package.json` `bin`) and a built-in list of known tools are accepted; install-step context is recognized; unknown bare commands are demoted to INFO.
- Path token filters: MIME types, CIDR/IP addresses, npm scopes, `owner/repo` references, bare file extensions and regex-like tokens are no longer treated as paths.
- Path context policies: files the reader is asked to create, fence title attributes, gitignored and build artifacts are not reported as missing; `tests` directories are skipped by default.
- Anchor overhaul: `attr_list` `{ #id }` and `[](){#id}` ids, HTML `id`/`name` anchors, and slug fidelity for emphasis, entities and apostrophes; anchors on plugin-generated pages are downgraded to INFO.
- Site-absolute link roots and MyST `(label)=` targets.
- Locale-aware dedup of findings repeated across translation directories, with translation-fallback downgrades.

### Changed

- Environment-variable claims now require an explicit shell context, and error-code prefixes are skipped.
- The ambient variable list is extended with interpreter and runtime variables.

Validated across 12 popular open-source repositories: the error count dropped from about 3,800 to under 500, while all previously verified real findings remained.

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
