"""Checker framework and the path-existence checker."""

from __future__ import annotations

import difflib
import fnmatch
import json
import os
import posixpath
import re
import shlex
import shutil
import sys
import tomllib
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Literal, Protocol

from driftwatch.envscan import scan_code_env_reads
from driftwatch.markdown import parse_markdown
from driftwatch.models import Claim, ClaimKind, Finding, Location, Severity


class Checker(Protocol):
    name: str

    def check(self, claims: list[Claim], root: Path) -> list[Finding]: ...


def is_within(candidate: Path, root: Path) -> bool:
    """True if ``candidate`` stays inside ``root``, lexically and after symlink resolution.

    The lexical test runs first and touches no filesystem entry, so a ``../..`` claim is rejected
    without ever being stat-ed; the realpath test then catches symlinks that point out of the root.
    """
    lexical = Path(os.path.abspath(candidate))
    base = Path(os.path.abspath(root))
    if not lexical.is_relative_to(base):
        return False
    return Path(os.path.realpath(lexical)).is_relative_to(Path(os.path.realpath(base)))


ESCAPE_MESSAGE = "path escapes the scanned repository"
PLACEHOLDER_STEMS = frozenset({"foo", "bar", "baz", "example", "sample", "myfile", "yourfile", "placeholder", "test"})
_INDEX_SKIP_DIRS = frozenset({"venv", "node_modules", "dist", "__pycache__"})


def _basename_index(root: Path) -> dict[str, str]:
    """Map each file basename under ``root`` to its first root-relative POSIX path (sorted walk)."""
    index: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in _INDEX_SKIP_DIRS)
        for filename in sorted(filenames):
            if filename not in index:
                index[filename] = (Path(dirpath) / filename).relative_to(root).as_posix()
    return index


_AUTHORING = re.compile(r"create a file|save it as|name it|add a file|new file called", re.IGNORECASE)
GENERATED_DIRS = ("site", "build", "dist", "docs/build")


def _read_lines(file: Path) -> list[str]:
    try:
        return file.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []


def is_authoring_instruction(claim: Claim) -> bool:
    """True if the claim's line, or the document line just before it, asks the reader to create a file."""
    if _AUTHORING.search(claim.context):
        return True
    lines = _read_lines(claim.location.file)
    previous = claim.location.line - 2
    return 0 <= previous < len(lines) and _AUTHORING.search(lines[previous]) is not None


def load_gitignore(root: Path) -> list[str]:
    """Patterns from ``root/.gitignore``. Negations (``!``) are not supported and are dropped."""
    lines = _read_lines(root / ".gitignore")
    stripped = (line.strip() for line in lines)
    return [line for line in stripped if line and not line.startswith(("#", "!"))]


def _pattern_matches(pattern: str, relative: str) -> bool:
    anchored = pattern.startswith("/") or "/" in pattern.rstrip("/")
    body = pattern.strip("/")
    parts = relative.split("/")
    if anchored:
        prefixes = ["/".join(parts[: i + 1]) for i in range(len(parts))]
        return any(fnmatch.fnmatchcase(prefix, body) for prefix in prefixes)
    return any(fnmatch.fnmatchcase(part, body) for part in parts)


def is_generated_or_ignored(relative: str, patterns: Sequence[str]) -> bool:
    """True for paths under a build-output directory or matched by a simple ``.gitignore`` matcher."""
    relative = posixpath.normpath(relative).strip("/")
    if any(relative == d or relative.startswith(f"{d}/") for d in GENERATED_DIRS):
        return True
    return any(_pattern_matches(p, relative) for p in patterns)


def _relative_to_root(candidates: Sequence[Path], root: Path) -> list[str]:
    """Root-relative POSIX paths (``..`` collapsed) for candidates that lie inside ``root``."""
    base = Path(os.path.abspath(root))
    absolute = (Path(os.path.abspath(c)) for c in candidates)
    return [a.relative_to(base).as_posix() for a in absolute if a.is_relative_to(base)]


class PathChecker:
    """Verifies that claimed paths exist relative to the repo root or the doc's directory."""

    name = "path"

    def check(self, claims: list[Claim], root: Path) -> list[Finding]:
        findings: list[Finding] = []
        index: dict[str, str] | None = None
        ignore_patterns = load_gitignore(root)
        for claim in claims:
            if claim.kind is not ClaimKind.PATH:
                continue
            finding = self._check_one(claim, root, ignore_patterns)
            if finding is not None and finding.severity is Severity.ERROR:
                if index is None:
                    index = _basename_index(root)
                finding = self._with_fallbacks(finding, index)
            if finding is not None:
                findings.append(finding)
        return findings

    def _with_fallbacks(self, finding: Finding, index: dict[str, str]) -> Finding:
        """Downgrade a missing-path ERROR to a WARNING for placeholder names or a path found elsewhere."""
        claim = finding.claim
        basename = Path(claim.value.rstrip("/")).name
        if Path(basename).stem.lower() in PLACEHOLDER_STEMS:
            return Finding(claim, Severity.WARNING, "placeholder path in docs", self.name)
        found = index.get(basename)
        if found is not None:
            message = f"path does not exist at the stated location; found at: {found}"
            return Finding(claim, Severity.WARNING, message, self.name)
        return finding

    def _check_one(self, claim: Claim, root: Path, ignore_patterns: Sequence[str] = ()) -> Finding | None:
        if claim.value.startswith("~/"):
            return Finding(claim, Severity.INFO, "user-local path, not checked", self.name)
        if Path(claim.value).is_absolute():
            return Finding(claim, Severity.WARNING, "absolute path in docs is machine-specific", self.name)
        doc_dir = claim.location.file.parent
        candidates = [root / claim.value, doc_dir / claim.value]
        inside = [c for c in candidates if is_within(c, root)]
        if not inside:
            return Finding(claim, Severity.WARNING, f"{ESCAPE_MESSAGE}: {claim.value}", self.name)
        if any(c.exists() for c in inside):
            return None
        if is_authoring_instruction(claim):
            return Finding(claim, Severity.INFO, "file the reader is asked to create", self.name)
        if any(is_generated_or_ignored(r, ignore_patterns) for r in _relative_to_root(inside, root)):
            return Finding(claim, Severity.INFO, "generated or ignored artifact", self.name)
        return Finding(claim, Severity.ERROR, f"path does not exist: {claim.value}", self.name)


MAX_SUGGESTIONS = 3
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_NON_SLUG_CHARS = re.compile(r"[^\w\- ]")


def slugify(text: str) -> str:
    """GitHub-style heading slug: lowercase, punctuation dropped (hyphens kept), spaces to hyphens."""
    text = _MD_LINK.sub(r"\1", text).strip().lower()
    return _NON_SLUG_CHARS.sub("", text).replace(" ", "-")


def heading_slugs(headings: list[str]) -> list[str]:
    """Slugs for ``headings`` in order; repeated slugs get ``-1``, ``-2`` ... suffixes like GitHub."""
    used: set[str] = set()
    counts: dict[str, int] = {}
    slugs: list[str] = []
    for text in headings:
        base = slugify(text)
        slug = base
        while slug in used:
            counts[base] = counts.get(base, 0) + 1
            slug = f"{base}-{counts[base]}"
        used.add(slug)
        slugs.append(slug)
    return slugs


MKDOCS_FILES = ("mkdocs.yml", "mkdocs.yaml")
DEFAULT_DOCS_DIR = "docs"
_DOCS_DIR = re.compile(r"""^docs_dir:[ \t]*['"]?([^'"#\r\n]*?)['"]?[ \t]*(?:#.*)?$""", re.MULTILINE)
_INDEX_STEMS = frozenset({"index", "readme"})
MkdocsSetting = bool | Literal["auto"]


def mkdocs_docs_dir(root: Path, setting: MkdocsSetting = "auto") -> Path | None:
    """The mkdocs docs directory when mkdocs mode is on for ``root``, else ``None``.

    ``auto`` enables the mode when mkdocs.yml / mkdocs.yaml exists at the root; ``docs_dir`` is read with a
    regex (default ``docs``). ``True`` forces the mode on, ``False`` off.
    """
    if setting is False:
        return None
    config = next((root / name for name in MKDOCS_FILES if (root / name).is_file()), None)
    if config is None and setting != "auto":
        return root / DEFAULT_DOCS_DIR
    if config is None:
        return None
    try:
        match = _DOCS_DIR.search(config.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        match = None
    docs_dir = match.group(1).strip() if match else ""
    return root / (docs_dir or DEFAULT_DOCS_DIR)


def _mkdocs_resolve(file_part: str, doc_file: Path, root: Path, docs_dir: Path) -> Path | None:
    """Resolve an extensionless relative link with mkdocs directory-URL semantics, or ``None``."""
    if file_part.startswith("/") or Path(file_part.rstrip("/")).suffix:
        return None
    docs = Path(os.path.abspath(docs_dir))
    page = Path(os.path.abspath(doc_file))
    if not page.is_relative_to(docs):
        return None
    relative = page.relative_to(docs)
    url_dir = relative.parent if relative.stem.lower() in _INDEX_STEMS else relative.with_suffix("")
    resolved = posixpath.normpath(posixpath.join(url_dir.as_posix(), file_part))
    if resolved == ".." or resolved.startswith("../"):
        return None
    base = docs / resolved if resolved != "." else docs
    candidates = [base.with_name(base.name + ".md"), base / "index.md"] if resolved != "." else [base / "index.md"]
    return next((c for c in candidates if is_within(c, root) and c.is_file()), None)


def _resolve(file_part: str, doc_file: Path, root: Path, docs_dir: Path | None = None) -> Path | None:
    """Resolve a link target: mkdocs URL semantics first (if on), then doc-relative, then repo root.

    A leading ``/`` means repo root.
    """
    if docs_dir is not None:
        found = _mkdocs_resolve(file_part, doc_file, root, docs_dir)
        if found is not None:
            return found
    if file_part.startswith("/"):
        bases = [root / file_part.lstrip("/")]
    else:
        bases = [doc_file.parent / file_part, root / file_part]
    candidates = [c for base in bases for c in _with_conventions(base, file_part)]
    return next((c for c in candidates if is_within(c, root) and c.exists()), None)


def _with_conventions(base: Path, file_part: str) -> list[Path]:
    """``base`` plus, for extensionless targets, the mkdocs-style ``.md`` / ``index.md`` / ``README.md`` forms."""
    if Path(file_part.rstrip("/")).suffix:
        return [base]
    return [base, base.with_name(base.name + ".md"), base / "index.md", base / "README.md"]


class LinkChecker:
    """Verifies that relative link and image targets exist."""

    name = "link"

    def check(self, claims: list[Claim], root: Path, mkdocs: MkdocsSetting = "auto") -> list[Finding]:
        docs_dir = mkdocs_docs_dir(root, mkdocs)
        return [
            Finding(claim, Severity.ERROR, f"link target does not exist: {claim.value}", self.name)
            for claim in claims
            if claim.kind is ClaimKind.LINK and _resolve(claim.value, claim.location.file, root, docs_dir) is None
        ]


class AnchorChecker:
    """Verifies that ``path#anchor`` fragments match a heading slug in the target markdown document."""

    name = "anchor"

    def check(self, claims: list[Claim], root: Path, mkdocs: MkdocsSetting = "auto") -> list[Finding]:
        findings: list[Finding] = []
        cache: dict[Path, list[str] | None] = {}
        docs_dir = mkdocs_docs_dir(root, mkdocs)
        for claim in claims:
            if claim.kind is not ClaimKind.ANCHOR:
                continue
            finding = self._check_one(claim, root, cache, docs_dir)
            if finding is not None:
                findings.append(finding)
        return findings

    def _check_one(
        self, claim: Claim, root: Path, cache: dict[Path, list[str] | None], docs_dir: Path | None
    ) -> Finding | None:
        file_part, _, anchor = claim.value.partition("#")
        target = claim.location.file if not file_part else _resolve(file_part, claim.location.file, root, docs_dir)
        # A missing file is the LinkChecker's finding; non-markdown targets cannot be verified.
        if target is None or not target.is_file() or target.suffix.lower() != ".md":
            return None
        if target not in cache:
            cache[target] = self._slugs(target)
        slugs = cache[target]
        if slugs is None or anchor.lower() in slugs:
            return None
        return Finding(claim, Severity.ERROR, self._message(claim.value, slugs), self.name)

    @staticmethod
    def _slugs(target: Path) -> list[str] | None:
        try:
            doc = parse_markdown(target, target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return None
        return heading_slugs([h.text for h in doc.headings])

    @staticmethod
    def _message(value: str, slugs: list[str]) -> str:
        anchor = value.partition("#")[2].lower()
        suggestions = difflib.get_close_matches(anchor, slugs, n=MAX_SUGGESTIONS, cutoff=0.4)
        suggestions += [s for s in slugs if s not in suggestions][: MAX_SUGGESTIONS - len(suggestions)]
        message = f"anchor does not match any heading: {value}"
        if suggestions:
            message += f" (available: {', '.join('#' + s for s in suggestions)})"
        return message


KNOWN_TOOLS = frozenset("""
git docker make curl wget pip pip3 pipx npm npx node python python3 uvicorn pytest uv uvx hatch pybabel
gunicorn daphne hypercorn pnpm yarn bun deno conda mamba poetry pdm tox nox ruff mypy black flake8 isort
gcloud firebase aws az terraform ansible helm kubectl minikube kind brew apt apt-get dpkg yum dnf apk
snap cargo rustc rustup go java javac mvn gradle dotnet ruby gem bundle rake rails php composer
where which gedit vim nano code open xdg-open deactivate briefcase django-admin flask celery alembic
sphinx-build mkdocs jupyter ipython virtualenv pyenv nvm tsc webpack vite next ng gatsby cmake gcc g++
clang ninja ssh scp rsync tar unzip zip sudo chmod chown mkdir cp mv rm ls cat grep sed awk find tree
touch head tail less sort uniq wc diff ps kill systemctl journalctl service ping netstat lsof env
printenv gh pre-commit twine build sh bash zsh powershell pwsh cmd start
""".split())
INSTALL_MARKERS = (
    "pip install", "npm install", "uv add", "brew install", "cargo install", "apt install",
)  # fmt: skip
GENERATED_BIN_DIRS = (".venv/", "venv/", "env/", "node_modules/")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


_REQUIREMENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


def _declared_names(root: Path) -> tuple[frozenset[str], frozenset[str]]:
    """Console-script names and dependency names declared in ``pyproject.toml`` (read statically)."""
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8")).get("project", {})
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return frozenset(), frozenset()
    requirements = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        requirements.extend(extra)
    names = {m.group(0).lower().replace("-", "_") for r in requirements if (m := _REQUIREMENT_NAME.match(r))}
    return frozenset(project.get("scripts", {})), frozenset(names)


def _is_python(token: str) -> bool:
    return token in ("python", "python3") or token.endswith("/python")


def _python_module(claim: Claim) -> str | None:
    """The MODULE in ``python -m MODULE`` taken from the claim's source line, if present."""
    try:
        tokens = shlex.split(claim.context)
    except ValueError:
        tokens = claim.context.split()
    tokens = [t for t in tokens if not _ASSIGNMENT.match(t)]
    if len(tokens) >= 3 and tokens[0] == claim.value and tokens[1] == "-m":
        return tokens[2]
    return None


def _module_in_repo(module: str, root: Path) -> bool:
    rel = Path(*module.split("."))
    for base in (root, root / "src"):
        if not is_within(base / rel, root):
            continue
        if (base / rel).with_suffix(".py").is_file() or (base / rel / "__init__.py").is_file():
            return True
    return False


def _module_known(module: str, root: Path) -> bool:
    """Repo module, or a stdlib / well-known tool module (resolved statically, never imported)."""
    top = module.split(".")[0]
    return (
        _module_in_repo(module, root)
        or top in sys.stdlib_module_names
        or top in KNOWN_TOOLS
        or top.lower() in _declared_names(root)[1]
    )


def _entry_point_names(text: str) -> set[str]:
    """Names from a ``console_scripts`` block of ``setup.cfg`` (``name = module:func`` lines)."""
    names: set[str] = set()
    in_block = False
    for line in text.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            in_block = line.strip().startswith("console_scripts")
            continue
        if in_block and "=" in line:
            names.add(line.split("=", 1)[0].strip())
    return names


def _package_json_bin(root: Path) -> set[str]:
    try:
        data = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return set()
    if not isinstance(data, dict):
        return set()
    bin_field = data.get("bin")
    if isinstance(bin_field, str) and isinstance(data.get("name"), str):
        return {data["name"].rsplit("/", 1)[-1]}
    return {str(k) for k in bin_field} if isinstance(bin_field, dict) else set()


def _defined_commands(root: Path) -> frozenset[str]:
    """Commands the repo itself defines: pyproject/setup.cfg console scripts and package.json ``bin``."""
    names = set(_declared_names(root)[0]) | _package_json_bin(root)
    try:
        pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        pyproject = {}
    names.update(pyproject.get("project", {}).get("gui-scripts", {}))
    names.update(pyproject.get("tool", {}).get("poetry", {}).get("scripts", {}))
    try:
        names |= _entry_point_names((root / "setup.cfg").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        pass
    return frozenset(names)


def _is_repo_path(token: str) -> bool:
    """True when ``token`` is a relative path that should resolve under the repo."""
    if token.startswith(("./", "../")):
        return True
    if "/" not in token or token.startswith(("/", "~")):
        return False
    return not token.startswith(GENERATED_BIN_DIRS)


def _fence_lines_above(claim: Claim, cache: dict[Path, list[str]]) -> list[str]:
    """Source lines above the claim in the same code fence (nearest first, up to the opening marker)."""
    file = claim.location.file
    if file not in cache:
        try:
            cache[file] = file.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            cache[file] = []
    above: list[str] = []
    for line in reversed(cache[file][: max(claim.location.line - 1, 0)]):
        if line.lstrip().startswith(("```", "~~~")):
            break
        above.append(line)
    return above


class CommandChecker:
    """Statically verifies documented shell commands; nothing is ever executed.

    Only repo-local-looking tokens that are missing are errors; bare unknown tokens are at most INFO.
    """

    name = "command"

    def check(self, claims: list[Claim], root: Path) -> list[Finding]:
        findings: list[Finding] = []
        defined = _defined_commands(root)
        cache: dict[Path, list[str]] = {}
        for claim in claims:
            if claim.kind is not ClaimKind.COMMAND:
                continue
            finding = self._check_one(claim, root, defined, cache)
            if finding is not None:
                findings.append(finding)
        return findings

    def _check_one(
        self, claim: Claim, root: Path, defined: frozenset[str], cache: dict[Path, list[str]]
    ) -> Finding | None:
        token = claim.value
        if _is_python(token):
            module = _python_module(claim)
            if module is not None and not _module_known(module, root):
                return Finding(claim, Severity.ERROR, f"documented python module not found: {module}", self.name)
            return None
        if (is_within(root / token, root) and (root / token).exists()) or token in defined:
            return None
        if _is_repo_path(token):
            return self._missing(claim)
        if shutil.which(token) is not None or token in KNOWN_TOOLS:
            return None
        if defined:
            return self._missing(claim)
        if any(marker in line for line in _fence_lines_above(claim, cache) for marker in INSTALL_MARKERS):
            return None
        message = "command not found in repo or on PATH (external tool?)"
        return Finding(claim, Severity.INFO, message, self.name)

    def _missing(self, claim: Claim) -> Finding:
        return Finding(claim, Severity.ERROR, f"documented command not found: {claim.value}", self.name)


_ENV_WORD = re.compile(r"(?<![A-Za-z0-9_])[A-Z][A-Z0-9_]{2,}(?![A-Za-z0-9_])")


def _mentioned_words(docs: Sequence[Path]) -> set[str]:
    """Every upper-case word in the raw text of ``docs`` (a looser test than the claim heuristics)."""
    words: set[str] = set()
    for doc in docs:
        try:
            words.update(_ENV_WORD.findall(doc.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            continue
    return words


# Variables the OS, shell or stdlib/HTTP libraries read on their own; docs mention them without the code reading them.
AMBIENT_ENV = frozenset({
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "PATH", "HOME", "USER", "SHELL", "TMPDIR",
    "TEMP", "TMP", "LANG", "LC_ALL", "TZ", "TERM", "SSLKEYLOGFILE", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "PYTHONPATH", "VIRTUAL_ENV", "CI", "EDITOR", "PAGER",
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
})  # fmt: skip


class EnvChecker:
    """Bidirectional env var check between documented ``ENV_VAR`` claims and what the code reads.

    The code-read set is built lazily from ``root`` on each call. ``docs`` is the scanned doc corpus;
    without it, only the claims count as documentation and no undocumented warnings are raised.
    """

    name = "env"

    def check(
        self,
        claims: list[Claim],
        root: Path,
        docs: Sequence[Path] = (),
        ambient_extra: Collection[str] = (),
    ) -> list[Finding]:
        reads = scan_code_env_reads(root)
        ambient = AMBIENT_ENV | frozenset(ambient_extra)
        env_claims = [c for c in claims if c.kind is ClaimKind.ENV_VAR]
        findings = [self._unread(c, ambient) for c in env_claims if c.value not in reads]
        if docs:
            findings.extend(self._undocumented(reads - ambient, env_claims, root, docs))
        return findings

    def _unread(self, claim: Claim, ambient: frozenset[str]) -> Finding:
        if claim.value in ambient:
            message = f"ambient environment variable (read by the OS/stdlib, not this codebase): {claim.value}"
            return Finding(claim, Severity.INFO, message, self.name)
        return Finding(claim, Severity.ERROR, f"documented env var is not read by the code: {claim.value}", self.name)

    def _undocumented(
        self, reads: set[str], claims: list[Claim], root: Path, docs: Sequence[Path]
    ) -> list[Finding]:
        mentioned = _mentioned_words(docs) | {c.value for c in claims}
        readme = root / "README.md"
        anchor_file = readme if readme.is_file() else docs[0]
        return [
            Finding(
                Claim(ClaimKind.ENV_VAR, name, Location(file=anchor_file, line=1), context=""),
                Severity.WARNING,
                f"env var read by code but undocumented: {name}",
                self.name,
            )
            for name in sorted(reads - mentioned)
        ]


CHECKERS: dict[str, Checker] = {
    PathChecker.name: PathChecker(),
    LinkChecker.name: LinkChecker(),
    AnchorChecker.name: AnchorChecker(),
    CommandChecker.name: CommandChecker(),
    EnvChecker.name: EnvChecker(),
}
