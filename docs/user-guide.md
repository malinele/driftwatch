# DriftWatch user guide

Docs that lie are bugs. DriftWatch statically checks the claims that markdown files make about a repository (file paths, links, shell commands, environment variables) against what is actually in the repository. It never imports or executes your code and never runs the documented commands.

Related pages: [checker reference](checkers.md), [CI integration](ci.md).

## Install

DriftWatch is not published yet. Install it from a source checkout (Python 3.12 or newer):

```bash
pip install -e .
```

This installs the `driftwatch` command and its one runtime dependency, `markdown-it-py`. For development tools (pytest, mypy, ruff) use `pip install -e '.[dev]'`.

Without installing, `python -m driftwatch.cli scan .` works from a checkout with `src/` on the Python path.

## Quickstart

Run the scan from the repository root. The current working directory is treated as the repository root: configuration, the baseline file and relative paths are all resolved against it.

```bash
driftwatch scan .
```

`scan` takes any number of files or directories (default: `.`). It collects every markdown file below them, skipping hidden directories (names starting with a dot) and `venv`, `node_modules`, `dist` and `__pycache__`.

### Reading the output

Each finding is one line:

```text
docs/setup.md:12: ERROR [path] path does not exist: src/app/main.py
README.md:1: WARNING [env] env var read by code but undocumented: CACHE_DIR
1 error(s), 1 warning(s) in 4 file(s) scanned
```

The format is `file:line: SEVERITY [checker] message`. The last line is a summary; it also reports how many findings the baseline suppressed. Use `--format json` or `--format sarif` for machine-readable output, and `--output PATH` to write the report to a file (the summary line then goes to stderr). See [CI integration](ci.md) for SARIF.

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | No remaining finding at or above the `fail_on` severity (default: error). Also returned by `--write-baseline`. |
| 1 | At least one remaining finding is at or above `fail_on`. |
| 2 | Usage or setup problem: a given path does not exist, the configuration is invalid, the baseline cannot be read, the `--output` file cannot be written, or the command line is malformed. |

Warnings alone do not fail the scan unless `fail_on` is set to `warning`.

The `--ci` flag is accepted but currently changes nothing.

## Adopting on an existing repository

A first scan of a mature repository usually reports many findings. To get a green build without fixing everything at once:

1. Run `driftwatch scan .` and read the findings.
2. Triage. Fix what is really wrong. Silence what is a false positive (see [suppressing false positives](#suppressing-false-positives)). Whatever is left is your backlog.
3. Record the backlog with `driftwatch scan . --write-baseline`. This writes every current finding to the baseline file and exits 0.
4. Commit the baseline. From now on `driftwatch scan .` picks it up automatically and only fails on findings that are not in it. The summary line shows how many were suppressed.
5. Tighten over time: fix findings from the backlog, then run `--write-baseline` again to shrink the file. Entries for findings that no longer exist are not reported as stale, so regenerating is how they disappear.

<!-- driftwatch: ignore=path -->
The baseline file is called `.driftwatch-baseline.json` and lives in the repository root; the example below shows its shape. Use `--baseline PATH` to read or write a different file. A path given with `--baseline` that does not exist is an error (exit 2).

```json
{
  "version": 1,
  "findings": [
    {"file": "docs/setup.md", "line": 12, "checker": "path", "value": "src/app/main.py"}
  ]
}
```

An entry is keyed on file, line, checker and the claimed value; the message text is not part of the key. Because the line number is part of the key, editing a document above a baselined finding moves it and it reappears as new. Regenerate the baseline after bulk edits. `--write-baseline` does not read the existing baseline, so it always records everything the scan currently finds (warnings included, after ignores and excludes).

## Suppressing false positives

### Inline ignore comments

An HTML comment suppresses findings on its own line and on the line directly after it:

```markdown
<!-- driftwatch: ignore -->
See `scripts/generated-at-build.sh` for details.
```

With no argument every checker is silenced. To silence only some checkers, list them after `ignore=`, comma separated:

```markdown
<!-- driftwatch: ignore=path,env -->
Set `LEGACY_MODE` and edit `config/local.yaml`.
```

Checker names are `path`, `link`, `anchor`, `command` and `env`. A name that is not a checker silences nothing. If several comments cover the same line, their scopes combine, and a bare ignore wins over any list.

The comment is found with a plain text pattern, so it also takes effect inside code fences and inline code. It can sit on the same line as the claim, for example at the end of a bullet.

### Exclude globs

To skip whole files, add patterns to `exclude` in the configuration. Each pattern is matched with `fnmatch` against the file's path relative to the repository root (POSIX separators) and also against the bare file name. In `fnmatch`, `*` also matches `/`, so `docs/archive/*` covers everything below that directory.

```toml
[tool.driftwatch]
exclude = ["CHANGELOG.md", "docs/archive/*"]
```

Excluded files are neither checked nor counted as documentation by the env checker. Exclusion applies to markdown files only: the env checker still reads source files from the whole repository.

## Translated documentation

A directory named by an ISO 639-1 code (optionally with a region, such as `pt-BR` or `zh_CN`) directly under a `docs` directory, or under an `i18n` or `locales` directory, is a locale copy. The source locale is `en` when `docs/en` exists, otherwise the bare `docs/` tree.

- **Dedup:** findings with the same path (locale segment removed), line, checker and value in the source locale and in translations are reported once, for the source locale. The translation copies collapse into one INFO: `same finding repeated in N locale copies (de, es, ...)`.
- **Fallback:** a path or link error that appears only in a translation, whose target exists in the source locale, becomes a WARNING: `missing in this locale, exists in source locale`.

Set `locales = false` to turn both off and get the raw findings.

## Configuration

<!-- driftwatch: ignore=path -->
Settings come from the `[tool.driftwatch]` table in `pyproject.toml`, from a `driftwatch.toml` file in the repository root, or both. In `driftwatch.toml` the keys sit at the top level, with no table header. When both exist they are merged key by key and `driftwatch.toml` wins. Unknown keys, wrong types and unknown checker names are errors (exit 2).

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `enabled_checkers` | list of strings | all five | Which checkers run: any of `path`, `link`, `anchor`, `command`, `env`. |
| `exclude` | list of strings | empty | Glob patterns of markdown files to skip. |
| `fail_on` | string | `"error"` | Lowest severity that makes the exit code 1: `"error"`, `"warning"` or `"info"` (case-insensitive). |
| `env_stoplist_extra` | list of strings | empty | Environment variable names to ignore entirely, both when found in docs and when read by code. |
| `ambient_extra` | list of strings | empty | Extra environment variable names treated as ambient (merged with the built-in ambient set). A documented ambient variable that the code does not read is INFO, not ERROR, and is never reported as undocumented. |
| `historical` | list of strings | empty | Extra glob patterns for changelog-type files, which only get the link and anchor checkers. Built in: `CHANGELOG*.md`, `HISTORY*.md`, `CHANGES*.md`, `RELEASES*.md`, `docs/release-notes*.md`. |
| `historical_full_scan` | boolean | `false` | When `true`, historical files are checked by every checker like any other document. |
| `mkdocs` | boolean or `"auto"` | `"auto"` | mkdocs directory-URL link resolution for documents under the docs directory. `"auto"` enables it when a mkdocs config file exists at the scan root. See the link checker in [checkers](checkers.md). |
| `locales` | boolean | `true` | Locale-aware reporting for translated docs. See [Translated documentation](#translated-documentation). Set `false` for raw per-file findings. |

Example:

```toml
[tool.driftwatch]
enabled_checkers = ["path", "link", "anchor", "env"]
exclude = ["CHANGELOG.md"]
fail_on = "warning"
env_stoplist_extra = ["CI_JOB_TOKEN"]
```

An empty `enabled_checkers` list disables every checker. Disabling `env` disables both of its directions.

For what each checker does and where it is blind, see the [checker reference](checkers.md).
