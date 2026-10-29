# DriftWatch 0.6.0 — 13-repo benchmark evidence (October 2026)

This directory is the verifiable evidence behind the campaign numbers quoted in
the changelog and release notes. <!-- driftwatch: ignore -->

## Method

- 13 popular open-source repositories were shallow-cloned at the exact commits
  pinned in `repos.json` and scanned with DriftWatch 0.6.0 using the default
  configuration (no baseline, no excludes):
  `driftwatch scan <clone>` from outside the clone.
- The raw final outputs are in `outputs/` (one file per repository, verbatim).
- Every ERROR finding was classified by hand against the pinned clone; the
  per-finding decisions are in `verdicts.json` (`true_positive`,
  `true_positive_low`, `false_positive`, with the basis for each class).

## Results (ERROR findings)

| version | total errors across the 13 repos |
|---|---|
| 0.1.0 | ~3,857 |
| 0.4.0 | 493 (hand-verified: 70 true, 14.2% precision) |
| 0.5.0 | 98 |
| **0.6.0** | **67 — 65 true, 1 true-but-low, 1 false positive (~97% precision)** |

Seven repositories finished with zero ERROR findings: express, rich, click,
uvicorn, starlette, black, typer.

## What these numbers do and do not claim

- Precision here means: of the ERRORs the tool reported on this sample, how
  many were real documentation defects. It says nothing about recall — defects
  the tool does not report are not measured by this benchmark.
- Two findings verified as real in earlier rounds are intentionally demoted to
  WARNING severity by 0.6.0 policies (a bare-basename path in starlette's
  contributing guide, and mirror-README links), so they no longer appear among
  the ERRORs counted above.
- The 53 fastify findings share 14 root causes in a single file
  (`docs/Reference/TypeScript.md` footer reference links); they were verified
  per root cause, with GitHub-style slug rules.

## Reproducing

Clone each repository at the commit from `repos.json`, install DriftWatch
0.6.0, and run the scan command above; outputs should match `outputs/` except
for environment-dependent PATH lookups in the command checker (a tool present
on your PATH can turn a command INFO into a pass, but does not affect the
ERROR counts here).
