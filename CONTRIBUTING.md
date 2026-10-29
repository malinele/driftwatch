# Contributing

For anything larger than a small fix, open an issue first so the change can be
discussed before you invest time in it.

## Gates

Run these before and after your changes; all must pass:

```bash
.venv/bin/python -m ruff check src tests
.venv/bin/python -m mypy --strict src
.venv/bin/python -m pytest tests/ -q
driftwatch scan .
```

The last command is the self-scan: the repository must stay free of findings.

## Commits

Use conventional commit style, for example `feat: add checker` or
`fix: handle empty config`. Common types are feat, fix, refactor, docs, test,
chore, perf and ci.

## License

Contributions are accepted under the project license, the
[PolyForm Noncommercial License 1.0.0](LICENSE). See
[COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md) for commercial use.
