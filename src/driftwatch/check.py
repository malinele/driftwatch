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
import unicodedata
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from driftwatch.envscan import scan_auto_envvar_prefixes, scan_code_env_reads
from driftwatch.extract import extract_myst_labels
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


def paths_by_basename(root: Path) -> dict[str, list[str]]:
    """Map each file basename under ``root`` to every root-relative POSIX path with it (sorted walk)."""
    index: dict[str, list[str]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in _INDEX_SKIP_DIRS)
        for filename in sorted(filenames):
            index.setdefault(filename, []).append((Path(dirpath) / filename).relative_to(root).as_posix())
    return index


def _basename_index(root: Path) -> dict[str, str]:
    """Map each file basename under ``root`` to its first root-relative POSIX path (sorted walk)."""
    return {name: paths[0] for name, paths in paths_by_basename(root).items()}


MIN_SUFFIX_SEGMENTS = 2


def find_by_suffix(value: str, index: Mapping[str, Sequence[str]]) -> str | None:
    """First indexed path ending in the full relative suffix of ``value`` (leading ``./`` / ``../`` dropped).

    Only suffixes with a directory component count, so a bare name never matches on its basename alone.
    """
    if not value or value.startswith(("/", "~")) or "://" in value:
        return None
    parts = [p for p in posixpath.normpath(value).split("/") if p not in ("", ".")]
    while parts and parts[0] == "..":
        parts = parts[1:]
    if len(parts) < MIN_SUFFIX_SEGMENTS or ".." in parts:
        return None
    suffix = "/".join(parts)
    return next((p for p in index.get(parts[-1], ()) if p == suffix or p.endswith(f"/{suffix}")), None)


_AUTHORING = re.compile(
    r"create a file|save it as|name it|add a file|new file called|\bcalled\b|\bnamed\b|for example|e\.g\.|"
    r"\byour |a file like",
    re.IGNORECASE,
)
AUTHORING_LOOKBACK = 2
ECOSYSTEM_FILES = frozenset({
    "requirements.txt", "setup.py", "pyproject.toml", "package.json", "mypy.ini", "serverless.yml",
    "llms.txt", "index.html", "404.html", "openapi.json", "docker-compose.yml", "Dockerfile", "Makefile",
    ".env", "mkdocs.yml", "mkdocs.yaml", "swagger-ui.css", "swagger-ui-bundle.js", "redoc.standalone.js",
    "favicon.ico", "search_index.json",
})  # fmt: skip
WORKFLOWS_DIR = ".github/workflows"
_WORKFLOW_SUFFIXES = (".yml", ".yaml")
GENERATED_DIRS = ("site", "build", "dist", "docs/build")


def _read_lines(file: Path) -> list[str]:
    try:
        return file.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []


def is_authoring_instruction(claim: Claim) -> bool:
    """True if the claim's line, or either of the two document lines before it, reads like authoring prose."""
    if _AUTHORING.search(claim.context):
        return True
    lines = _read_lines(claim.location.file)
    end = claim.location.line - 1
    window = lines[max(0, end - AUTHORING_LOOKBACK) : end]
    return any(_AUTHORING.search(line) for line in window)


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


def _workflow_location(value: str, root: Path) -> str | None:
    """Root-relative path of a bare ``*.yml`` / ``*.yaml`` name that exists under ``.github/workflows``."""
    if "/" in value or not value.lower().endswith(_WORKFLOW_SUFFIXES):
        return None
    candidate = root / WORKFLOWS_DIR / value
    return f"{WORKFLOWS_DIR}/{value}" if candidate.is_file() else None


def _parent_exists(claim: Claim, root: Path) -> bool:
    """True if the claim has a directory component and that parent exists under the root or the doc's directory."""
    parent = Path(claim.value.rstrip("/")).parent
    if parent == Path("."):
        return False
    candidates = [root / parent, claim.location.file.parent / parent]
    return any(is_within(c, root) and c.is_dir() for c in candidates)


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
                finding = self._with_fallbacks(finding, index, root)
            if finding is not None:
                findings.append(finding)
        return findings

    def _with_fallbacks(self, finding: Finding, index: dict[str, str], root: Path) -> Finding:
        """Grade a missing-path ERROR: placeholder / found-elsewhere / ecosystem file / severity ladder.

        ERROR survives only when the claim has a directory component and its parent directory exists,
        i.e. the doc points into a real tree. Anything else is at most a WARNING.
        """
        claim = finding.claim
        basename = Path(claim.value.rstrip("/")).name
        if Path(basename).stem.lower() in PLACEHOLDER_STEMS:
            return Finding(claim, Severity.WARNING, "placeholder path in docs", self.name)
        found = _workflow_location(claim.value, root) or index.get(basename)
        if found is not None:
            message = f"path does not exist at the stated location; found at: {found}"
            return Finding(claim, Severity.WARNING, message, self.name)
        if basename in ECOSYSTEM_FILES:
            return Finding(claim, Severity.INFO, "common ecosystem file, not present in this repo", self.name)
        if _parent_exists(claim, root):
            return finding
        message = f"path does not exist and its parent directory is not in the repo: {claim.value}"
        return Finding(claim, Severity.WARNING, message, self.name)

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
_ATTR_LIST = re.compile(r"\s*\{:?[^{}]*\}\s*$")
_HTML_TAG = re.compile(r"</?[A-Za-z][^<>]*>")
_HTML_ENTITY = re.compile(r"&(?:#\d+|#x[0-9A-Fa-f]+|\w+);")
_EMPHASIS = re.compile(r"\*+|(?<!\w)_+|_+(?!\w)")


def slugify(text: str) -> str:
    """GitHub-style heading slug of the rendered text.

    Drops a trailing attr_list, HTML tags and entities, and emphasis markers (``*``, and ``_`` at word
    edges, so ``snake_case`` survives); lowercases, drops punctuation (apostrophes included, hyphens
    kept) and turns spaces into hyphens.
    """
    text = _ATTR_LIST.sub("", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _HTML_ENTITY.sub("", _HTML_TAG.sub("", text))
    text = _EMPHASIS.sub("", text).strip().lower()
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


def fold_diacritics(text: str) -> str:
    """``text`` with accents stripped (NFKD, combining marks dropped), as VitePress slugs do."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


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


def _site_roots(doc_file: Path, root: Path, docs_dir: Path | None) -> list[Path]:
    """Candidate site roots for a ``/``-absolute link: scan root, docs dir, top of the document's tree."""
    roots = [root, docs_dir if docs_dir is not None else root / DEFAULT_DOCS_DIR]
    try:
        parts = Path(os.path.abspath(doc_file)).relative_to(os.path.abspath(root)).parts
    except ValueError:
        parts = ()
    if len(parts) > 1:
        roots.append(root / parts[0])
    return list(dict.fromkeys(roots))


def _resolve(file_part: str, doc_file: Path, root: Path, docs_dir: Path | None = None) -> Path | None:
    """Resolve a link target: mkdocs URL semantics first (if on), then doc-relative, then repo root.

    A leading ``/`` is site-absolute: tried against the scan root, the docs dir, then the docs tree top.
    """
    if docs_dir is not None:
        found = _mkdocs_resolve(file_part, doc_file, root, docs_dir)
        if found is not None:
            return found
    if file_part.startswith("/"):
        relative = file_part.lstrip("/")
        bases = [site_root / relative for site_root in _site_roots(doc_file, root, docs_dir)]
    else:
        bases = [doc_file.parent / file_part, root / file_part]
    candidates = [c for base in bases for c in _with_conventions(base, file_part)]
    return next((c for c in candidates if is_within(c, root) and c.exists()), None)


def _with_conventions(base: Path, file_part: str) -> list[Path]:
    """``base`` plus, for extensionless targets, the mkdocs-style ``.md`` / ``index.md`` / ``README.md`` forms."""
    if Path(file_part.rstrip("/")).suffix:
        return [base]
    return [base, base.with_name(base.name + ".md"), base / "index.md", base / "README.md"]


_PLACEHOLDER_TARGET = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+")
SCRIPTS_DIR = "scripts"


def _is_template_placeholder(claim: Claim, root: Path) -> bool:
    """ALLCAPS_WITH_UNDERSCORES link targets, or a bare ``url`` in a file under ``scripts/``."""
    if _PLACEHOLDER_TARGET.fullmatch(claim.value):
        return True
    if claim.value != "url":
        return False
    base = Path(os.path.abspath(root))
    file = Path(os.path.abspath(claim.location.file))
    return file.is_relative_to(base) and SCRIPTS_DIR in file.relative_to(base).parts[:-1]


class LinkChecker:
    """Verifies that relative link and image targets exist."""

    name = "link"

    def check(
        self,
        claims: list[Claim],
        root: Path,
        mkdocs: MkdocsSetting = "auto",
        labels: frozenset[str] | None = None,
    ) -> list[Finding]:
        docs_dir = mkdocs_docs_dir(root, mkdocs)
        known = labels if labels is not None else collect_myst_labels(root)
        return [
            Finding(claim, Severity.ERROR, f"link target does not exist: {claim.value}", self.name)
            for claim in claims
            if claim.kind is ClaimKind.LINK
            and not _is_template_placeholder(claim, root)
            and claim.value not in known
            and _resolve(claim.value, claim.location.file, root, docs_dir) is None
        ]


def collect_myst_labels(root: Path) -> frozenset[str]:
    """Index MyST ``(name)=`` target labels from every markdown file under ``root``."""
    labels: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in _INDEX_SKIP_DIRS)
        for filename in filenames:
            if not filename.endswith(".md"):
                continue
            try:
                labels |= extract_myst_labels((Path(dirpath) / filename).read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
    return frozenset(labels)


DYNAMIC_ANCHOR_MESSAGE = "anchor target is plugin-generated or included content, cannot verify statically"


@dataclass(frozen=True)
class _AnchorTargets:
    slugs: list[str]  # heading slugs in document order (used for suggestions)
    ids: frozenset[str]  # every valid anchor id: slugs, attr_list ids, HTML id/name values
    folded_ids: frozenset[str]  # ``ids`` with diacritics stripped
    dynamic: bool


class AnchorChecker:
    """Verifies that ``path#anchor`` fragments match a heading slug in the target markdown document."""

    name = "anchor"

    def check(self, claims: list[Claim], root: Path, mkdocs: MkdocsSetting = "auto") -> list[Finding]:
        findings: list[Finding] = []
        cache: dict[Path, _AnchorTargets | None] = {}
        docs_dir = mkdocs_docs_dir(root, mkdocs)
        for claim in claims:
            if claim.kind is not ClaimKind.ANCHOR:
                continue
            finding = self._check_one(claim, root, cache, docs_dir)
            if finding is not None:
                findings.append(finding)
        return findings

    def _check_one(
        self, claim: Claim, root: Path, cache: dict[Path, _AnchorTargets | None], docs_dir: Path | None
    ) -> Finding | None:
        file_part, _, anchor = claim.value.partition("#")
        target = claim.location.file if not file_part else _resolve(file_part, claim.location.file, root, docs_dir)
        # A missing file is the LinkChecker's finding; non-markdown targets cannot be verified.
        if target is None or not target.is_file() or target.suffix.lower() != ".md":
            return None
        if target not in cache:
            cache[target] = self._targets(target)
        targets = cache[target]
        if targets is None or anchor in targets.ids or anchor.lower() in targets.ids:
            return None
        if fold_diacritics(anchor.lower()) in targets.folded_ids:
            return None
        if targets.dynamic:
            return Finding(claim, Severity.INFO, DYNAMIC_ANCHOR_MESSAGE, self.name)
        return Finding(claim, Severity.ERROR, self._message(claim.value, targets.slugs), self.name)

    @staticmethod
    def _targets(target: Path) -> _AnchorTargets | None:
        try:
            doc = parse_markdown(target, target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return None
        slugs = heading_slugs([h.text for h in doc.headings])
        ids = frozenset(slugs) | frozenset(doc.anchor_ids)
        folded = frozenset(fold_diacritics(i.lower()) for i in ids)
        return _AnchorTargets(slugs=slugs, ids=ids, folded_ids=folded, dynamic=doc.dynamic)

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
DEFINITION_MARKERS = (
    "pip install", "console_scripts", "[project.scripts]", "entry_points", "add_typer", "Typer(",
)  # fmt: skip
DEFINITION_WINDOW = 3
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


def _entry_point_names(text: str) -> dict[str, str]:
    """``name -> module:func`` from a ``console_scripts`` block of ``setup.cfg``."""
    names: dict[str, str] = {}
    in_block = False
    for line in text.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            in_block = line.strip().startswith("console_scripts")
            continue
        if in_block and "=" in line:
            name, target = line.split("=", 1)
            names[name.strip()] = target.strip()
    return names


def _package_json_bin(root: Path) -> dict[str, tuple[str, str]]:
    """``name -> ("file", relative path)`` for the ``bin`` field of package.json."""
    try:
        data = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    bin_field = data.get("bin")
    if isinstance(bin_field, str) and isinstance(data.get("name"), str):
        return {data["name"].rsplit("/", 1)[-1]: ("file", bin_field)}
    if isinstance(bin_field, dict):
        return {str(k): ("file", str(v)) for k, v in bin_field.items()}
    return {}


def _defined_command_targets(root: Path) -> dict[str, tuple[str, str]]:
    """Commands the repo defines, as ``name -> (kind, target)``: ``module:func`` entry points or bin files."""
    targets: dict[str, tuple[str, str]] = dict(_package_json_bin(root))
    try:
        pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        pyproject = {}
    sections = (
        pyproject.get("project", {}).get("scripts", {}),
        pyproject.get("project", {}).get("gui-scripts", {}),
        pyproject.get("tool", {}).get("poetry", {}).get("scripts", {}),
    )
    for section in sections:
        for name, target in section.items():
            targets[str(name)] = ("entry", target if isinstance(target, str) else "")
    try:
        setup_cfg = _entry_point_names((root / "setup.cfg").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        setup_cfg = {}
    targets.update({name: ("entry", target) for name, target in setup_cfg.items()})
    return targets


def _target_missing(kind: str, target: str, root: Path) -> bool:
    """True when a repo-defined command points at a module or file that is not in the repo."""
    if not target:
        return False
    if kind == "file":
        path = root / target
        return is_within(path, root) and not path.exists()
    module = target.split(":", 1)[0].strip()
    return bool(module) and not _module_in_repo(module, root)


def _doc_lines(file: Path, cache: dict[Path, list[str]]) -> list[str]:
    if file not in cache:
        try:
            cache[file] = file.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            cache[file] = []
    return cache[file]


def _fence_lines_above(claim: Claim, cache: dict[Path, list[str]]) -> list[str]:
    """Source lines above the claim in the same code fence (nearest first, up to the opening marker)."""
    above: list[str] = []
    for line in reversed(_doc_lines(claim.location.file, cache)[: max(claim.location.line - 1, 0)]):
        if line.lstrip().startswith(("```", "~~~")):
            break
        above.append(line)
    return above


def _normalize_name(text: str) -> str:
    return text.lower().replace("_", "-")


def _doc_defines(claim: Claim, cache: dict[Path, list[str]]) -> bool:
    """True when the token sits next to an install or entry-point marker anywhere in its own document."""
    lines = _doc_lines(claim.location.file, cache)
    name = re.escape(_normalize_name(claim.value.rsplit("/", 1)[-1]))
    word = re.compile(rf"(?<![\w-]){name}(?![\w-])")
    for index, line in enumerate(lines):
        if not any(marker in line for marker in DEFINITION_MARKERS):
            continue
        window = lines[max(index - DEFINITION_WINDOW, 0) : index + DEFINITION_WINDOW + 1]
        if any(word.search(_normalize_name(near)) for near in window):
            return True
    return False


PROJECT_SCRIPTS_MARKER = "[project.scripts]"
_FENCE_OPENER = ("```", "~~~")
_PIP_INSTALL = re.compile(r"\bpip3?\s+install\b(.*)")


def _doc_packages_module(claim: Claim, module: str, cache: dict[Path, list[str]]) -> bool:
    """True when the claim's document packages code itself: ``[project.scripts]``, a fence titled
    ``pyproject.toml``, or a ``pip install`` of a name matching the module's top-level package."""
    top = _normalize_name(module.split(".")[0])
    for line in _doc_lines(claim.location.file, cache):
        if PROJECT_SCRIPTS_MARKER in line:
            return True
        if line.lstrip().startswith(_FENCE_OPENER) and "pyproject.toml" in line:
            return True
        match = _PIP_INSTALL.search(line)
        if match and top in _requirement_names(match.group(1)):
            return True
    return False


def _requirement_names(args: str) -> set[str]:
    """Normalized distribution names in ``pip install`` arguments (extras and version pins dropped)."""
    stripped = (arg.strip("'\"`") for arg in args.split())
    return {_normalize_name(m.group(0)) for arg in stripped if (m := _REQUIREMENT_NAME.match(arg))}


def _missing_local_path(token: str, root: Path) -> bool:
    """A missing ``./`` or ``../`` path whose parent directory exists below the repo root."""
    if not token.startswith(("./", "../")):
        return False
    parent = (root / token).parent
    return is_within(parent, root) and parent.resolve() != root.resolve() and parent.is_dir()


class CommandChecker:
    """Statically verifies documented shell commands; nothing is ever executed.

    ERROR is reserved for repo-defined commands whose target is missing and for ``./`` / ``../``
    paths that do not exist below an existing directory; everything unknown is at most INFO.
    """

    name = "command"

    def check(self, claims: list[Claim], root: Path) -> list[Finding]:
        findings: list[Finding] = []
        defined = _defined_command_targets(root)
        cache: dict[Path, list[str]] = {}
        for claim in claims:
            if claim.kind is not ClaimKind.COMMAND:
                continue
            finding = self._check_one(claim, root, defined, cache)
            if finding is not None:
                findings.append(finding)
        return findings

    def _check_one(
        self, claim: Claim, root: Path, defined: dict[str, tuple[str, str]], cache: dict[Path, list[str]]
    ) -> Finding | None:
        token = claim.value
        if _is_python(token):
            module = _python_module(claim)
            if module is None or _module_known(module, root):
                return None
            if _doc_packages_module(claim, module, cache):
                return Finding(claim, Severity.INFO, f"module built by this tutorial: {module}", self.name)
            return Finding(claim, Severity.ERROR, f"documented python module not found: {module}", self.name)
        if is_within(root / token, root) and (root / token).exists():
            return None
        if token in defined:
            kind, target = defined[token]
            return self._missing(claim) if _target_missing(kind, target, root) else None
        if _missing_local_path(token, root):
            return self._missing(claim)
        if shutil.which(token) is not None or token in KNOWN_TOOLS:
            return None
        if any(marker in line for line in _fence_lines_above(claim, cache) for marker in INSTALL_MARKERS):
            return None
        if _doc_defines(claim, cache):
            return None
        message = "command not found in repo or on PATH (external tool?)"
        return Finding(claim, Severity.INFO, message, self.name)

    def _missing(self, claim: Claim) -> Finding:
        return Finding(claim, Severity.ERROR, f"documented command not found: {claim.value}", self.name)


_ENV_DEFINITION = re.compile(r"^[ \t]*(?:-[ \t]+)?(?:\$ )?(?:export\s+)?([A-Z][A-Z0-9_]{2,})=")
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
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "PYTHONUTF8", "PYTHONIOENCODING", "PYTHONDONTWRITEBYTECODE",
    "PYTHONWARNINGS", "NODE_OPTIONS", "NODE_ENV", "WATCHFILES_FORCE_POLLING", "FORCE_COLOR", "NO_COLOR",
    "DEBIAN_FRONTEND",
})  # fmt: skip
# Name prefixes of variables that external tools read (editors, package managers, cloud SDKs, CI hosts).
TOOL_ENV_PREFIXES = (
    "GEDIT_", "UV_", "GOOGLE_", "AWS_", "AZURE_", "GCP_", "NPM_", "YARN_", "PNPM_", "CARGO_", "PIP_",
    "DOCKER_", "COMPOSE_", "KUBE", "K8S_", "GH_", "GITLAB_", "VSCODE_", "JAVA_", "GRADLE_", "MAVEN_",
)  # fmt: skip


_EXPORT_DEFINITION = re.compile(r"\bexport\s+([A-Z][A-Z0-9_]{2,})=")
_DOTENV_ASSIGNMENT = re.compile(r"^[ \t]*(?:export\s+)?([A-Z][A-Z0-9_]{2,})[ \t]*=")
_DOTENV_OPENER = re.compile(r"(?<![\w.])\.env(?![\w])")
_DOTENV_LANGUAGES = frozenset({"env", "dotenv", ".env"})


@dataclass(frozen=True)
class _DocDefinitions:
    """Variables a document defines (``dotenv``: inside a ``.env``-style fence) and the lines naming each."""

    defined: frozenset[str]
    dotenv: frozenset[str]
    mentions: dict[str, frozenset[int]]

    def covers(self, claim: Claim) -> bool:
        """A ``.env`` definition covers every line; any other one covers the whole document once the
        variable is named on a second line (a lone ``export X=1`` is the claim itself)."""
        if claim.value in self.dotenv:
            return True
        return claim.value in self.defined and len(self.mentions.get(claim.value, frozenset())) > 1


def _is_dotenv_fence(language: str, opener: str) -> bool:
    return language.lower() in _DOTENV_LANGUAGES or _DOTENV_OPENER.search(opener) is not None


def _dotenv_fence_names(doc: Path, text: str) -> set[str]:
    """Names assigned in the document's ``.env``-style fences (language env/dotenv or ``.env`` in the opener)."""
    source = text.splitlines()
    names: set[str] = set()
    for fence in parse_markdown(doc, text).code_fences:
        opener = source[fence.start_line - 1] if 0 < fence.start_line <= len(source) else ""
        if not _is_dotenv_fence(fence.language, opener):
            continue
        names.update(m.group(1) for line in fence.content.splitlines() if (m := _DOTENV_ASSIGNMENT.match(line)))
    return names


def _user_defined(doc: Path) -> _DocDefinitions:
    """Where the document defines each variable: ``VAR=value`` at line start (``.env`` style, optionally as a
    ``- `` list item or behind ``export``), ``export VAR=`` anywhere on a line, or any assignment in a
    ``.env``-style fence."""
    try:
        text = doc.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return _DocDefinitions(frozenset(), frozenset(), {})
    defined: set[str] = set()
    mentions: dict[str, set[int]] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        defined.update(m.group(1) for m in _EXPORT_DEFINITION.finditer(line))
        match = _ENV_DEFINITION.match(line)
        if match:
            defined.add(match.group(1))
        for word in _ENV_WORD.findall(line):
            mentions.setdefault(word, set()).add(number)
    dotenv = frozenset(_dotenv_fence_names(doc, text))
    return _DocDefinitions(
        frozenset(defined) | dotenv,
        dotenv,
        {name: frozenset(mentions.get(name, set())) for name in defined},
    )


def _defined_in_document(claim: Claim, cache: dict[Path, _DocDefinitions]) -> bool:
    """True when the claim's document defines the variable, which then holds for the whole document.

    A definition in a ``.env``-style fence covers every mention, its own line included. Any other
    definition does too once the name appears on a second line; a lone ``export X=1`` that nothing else
    in the document mentions is the claim itself and is still checked.
    """
    file = claim.location.file
    if file not in cache:
        cache[file] = _user_defined(file)
    return cache[file].covers(claim)


def _set_inline_for_others(claim: Claim) -> bool:
    """True for ``VAR=x cmd``: the variable is set for another program's environment."""
    pattern = r"(?<![A-Za-z0-9_])" + re.escape(claim.value) + r"=[^\s`]*\s+[A-Za-z./~]"
    return re.search(pattern, claim.context) is not None


def _ci_text(root: Path) -> str:
    texts: list[str] = []
    for workflow in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        try:
            texts.append(workflow.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
    return "\n".join(texts)


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
        tool_prefixes_extra: Collection[str] = (),
    ) -> list[Finding]:
        reads = scan_code_env_reads(root)
        ambient = AMBIENT_ENV | frozenset(ambient_extra)
        tools = (*TOOL_ENV_PREFIXES, *tool_prefixes_extra)
        env_claims = [c for c in claims if c.kind is ClaimKind.ENV_VAR]
        prefixes = tuple(p + "_" for p in scan_auto_envvar_prefixes(root))
        ci_words = frozenset(_ENV_WORD.findall(_ci_text(root)))
        defined: dict[Path, _DocDefinitions] = {}
        findings = [
            self._unread(c, ambient, tools)
            for c in env_claims
            if c.value not in reads
            and (
                c.value in ambient  # still reported as INFO
                or not (
                    c.value.startswith(prefixes)
                    or c.value in ci_words
                    or _set_inline_for_others(c)
                    or _defined_in_document(c, defined)
                )
            )
        ]
        if docs:
            findings.extend(self._undocumented(reads - ambient, env_claims, root, docs))
        return findings

    def _unread(self, claim: Claim, ambient: frozenset[str], tools: tuple[str, ...]) -> Finding:
        if claim.value in ambient:
            message = f"ambient environment variable (read by the OS/stdlib, not this codebase): {claim.value}"
            return Finding(claim, Severity.INFO, message, self.name)
        if claim.value.startswith(tools):
            message = f"variable of an external tool (not read by this codebase): {claim.value}"
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
