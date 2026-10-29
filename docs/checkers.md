# Checker reference

DriftWatch has five checkers, covering four kinds of claim: paths, links and anchors, commands, and environment variables. Each claim comes from the markdown structure (parsed with markdown-it-py) and is checked against the file system or the source code. Nothing is executed or imported. The names in the table are what `enabled_checkers`, `ignore=` comments and the `[checker]` tag in the output use. See the [user guide](user-guide.md) for configuration and suppression.

| Checker | Severity produced | Checks |
|---------|-------------------|--------|
| `path` | error, warning | Inline code spans that look like file paths exist. |
| `link` | error | Relative link and image targets exist. |
| `anchor` | error | Fragments (`#heading`) match a heading in the target markdown file. |
| `command` | error | Repo-local command paths in shell code fences exist; unknown bare tools are only INFO. |
| `env` | error, warning | Environment variables in docs and in code agree. |

## Wiring

A normal `scan` extracts path, link, anchor, command and environment variable claims from every document (`src/driftwatch/scanner.py`) and runs all enabled checkers on them. The end-to-end test in `tests/test_e2e.py` asserts that the demo fixture produces link, anchor and command errors. The command checker also treats names declared in the scanned project's `pyproject.toml` (`[project.scripts]` entries and dependency names, for `python -m`) as known.

## Path checker

**Claims:** every inline code span in prose that looks like a repository path. Code fences are not read for paths.

A span counts as a path when, after trimming whitespace and trailing punctuation (`. , ; : ! ? ) ] } ' "`), it:

- is non-empty and shorter than 200 characters,
- contains no whitespace and does not start with a hyphen,
- contains no asterisk or opening brace and does not start with a URL scheme (`https://`, `git@host:` and similar),
- and either contains a slash or ends in one of: `.py .md .toml .yaml .yml .json .txt .sh .cfg .ini .html .css .js .ts .png .lock` (case-insensitive).

Spans containing a single or double quote character (string literals such as `b"HTTP/1.1"`) are never claimed.

**ERROR:** `path does not exist: ...` when the path exists neither relative to the repository root (the working directory) nor relative to the directory of the document that mentions it, and neither downgrade below applies.

**WARNING:** `absolute path in docs is machine-specific` for any absolute path, whether or not it exists.

**WARNING:** `placeholder path in docs` replaces the ERROR when the missing path's base name without extension is one of `foo bar baz example sample myfile yourfile placeholder test`.

**WARNING:** `path does not exist at the stated location; found at: <path>` replaces the ERROR when a file with the same base name exists elsewhere under the root. The first match in sorted order is reported. The index is built once per scan and skips hidden directories, `venv`, `node_modules`, `dist` and `__pycache__`.

**INFO:** `user-local path, not checked` for paths starting with `~/`. They are claimed but never looked up.

Example:

```markdown
Entry point: `src/app/main.py`       <- ERROR if the file is missing
Config lives in `/etc/app/conf.toml` <- WARNING, absolute
Run it with `make test`              <- not a path (contains whitespace)
```

Limitations:

- A bare file name such as main.py is looked up in the root and in the document's own directory; if missing, the base name index gives a warning (not an error) when the file exists elsewhere.
- Anything with a slash is treated as a path, so a span like "and/or" or "v1/v2" is reported. Use an ignore comment for these.
- Globs (anything with an asterisk) and brace expansions are skipped, not checked.
- A span without a slash and with an extension outside the list above is not seen.

## Link checker

**Claims:** the target of every `[text](target)` link and `![alt](src)` image.

Skipped: any target with a URL scheme (http, https, mailto and so on) or starting with two slashes, empty targets, and autolinks written in angle brackets. A query string is dropped and percent-escapes are decoded before the lookup. A pure fragment link such as `[x](#section)` has no file part and is handled by the anchor checker; a link like `[x](other.md#section)` yields one link claim and one anchor claim.

Resolution: relative to the document's directory first, then relative to the repository root. A target starting with a slash is relative to the repository root only. A directory counts as existing. A target with no file extension is tried, in order, as written, then with the markdown suffix appended, then as a directory containing an index file, then as a directory containing a readme file (mkdocs conventions; the file names are index.md and README.md). The anchor checker resolves cross-document targets the same way.

**ERROR:** `link target does not exist: ...`.

Mkdocs mode: mkdocs serves a page at a directory URL, so a relative extensionless link is resolved against that URL, not against the file. The mode is on for documents under the docs directory when a mkdocs.yml or mkdocs.yaml file exists at the scan root (the `mkdocs` option defaults to `"auto"`; `true` forces it on, `false` off). The docs directory comes from a `docs_dir:` line in that file if present, otherwise it is docs. For a page named sub/page.md the URL directory is sub/page/, and index.md and README.md map to their parent directory. The link is resolved against the URL directory, then mapped back to a file: the result with the markdown suffix appended, or the result as a directory containing index.md, inside the docs directory. For example, a link to ../advanced/transports from docs/async.md resolves to docs/advanced/transports.md. This is tried first; if it finds nothing, the chain above still applies. Targets with a file extension, and targets starting with a slash, always use the chain above. The anchor checker maps cross-document targets the same way.

Example:

```markdown
See [the guide](docs/guide.md) and ![diagram](img/flow.png).
```

Limitations:

- Reference-style links (`[text][ref]`) are resolved by the parser to their definition and checked at the line where they are used. The definition line itself is not a link, so an unused definition is never checked.
- External links are not verified; there is no network access.
- Links written as raw HTML are not extracted.

## Anchor checker

**Claims:** the fragment of a link, either alone (`[x](#heading)`, same document) or after a file (`[x](other.md#heading)`).

The target must be an existing markdown file. If the file is missing (that is the link checker's finding) or is not markdown, the claim is skipped silently. Heading slugs follow the GitHub convention: lower-case, punctuation removed except hyphens and underscores, spaces turned into hyphens, link syntax in the heading reduced to its text, and repeated headings suffixed with `-1`, `-2` and so on. The fragment is compared lower-cased.

**ERROR:** `anchor does not match any heading: ...`, followed by up to three available slugs (closest matches first).

Example:

```markdown
## Exit codes

Jump to [exit codes](#exit-codes).     <- ok
Jump to [exit codes](#exit-code).      <- ERROR, lists #exit-codes
```

Limitations:

- Only headings define anchors. Explicit HTML ids and names are not recognised.
- The slug rules approximate GitHub's. Other renderers may slug differently.
- Fragments into non-markdown files are not verified.

## Command checker

**Claims:** the first command word of each line in code fences whose language is `bash`, `sh`, `shell`, `zsh` or `console`, plus prompted lines (`$ ` or `> `) in fences with no language. Fences with any other language (`python`, `yaml`, `text`, `markdown` ...) are not read, so always give example output a language.

How lines are read:

- One leading `$ ` or `> ` prompt is removed.
- In a `console` fence that has at least one prompted line, unprompted lines are treated as output and ignored.
- Blank lines, comment lines and the continuation lines after a trailing backslash are skipped.
- Leading variable assignments are skipped to find the command word.
- A first word is never claimed when it is all capitals (two or more characters, such as `DEBUG`), ends with `:` or `.`, contains a parenthesis, or is a dotted identifier (contains `.` but no `/`, such as `httpx.ProxyError:`).
- Lines whose first word is a shell builtin or keyword (`cd`, `export`, `source`, `set`, `echo`, `exit`, `if`, `for`, `while` and similar) or starts with an operator such as a pipe, ampersand, semicolon, parenthesis or brace are skipped.

Continuation lines are joined into one logical line before it is read, lines starting with box-drawing glyphs (`│ ├ └ ─`, tree output) or `$Env:` (PowerShell) are skipped, and a trailing `# (N)!` mkdocs annotation is stripped.

Policy: a command is an ERROR only when it looks repo-local and is missing. A token is accepted without a finding when it exists relative to the repository root, is `python`, `python3` or a path ending in "/python", or is a command the repo defines (`[project.scripts]`, `[project.gui-scripts]`, `[tool.poetry.scripts]`, `console_scripts` in setup.cfg, and `bin` in package.json). A missing token is an ERROR when it:

- starts with `./` or `../`, or
- contains a path separator and should resolve under the repo (absolute and home-relative paths, and paths under virtualenv or node_modules directories, are exempt), or
- is a bare name while the repo defines its own commands and the name matches none of them (unless it is found on `PATH` or in the built-in tool list).

Every other bare token that is not found is never an ERROR. It is silent when it is on the `PATH` or in the built-in known-tools allowlist (git, docker, make, curl, pip, npm, node, python, uv, uvx, pnpm, yarn, bun, deno, conda, gcloud, kubectl, brew, apt, cargo, go, java, mvn, gradle and many more). It is also silent when an install-looking line (`pip install`, `npm install`, `uv add`, `brew install`, `cargo install`, `apt install`) appears above it in the same fence. Otherwise it is an INFO.

For `python -m MODULE` the module is also checked: it must be a file or package under the root or under `src/`, a standard-library module, or have one of the well-known names above as its top-level package.

**ERROR:** `documented command not found: ...` (repo-local tokens only) or `documented python module not found: ...`. **INFO:** `command not found in repo or on PATH (external tool?)`. This checker produces no warnings.

Example:

````markdown
```bash
$ python -m myapp.cli --help
./scripts/deploy.sh
mytool run
```
````

The first line is an ERROR unless the module exists under the root or `src/`. The second is an ERROR if that script is missing. The third is an INFO unless `mytool` is on the `PATH`, installed by an earlier line of the same fence, or defined by the repo. If the repo defines its own commands and `mytool` is not one of them, it is an ERROR.

Limitations:

- Only the first command of a line is claimed. Anything after a pipe, `&&`, `||` or `;` is ignored.
- Results depend on the machine: a tool that is installed locally but not in CI (or the reverse) changes the outcome.
- Arguments are not checked, except the module in `python -m MODULE`. A script path passed to python is not verified.
- Lines are read one at a time, so the body of a heredoc or a multi-line quoted string can be mistaken for commands.
- Python modules are located by file name only, so a module provided by an installed third-party package is reported unless its top-level name is one of the well-known ones.

## Historical documents

Changelog-type files describe the past, not the present, so a dead path, command or variable in them is not drift. Files whose base name matches `CHANGELOG*.md`, `HISTORY*.md`, `CHANGES*.md` or `RELEASES*.md`, or whose path matches `docs/release-notes*.md` (case-insensitive), run only the link and anchor checkers. Path, command and env claims are not extracted from them. The `historical` option adds glob patterns (matched against the base name and the root-relative path, case-insensitive); `historical_full_scan = true` turns the policy off. These files still count as documentation for the undocumented-variable warning.

## Env checker

**Claims, documentation side:** environment variable names found in inline code and in shell-language fences (same languages as the command checker). A name must match the pattern of three or more upper-case letters, digits and underscores starting with a letter. It must contain an underscore or appear in an assignment, an export statement or a dollar-sign reference. Common words (`TODO`, `GET`, `JSON`, `README` and similar) and OS-provided variables (`PATH`, `HOME`, `USER`, `SHELL`, `PWD`, `TERM`, `LANG` and similar) are ignored. In inline code a bare name only counts when it is the entire span. Fences with an explicit non-shell language (`python`, `json`, `text` ...) are never mined. In fences with no language tag only lines starting with the prompt `$ ` or `> ` are read (prompt removed), because bare fences mostly show output. In shell-language fences, lines that look like output are skipped: lines containing `...`, lines whose first token ends with `:` (such as `ssl.SSLCertVerificationError: ...`), and every line from a `Traceback (most recent call last)` header to the end of the fence.

**Code side:** names read by source files anywhere under the root (not only the scanned paths), found with regular expressions:

- Python: `os.environ["X"]`, `os.environ.get(...)`, `os.environ.setdefault(...)`, `os.environ.pop(...)` and `os.getenv(...)` with a string literal name, and the upper-cased annotated field names of classes that list `BaseSettings` as a base.
- JavaScript and TypeScript (extensions js, jsx, ts, tsx, mjs, cjs): `process.env.X` and `process.env["X"]`.
- Shell scripts (extension sh): a dollar-sign reference, with or without braces, minus variables assigned in the same script (plain, `export` or `local` assignment, `for` loop variable, `read` target). Comment lines are ignored.

Hidden directories, `venv`, `node_modules`, `dist` and `__pycache__` are not searched.

**ERROR:** `documented env var is not read by the code: ...`.

**INFO:** `ambient environment variable (read by the OS/stdlib, not this codebase): ...` replaces the ERROR when the documented name is in the built-in ambient set and no source file reads it. The set holds variables that the OS, the shell, the standard library or HTTP libraries read on their own: `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, `PATH`, `HOME`, `USER`, `SHELL`, `TMPDIR`, `TEMP`, `TMP`, `LANG`, `LC_ALL`, `TZ`, `TERM`, `SSLKEYLOGFILE`, `SSL_CERT_FILE`, `SSL_CERT_DIR`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`, `PYTHONPATH`, `VIRTUAL_ENV`, `CI`, `EDITOR`, `PAGER`, `XDG_CONFIG_HOME` and `XDG_CACHE_HOME`. Add project-specific names with `ambient_extra`. Ambient names are also never reported by the undocumented warning below, even when the code reads them.

**WARNING:** `env var read by code but undocumented: ...` when the code reads a variable whose name does not appear as an upper-case word anywhere in the scanned documents. The raw text is searched, so a mention in prose or in an HTML comment is enough. These warnings are attached to README.md line 1 when that file exists, otherwise to line 1 of the first scanned document.

Names listed in `env_stoplist_extra` are dropped in both directions. Disabling the checker disables both directions.

Example:

```markdown
Set `DATABASE_URL` before starting the service.
```

This is an ERROR if no source file reads that name, and clears once one does, for example through `os.environ["DATABASE_URL"]`.

Limitations:

- Only string-literal names are seen. `os.environ[name]` with a variable, and reads through helper wrappers such as `get_env("X")`, are invisible.
- Only Python, JavaScript/TypeScript and shell sources are scanned. Dockerfiles, YAML, dotenv files and other languages are not.
- Shell variables defined elsewhere, for example in a sourced file, can be reported as reads.
- For `BaseSettings`, `env_prefix`, `alias` and `validation_alias` are ignored: the upper-cased field name is taken as the variable name.
- The comparison is by name only. Test fixtures and sample sources under the root count as code, so their reads also raise undocumented warnings.
- An upper-case word with an underscore in inline code is treated as an env var even if it is something else (a constant, an enum value). Put it in `env_stoplist_extra` or add an ignore comment.

## Baseline behaviour

The baseline is applied to all checkers after they run. A baselined finding is identified by file, line, checker and claimed value. It is line-sensitive: when lines are inserted above a baselined finding, its key no longer matches and it is reported again until the baseline is regenerated. Messages are not part of the key. Env warnings attached to README.md line 1 are keyed there too.
