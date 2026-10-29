# Checker reference

DriftWatch has five checkers, covering four kinds of claim: paths, links and anchors, commands, and environment variables. Each claim comes from the markdown structure (parsed with markdown-it-py) and is checked against the file system or the source code. Nothing is executed or imported. The names in the table are what `enabled_checkers`, `ignore=` comments and the `[checker]` tag in the output use. See the [user guide](user-guide.md) for configuration and suppression.

| Checker | Severity produced | Checks |
|---------|-------------------|--------|
| `path` | error, warning | Inline code spans that look like file paths exist. |
| `link` | error | Relative link and image targets exist. |
| `anchor` | error, info | Fragments (`#heading`) match a heading, explicit id or HTML anchor in the target markdown file. |
| `command` | error, info | Repo-defined commands have a target and `./` paths under existing directories exist; every other unknown tool is only INFO. |
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

Further filters remove tokens that only look like paths:

- A token with a slash whose last segment has no file extension is claimed only with a leading marker (`./`, `../`, `~/` or an absolute `/`). A trailing-slash directory form is claimed with a marker, or when its first segment is one of `docs src tests scripts app`. This drops media types, `owner/repo` names and prose such as `async/await`.
- Tokens starting with `@` (npm scopes) are never claimed.
- Media types under the IANA top-level types (`application audio font image message model multipart text video`) are not claimed, unless the last segment ends in a known file extension (so a file such as `image/logo.png` <!-- driftwatch: ignore=path --> is still checked).
- CIDR and IP forms (IPv4 or colon-hex groups with a `/N` suffix, optionally in square brackets) are not claimed.
- Bare extensions such as `.json` or `.py` are not claimed.
- Tokens containing `^`, `$`, `|` or a shell redirection like `2>/` are not claimed.
- A colon after the first segment (as in `owner/repo:HEAD`) disqualifies the token.

Spans containing a single or double quote character (string literals such as `b"HTTP/1.1"`) are never claimed.

**Link text:** an inline code span that is the visible text of a markdown link whose target is an `http(s)` URL is never claimed. It names the link's subject, not a repo file. Links with relative targets are unaffected.

**Tree diagrams:** a code fence that contains box-drawing glyphs (`├ └ │`), or that looks like a directory listing (two or more consecutive lines ending in `/`), is a tree diagram. Inline code spans in the same document that name an entry drawn in such a fence (compared by last path segment, so a prose mention of a nested directory matches its entry) are not claimed. Tutorials draw the layout the reader is about to build, then refer to its parts in prose.

**Severity ladder for missing paths:** a path that exists nowhere is graded by how much the document points into the real tree.

- ERROR (`path does not exist: ...`) only when the claimed path has a directory component and its parent directory exists under the repository root or relative to the document. The document points into a real tree and names a file or directory that is not there.
- WARNING (`path does not exist and its parent directory is not in the repo: ...`) otherwise: a bare file name, or a path whose parent directory is missing too.
- INFO (`common ecosystem file, not present in this repo`) when the base name is in the built-in ecosystem list: `requirements.txt`, `setup.py`, `pyproject.toml`, `package.json`, `mypy.ini`, `serverless.yml`, `llms.txt`, `index.html`, `404.html`, `openapi.json`, `docker-compose.yml`, `Dockerfile`, `Makefile`, `.env`, `mkdocs.yml`, `mkdocs.yaml`, `swagger-ui.css`, `swagger-ui-bundle.js`, `redoc.standalone.js` and `favicon.ico`. These files are named in docs as things a project of that kind has, whether or not this repo has one.

The placeholder, found-elsewhere and ecosystem rules below are tried before the ladder, in that order, so a file that exists at another location still gets the found-at WARNING.

**ERROR:** `path does not exist: ...` when the path exists neither relative to the repository root (the working directory) nor relative to the directory of the document that mentions it, and no downgrade applies (see the ladder above).

**WARNING:** `absolute path in docs is machine-specific` for any absolute path, whether or not it exists.

**WARNING:** `placeholder path in docs` replaces the ERROR when the missing path's base name without extension is one of `foo bar baz example sample myfile yourfile placeholder test`.

**WARNING:** `path does not exist at the stated location; found at: <path>` replaces the ERROR when a file with the same base name exists elsewhere under the root. The first match in sorted order is reported. The index is built once per scan and skips hidden directories, `venv`, `node_modules`, `dist` and `__pycache__`. Because hidden directories are skipped, a bare workflow name ending in .yml or .yaml is also looked up under the repository's GitHub workflows directory and gets the same WARNING when it exists there.

**INFO:** `user-local path, not checked` for paths starting with `~/`. They are claimed but never looked up.

**INFO:** `file the reader is asked to create` replaces the ERROR when the claim's line, or either of the two lines before it, contains (case-insensitive) `create a file`, `save it as`, `name it`, `add a file`, `new file called`, the words `called` or `named`, `for example`, `e.g.`, `your ` or `a file like`. Such a path is an instruction or an example, not a claim about the repo.

**INFO:** `generated or ignored artifact` replaces the ERROR when the missing path lies under `site/`, `build/`, `dist/` or `docs/build/`, or is matched by the repository's root `.gitignore`. The matcher is deliberately simple: exact names, `dir/` prefixes, and `*` globs via `fnmatch`, matched against the whole path (patterns containing a slash or starting with `/`) or any single path component (other patterns). Limitation: negation lines (`!pattern`) are not supported and are ignored, as are nested `.gitignore` files.

**Fence titles:** an inline code span equal to the title of any code fence in the same document is never claimed. A title is `title="NAME"` (mkdocs/material) or a filename token after the language, as in ```` ```python main.py ````.

**Test content:** markdown under `tests/`, `test/`, `fixtures/` or `testdata/` directories (relative to the root) is not scanned by default, since it is usually fixture input. Set `scan_tests = true` in the configuration to scan it.

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

Resolution: relative to the document's directory first, then relative to the repository root. A target starting with a slash is site-absolute: it is tried against the scan root, then the docs directory (docs, or the mkdocs docs_dir), then the top directory of the current document's tree, each with the extensionless chain below; only if none resolve is it an error. A directory counts as existing. A target with no file extension is tried, in order, as written, then with the markdown suffix appended, then as a directory containing an index file, then as a directory containing a readme file (mkdocs conventions; the file names are index.md and README.md). The anchor checker resolves cross-document targets the same way.

MyST labels: a line of the form `(name)=` in any scanned document defines a global label, and a link whose target equals that name resolves. Detection relies on that syntax alone; no Sphinx conf.py is needed.

**ERROR:** `link target does not exist: ...`.

Mkdocs mode: mkdocs serves a page at a directory URL, so a relative extensionless link is resolved against that URL, not against the file. The mode is on for documents under the docs directory when a mkdocs.yml or mkdocs.yaml file exists at the scan root (the `mkdocs` option defaults to `"auto"`; `true` forces it on, `false` off). The docs directory comes from a `docs_dir:` line in that file if present, otherwise it is docs. For a page named sub/page.md the URL directory is sub/page/, and index.md and README.md map to their parent directory. The link is resolved against the URL directory, then mapped back to a file: the result with the markdown suffix appended, or the result as a directory containing index.md, inside the docs directory. For example, a link to ../advanced/transports from docs/async.md resolves to docs/advanced/transports.md. This is tried first; if it finds nothing, the chain above still applies. Targets with a file extension, and targets starting with a slash, always use the chain above. The anchor checker maps cross-document targets the same way.

Example:

```markdown
See [the guide](docs/guide.md) and ![diagram](img/flow.png).
```

Limitations:

- Reference-style links (`[text][ref]`) are resolved by the parser to their definition and checked at the line where they are used. The definition line itself is not a link, so an unused definition is never checked. Names defined only by `[name]: target` are never matched against MyST labels.
- External links are not verified; there is no network access.
- Links written as raw HTML are not extracted.

## Anchor checker

**Claims:** the fragment of a link, either alone (`[x](#heading)`, same document) or after a file (`[x](other.md#heading)`).

The target must be an existing markdown file. If the file is missing (that is the link checker's finding) or is not markdown, the claim is skipped silently. A document defines these anchor ids:

- Heading slugs, following the GitHub convention: a trailing attr_list, HTML tags and HTML entities are dropped, emphasis markers are removed (`*`, and `_` at word edges, so `snake_case` is kept), then lower-case, punctuation removed (apostrophes, straight or curly, included) except hyphens and underscores, spaces turned into hyphens, link syntax reduced to its text, and repeated headings suffixed with `-1`, `-2` and so on. `### How _Black_ wraps lines` gives `how-black-wraps-lines`.
- attr_list ids (`## Installation { #installation }`, a standalone `[](){#typeddict}` or `{#id}` line) anywhere in the file.
- HTML `id=` and `name=` values (`<a id="x"></a>`, `<a name="y"></a>`) anywhere in the file.

The fragment is compared as written and lower-cased.

A document is *dynamic* when it contains a mkdocstrings `::: module.path` directive, a `{{ macro }}` template (not `${{ }}`) or a `--8<--` snippet include. Its content is generated or included at build time, so an unmatched anchor into it cannot be judged.

**ERROR:** `anchor does not match any heading: ...`, followed by up to three available slugs (closest matches first).

**INFO:** `anchor target is plugin-generated or included content, cannot verify statically`, for an unmatched anchor into a dynamic document.

Example:

```markdown
## Exit codes

Jump to [exit codes](#exit-codes).     <- ok
Jump to [exit codes](#exit-code).      <- ERROR, lists #exit-codes
```

Limitations:

- Ids are collected from the raw text, including code fences, so an id shown in an example still counts. Anchors produced by plugins are only recognised as a whole (the dynamic INFO), never individually.
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

**Output guards:** lines whose first command token starts with `-` (flags such as `--help`), starts with `//`, or contains `://` (a URL) are never claimed, nor are lines with a token containing `+=` (`fpath+=(...)`). The zsh configuration words `fpath`, `zstyle`, `autoload` and `compinit` count as shell builtins and are skipped. These guards apply in every fence, prompted or not.

Policy (v3): a command is an ERROR in exactly two cases, everything else unknown is at most INFO.

- **A repo-defined command whose target is missing.** Repo-defined commands are `[project.scripts]`, `[project.gui-scripts]`, `[tool.poetry.scripts]`, `console_scripts` in setup.cfg, and `bin` in package.json. A defined command passes when its target exists (the `module` of `module:func` as a file or package under the root or `src/`, or the `bin` file) and is an ERROR when it does not.
- **A `./` or `../` path that does not exist while its parent directory exists below the repository root.** A path whose parent is the repository root itself (such as `./traefik`, usually a downloaded binary) or whose parent does not exist is only INFO. A path without a `./` or `../` prefix (`scripts/x.sh`) is no longer an ERROR candidate.

A token is accepted without a finding when it exists relative to the repository root, is `python`, `python3` or a path ending in "/python", or is a repo-defined command with an existing target. Any other token is silent when it is on the `PATH` or in the built-in known-tools allowlist (git, docker, make, curl, pip, npm, node, python, uv, uvx, pnpm, yarn, bun, deno, conda, gcloud, kubectl, brew, apt, cargo, go, java, mvn, gradle and many more). It is also silent when an install-looking line (`pip install`, `npm install`, `uv add`, `brew install`, `cargo install`, `apt install`) appears above it in the same fence.

**Doc-defined CLIs:** a token is also silent when the same document mentions it (hyphens and underscores compare equal) within three lines of a definition marker: `pip install`, `console_scripts`, `[project.scripts]`, `entry_points`, `add_typer` or `Typer(`. Tutorials invent tools such as `rick-portal-gun` and show how to install or declare them. Otherwise the token is an INFO.

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

The first line is an ERROR unless the module exists under the root or `src/`. The second is an ERROR if `scripts/` exists below the root but the script does not (INFO when `scripts/` is missing). The third is an INFO unless `mytool` is on the `PATH`, installed by an earlier line of the same fence, declared next to a definition marker in the document, or defined by the repo with an existing target.

Limitations:

- Only the first command of a line is claimed. Anything after a pipe, `&&`, `||` or `;` is ignored.
- Results depend on the machine: a tool that is installed locally but not in CI (or the reverse) changes the outcome.
- Arguments are not checked, except the module in `python -m MODULE`. A script path passed to python is not verified.
- Lines are read one at a time, so the body of a heredoc or a multi-line quoted string can be mistaken for commands.
- Python modules are located by file name only, so a module provided by an installed third-party package is reported unless its top-level name is one of the well-known ones.

## Historical documents

Changelog-type files describe the past, not the present, so a dead path, command or variable in them is not drift. Files whose base name matches `CHANGELOG*.md`, `HISTORY*.md`, `CHANGES*.md` or `RELEASES*.md`, or whose path matches `docs/release-notes*.md` (case-insensitive), run only the link and anchor checkers. Path, command and env claims are not extracted from them. The `historical` option adds glob patterns (matched against the base name and the root-relative path, case-insensitive); `historical_full_scan = true` turns the policy off. These files still count as documentation for the undocumented-variable warning.

## Env checker

**Claims, documentation side:** environment variable names found in inline code and in shell-language fences (same languages as the command checker). A name must match the pattern of three or more upper-case letters, digits and underscores starting with a letter. It is claimed only in an explicit shell/env context: `export X`, an assignment `X=value` (at line start or before a command, including `env X=value`), or a `$X` / `${X}` reference. Inline code counts only when the span itself contains such an assignment or `$` usage. A bare upper-case token in prose or inline code (`CUSTOM_MESSAGES`, `DEFAULT_MAP`) is a constant name, not a claim. Names with an error-code prefix (`ERR_`, `FST_ERR_`, or `E` plus two or more capitals and an underscore, such as `EACCES_`) are never claimed, even in shell context. Common words (`TODO`, `GET`, `JSON`, `README` and similar) and OS-provided variables (`PATH`, `HOME`, `USER`, `SHELL`, `PWD`, `TERM`, `LANG` and similar) are ignored. Fences with an explicit non-shell language (`python`, `json`, `text` ...) are never mined. In fences with no language tag only lines starting with the prompt `$ ` or `> ` are read (prompt removed), because bare fences mostly show output. In shell-language fences, lines that look like output are skipped: lines containing `...`, lines whose first token ends with `:` (such as `ssl.SSLCertVerificationError: ...`), and every line from a `Traceback (most recent call last)` header to the end of the fence.

**Code side:** names read by source files anywhere under the root (not only the scanned paths), found with regular expressions:

- Python: `os.environ["X"]`, `os.environ.get(...)`, `os.environ.setdefault(...)`, `os.environ.pop(...)` and `os.getenv(...)` with a string literal name, and the upper-cased annotated field names of classes that list `BaseSettings` as a base.
- JavaScript and TypeScript (extensions js, jsx, ts, tsx, mjs, cjs): `process.env.X` and `process.env["X"]`.
- Shell scripts (extension sh): a dollar-sign reference, with or without braces, minus variables assigned in the same script (plain, `export` or `local` assignment, `for` loop variable, `read` target). Comment lines are ignored.

Hidden directories, `venv`, `node_modules`, `dist` and `__pycache__` are not searched. Example code (`docs_src/`, `examples/`, `example/`) is ordinary code: its reads count, so a variable read only by a documentation example is not reported as unread.

**Policy (v3): documented-but-not-read is suppressed** (no finding at all, not even INFO) for a non-ambient variable when any of these holds:

- **Auto env prefix.** Python code contains `auto_envvar_prefix="PREFIX"` (keyword or `"auto_envvar_prefix": "PREFIX"` dict form, the click convention). Every documented `PREFIX_*` name counts as read.
- **Defined in the document.** The same document has a line that starts with an assignment (`.env` style, optional `$ ` prompt, optional `export`) to that name, on a different line than the claim. The reader is shown how to define it. A lone export line is the claim itself and is still checked.
- **Set inline for another program.** The claim's line assigns the name directly before a command word, as in running python with a hash-seed variable set in front. The variable is set for that program's environment.
- **Set in CI.** The name appears as an upper-case word in any `.github/workflows/*.yml` or `*.yaml` of the repository.

Ambient names keep their INFO regardless. A variable the code reads but no document mentions still gets the WARNING below, and a documented non-ambient variable matching none of the rules above is still an ERROR.

**ERROR:** `documented env var is not read by the code: ...`.

**INFO:** `ambient environment variable (read by the OS/stdlib, not this codebase): ...` replaces the ERROR when the documented name is in the built-in ambient set and no source file reads it. The set holds variables that the OS, the shell, the standard library or HTTP libraries read on their own: `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, `PATH`, `HOME`, `USER`, `SHELL`, `TMPDIR`, `TEMP`, `TMP`, `LANG`, `LC_ALL`, `TZ`, `TERM`, `SSLKEYLOGFILE`, `SSL_CERT_FILE`, `SSL_CERT_DIR`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`, `PYTHONPATH`, `VIRTUAL_ENV`, `CI`, `EDITOR`, `PAGER`, `XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, and the interpreter/runtime variables `PYTHONUTF8`, `PYTHONIOENCODING`, `PYTHONDONTWRITEBYTECODE`, `PYTHONWARNINGS`, `NODE_OPTIONS`, `NODE_ENV`, `WATCHFILES_FORCE_POLLING`, `FORCE_COLOR`, `NO_COLOR` and `DEBIAN_FRONTEND`. Add project-specific names with `ambient_extra`. Ambient names are also never reported by the undocumented warning below, even when the code reads them.

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
- A name in an explicit env context (`X=1`, `export X`, `$X`) is treated as an env var even if it is something else. Put it in `env_stoplist_extra` or add an ignore comment. A variable documented only as a bare name in prose (`` `MY_TOKEN` ``) is no longer a claim, so it is not checked against the code.

## Baseline behaviour

The baseline is applied to all checkers after they run. A baselined finding is identified by file, line, checker and claimed value. It is line-sensitive: when lines are inserted above a baselined finding, its key no longer matches and it is reported again until the baseline is regenerated. Messages are not part of the key. Env warnings attached to README.md line 1 are keyed there too.
