# Running DriftWatch in CI

DriftWatch is a plain command-line tool: it reads the working tree, prints a report and sets an exit code. This page shows a GitHub Actions job that uploads the report as SARIF so findings show up as code scanning alerts, how to use the exit code for plain pass/fail gating, and a pre-commit hook. For the commands and options themselves see the [user guide](user-guide.md).

## GitHub Actions with SARIF upload

```yaml
name: DriftWatch

on:
  push:
    branches: [main]
  pull_request:

jobs:
  driftwatch:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      security-events: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install DriftWatch
        run: python -m pip install -e .
      - name: Scan documentation
        run: driftwatch scan . --format sarif --output driftwatch.sarif
      - name: Upload SARIF
        if: always() && hashFiles('driftwatch.sarif') != ''
        uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: driftwatch.sarif
```

Notes:

- The install step assumes the job runs in a checkout of DriftWatch itself. DriftWatch is not published yet, so in another repository you need to get its source into the job first (for example a second checkout of the DriftWatch repository) and install from there with the same command.
- Run the scan from the repository root. Configuration, the baseline file and the paths in the report are all relative to the working directory.
- With `--output`, the report goes to the file and a one-line summary goes to stderr, so the job log still shows the totals.
- The scan step exits 1 when an error-level finding remains, which fails the job. The `if: always()` condition makes the upload step run anyway, so the alerts are published even for a failing scan. The `hashFiles` condition skips the upload when no report was written, which happens when the scan stops early with exit code 2 (bad configuration, unreadable baseline).
- Each checker is a SARIF rule (`path`, `link`, `anchor`, `command`, `env`). Severities map to SARIF levels: error to error, warning to warning.
- The `upload-sarif` action needs code scanning to be available for the repository, and the `security-events: write` permission. Workflow runs triggered from forks get a read-only token, so the upload step fails there.
- A committed baseline is picked up automatically from the repository root, so CI only reports findings that are not in it.

The repository's own workflow, `.github/workflows/ci.yml`, runs lint, type-check and tests; it does not run a DriftWatch scan.

## Plain gating with exit codes

If you do not need alerts, run the scan and let the exit code decide:

```bash
driftwatch scan .
```

| Exit code | Meaning |
|-----------|---------|
| 0 | Nothing at or above the `fail_on` severity (default: error). |
| 1 | At least one finding at or above `fail_on`. |
| 2 | Setup problem: missing path, invalid configuration, unreadable baseline, or the `--output` file could not be written. |

Warnings do not fail the job by default. To fail on them as well, set `fail_on = "warning"` in the `[tool.driftwatch]` table of `pyproject.toml`. To see the report but never fail on findings, append `|| true` in the workflow step; this also hides exit code 2.

## Pre-commit

With the pre-commit framework, a local hook runs the same scan before each commit. Add this to your pre-commit configuration file (the YAML file at the repository root):

```yaml
repos:
  - repo: local
    hooks:
      - {id: driftwatch, name: driftwatch, entry: driftwatch scan ., language: system, pass_filenames: false}
```

The hook uses the `driftwatch` executable already on your `PATH`, so install DriftWatch into the same environment first. It scans the whole repository on every run, because the env checker compares documents with source files and a change to either side can create a finding.
