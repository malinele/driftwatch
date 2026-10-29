# Checker reference

DriftWatch has five checkers, covering four kinds of claim: paths, links and anchors, commands, and environment variables. Each claim comes from the markdown structure (parsed with markdown-it-py) and is checked against the file system or the source code. Nothing is executed or imported. The names in the table are what `enabled_checkers`, `ignore=` comments and the `[checker]` tag in the output use. See the [user guide](user-guide.md) for configuration and suppression.

| Checker | Severity produced | Checks |
|---------|-------------------|--------|
| `path` | error, warning | Inline code spans that look like file paths exist. |
| `link` | error | Relative link and image targets exist. |
| `anchor` | error | Fragments (`#heading`) match a heading in the target markdown file. |
| `command` | error | The first word of shell lines in code fences is a known command. |
| `env` | error, warning | Environment variables in docs and in code agree. |

## Current wiring status

`scan` currently extracts only path claims and environment variable claims from documents (`src/driftwatch/scanner.py`). The link, anchor and command checkers are implemented and unit-tested, but a normal scan does not hand them any claims, so today they report nothing. The end-to-end test that expects them to fire is marked as an expected failure in `tests/test_e2e.py`. The sections below describe what each checker does once it receives claims.

## Path checker

**Claims:** every inline code span in prose that looks like a repository path. Code fences are not read for paths.

A span counts as a path when, after trimming whitespace and trailing punctuation (`. , ; : ! ? ) ] } ' "`), it:

- is non-empty and shorter than 200 characters,
- contains no whitespace and does not start with a hyphen,
- contains no asterisk or opening brace and does not start with a URL scheme (`https://`, `git@host:` and similar),
- and either contains a slash or ends in one of: `.py .md .toml .yaml .yml .json .txt .sh .cfg .ini .html .css .js .ts .png .lock` (case-insensitive).

**ERROR:** `path does not exist: ...` when the path exists neither relative to the repository root (the working directory) nor relative to the directory of the document that mentions it.

**WARNING:** `absolute path in docs is machine-specific` for any absolute path, whether or not it exists.

Example:

```markdown
Entry point: `src/app/main.py`       <- ERROR if the file is missing
Config lives in `/etc/app/conf.toml` <- WARNING, absolute
Run it with `make test`              <- not a path (contains whitespace)
```

Limitations:

- A bare file name such as main.py is only looked up in the root and in the document's own directory. There is no recursive search.
- Anything with a slash is treated as a path, so a span like "and/or" or "v1/v2" is reported, and a home-relative path starting with a tilde is looked up relative to the root and reported as missing. Use an ignore comment for these.
- Globs (anything with an asterisk) and brace expansions are skipped, not checked.
- A span without a slash and with an extension outside the list above is not seen.

## Link checker

**Claims:** the target of every `[text](target)` link and `![alt](src)` image.

Skipped: any target with a URL scheme (http, https, mailto and so on) or starting with two slashes, empty targets, and autolinks written in angle brackets. A query string is dropped and percent-escapes are decoded before the lookup. A pure fragment link such as `[x](#section)` has no file part and is handled by the anchor checker; a link like `[x](other.md#section)` yields one link claim and one anchor claim.

Resolution: relative to the document's directory first, then relative to the repository root. A target starting with a slash is relative to the repository root only. A directory counts as existing.

**ERROR:** `link target does not exist: ...`.

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

**Claims:** the first command word of each line in code fences whose language is `bash`, `sh`, `shell`, `zsh`, `console`, or empty. Fences with any other language (`python`, `yaml`, `text`, `markdown` ...) are not read, so always give example output a language.

How lines are read:

- One leading `$ ` or `> ` prompt is removed.
- In a `console` fence that has at least one prompted line, unprompted lines are treated as output and ignored.
- Blank lines, comment lines and the continuation lines after a trailing backslash are skipped.
- Leading variable assignments are skipped to find the command word.
- Lines whose first word is a shell builtin or keyword (`cd`, `export`, `source`, `set`, `echo`, `exit`, `if`, `for`, `while` and similar) or starts with an operator such as a pipe, ampersand, semicolon, parenthesis or brace are skipped.

A command is accepted when any of these holds: it exists relative to the repository root (so a script path like ./scripts/deploy.sh works), it is found on the `PATH` of the machine running the scan, it is `python`, `python3` or a path ending in "/python", or it is one of `git docker make curl pip npm node uvicorn pytest`.

For `python -m MODULE` the module is also checked: it must be a file or package under the root or under `src/`, a standard-library module, or have one of the well-known names above as its top-level package.

**ERROR:** `documented command not found: ...` or `documented python module not found: ...`. This checker produces no warnings.

Example:

````markdown
```bash
$ python -m myapp.cli --help
./scripts/deploy.sh
mytool run
```
````

The first line is an ERROR unless the module exists under the root or `src/`. The second is accepted if that script exists. The third is an ERROR unless `mytool` is on the `PATH`.

Limitations:

- Only the first command of a line is claimed. Anything after a pipe, `&&`, `||` or `;` is ignored.
- Results depend on the machine: a tool that is installed locally but not in CI (or the reverse) changes the outcome.
- Arguments are not checked, except the module in `python -m MODULE`. A script path passed to python is not verified.
- Lines are read one at a time, so the body of a heredoc or a multi-line quoted string can be mistaken for commands.
- Python modules are located by file name only, so a module provided by an installed third-party package is reported unless its top-level name is one of the well-known ones.

## Env checker

**Claims, documentation side:** environment variable names found in inline code and in shell-language fences (same languages as the command checker). A name must match the pattern of three or more upper-case letters, digits and underscores starting with a letter. It must contain an underscore or appear in an assignment, an export statement or a dollar-sign reference. Common words (`TODO`, `GET`, `JSON`, `README` and similar) and OS-provided variables (`PATH`, `HOME`, `USER`, `SHELL`, `PWD`, `TERM`, `LANG` and similar) are ignored. In inline code a bare name only counts when it is the entire span.

**Code side:** names read by source files anywhere under the root (not only the scanned paths), found with regular expressions:

- Python: `os.environ["X"]`, `os.environ.get(...)`, `os.environ.setdefault(...)`, `os.environ.pop(...)` and `os.getenv(...)` with a string literal name, and the upper-cased annotated field names of classes that list `BaseSettings` as a base.
- JavaScript and TypeScript (extensions js, jsx, ts, tsx, mjs, cjs): `process.env.X` and `process.env["X"]`.
- Shell scripts (extension sh): a dollar-sign reference, with or without braces, minus variables assigned in the same script (plain, `export` or `local` assignment, `for` loop variable, `read` target). Comment lines are ignored.

Hidden directories, `venv`, `node_modules`, `dist` and `__pycache__` are not searched.

**ERROR:** `documented env var is not read by the code: ...`.

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
