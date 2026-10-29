<!-- DriftWatch itself reads no environment variables. The self-scan also sees sample sources under tests/ that do: APP_MODE, DATABASE_URL, DEBUGGING, DEMO_FLAG, HIDDEN_TWO, PORT_NUMBER, SECRET_TOKEN, UNDOC_VAR. They are named here only so the undocumented-env warning stays quiet. -->
# DriftWatch

Docs that lie are bugs. DriftWatch checks markdown claims against repository reality.

## Why

Documentation that lies is a bug. A wrong install command or a documented-but-dead environment variable costs every newcomer an afternoon. In AI-assisted development it is worse: agents read the docs and turn documentation lies into code bugs.

## What it checks

| Checker | Claim | Example finding |
|---------|-------|-----------------|
| paths | A file or directory named in the docs exists. | A documented script that was deleted. |
| links + anchors | Internal links resolve to a file and a heading. | A link to a heading that was renamed. |
| commands | Shell commands in fences refer to real entry points. | A documented command that no longer exists. |
| env | Documented and code-read environment variables agree, in both directions. | A documented variable that no code reads. |

Today a normal scan feeds only path and env claims to the checkers; see the [checker reference](docs/checkers.md) for the current status of the others.

## Quickstart

Install from source for now:

```bash
pip install -e .
driftwatch scan .
```

Sample output:

```text
README.md:1: WARNING [env] env var read by code but undocumented: DEMO_FLAG
README.md:3: ERROR [path] path does not exist: scripts/old_run.sh
docs/setup.md:5: ERROR [path] path does not exist: config/settings.yaml
docs/setup.md:9: ERROR [env] documented env var is not read by the code: APP_SECRET_KEY
3 error(s), 1 warning(s) in 2 file(s) scanned
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | No remaining finding at or above the failure severity (default: error). |
| 1 | At least one remaining finding is at or above it. |
| 2 | Usage or setup problem: bad path, invalid configuration, unreadable baseline. |

## Adopting on an existing repo

1. Run `driftwatch scan .` and fix or silence what is wrong.
2. Run `driftwatch scan . --write-baseline` to record the remaining backlog.
3. Commit the baseline, then tighten: fix baselined findings over time and regenerate it.

Details are in the [user guide](docs/user-guide.md).

## CI

Run `driftwatch scan .` in your pipeline; a non-zero exit fails the build.
Use `--format sarif` with `--output` to publish findings to code scanning. See [CI integration](docs/ci.md).

## Documentation

- [User guide](docs/user-guide.md)
- [Checker reference](docs/checkers.md)
- [CI integration](docs/ci.md)
- [Changelog](CHANGELOG.md)
