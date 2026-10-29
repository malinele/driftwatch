"""Discover markdown files, extract claims and run the registered checkers."""

from __future__ import annotations

import fnmatch
import os
import posixpath
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import unquote

from driftwatch.check import CHECKERS, AnchorChecker, EnvChecker, LinkChecker, is_within
from driftwatch.config import Config
from driftwatch.extract import (
    extract_command_claims,
    extract_env_claims,
    extract_link_claims,
    extract_myst_labels,
    extract_path_claims,
)
from driftwatch.markdown import parse_markdown
from driftwatch.models import Claim, Finding, Severity

SKIP_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "dist", "__pycache__"})


def _is_skipped_dir(name: str) -> bool:
    return name in SKIP_DIRS or name.startswith(".")


def discover_markdown(paths: list[Path]) -> list[Path]:
    """Return a sorted, de-duplicated list of ``*.md`` files under ``paths``."""
    found: set[Path] = set()
    for path in paths:
        if path.is_file():
            if path.suffix == ".md":
                found.add(path)
            continue
        found.update(_walk(path))
    return sorted(found)


def _walk(directory: Path) -> Iterator[Path]:
    for current, dirnames, filenames in os.walk(directory):
        dirnames[:] = [d for d in dirnames if not _is_skipped_dir(d)]
        for filename in filenames:
            if filename.endswith(".md"):
                yield Path(current) / filename


# Matches `<!-- driftwatch: ignore -->` and `<!-- driftwatch: ignore=a,b -->`.
_IGNORE_COMMENT = re.compile(r"<!--\s*driftwatch:\s*ignore(?:\s*=\s*([\w,\s-]+?))?\s*-->")

# line number -> checker names to suppress (``None`` means every checker)
IgnoreMap = dict[int, frozenset[str] | None]


def _merge_scope(current: frozenset[str] | None, new: frozenset[str] | None) -> frozenset[str] | None:
    if current is None or new is None:
        return None
    return current | new


def parse_ignores(text: str) -> IgnoreMap:
    """Map each line covered by an ignore comment (its own and the next) to the checkers it silences."""
    ignores: IgnoreMap = {}
    for number, line in enumerate(text.splitlines(), start=1):
        for match in _IGNORE_COMMENT.finditer(line):
            names = match.group(1)
            scope = frozenset(n.strip() for n in names.split(",") if n.strip()) if names else None
            for covered in (number, number + 1):
                ignores[covered] = _merge_scope(ignores[covered], scope) if covered in ignores else scope
    return ignores


def _is_ignored(finding: Finding, ignores: dict[Path, IgnoreMap]) -> bool:
    location = finding.claim.location
    file_ignores = ignores.get(location.file)
    if file_ignores is None or location.line not in file_ignores:
        return False
    scope = file_ignores[location.line]
    return scope is None or finding.checker in scope


def is_excluded(file: Path, root: Path, patterns: tuple[str, ...]) -> bool:
    try:
        relative = file.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        relative = file.as_posix()
    return any(fnmatch.fnmatch(relative, p) or fnmatch.fnmatch(file.name, p) for p in patterns)


# Changelog-type files describe the past, so only their links and anchors are checked.
HISTORICAL_PATTERNS = ("CHANGELOG*.md", "HISTORY*.md", "CHANGES*.md", "RELEASES*.md", "docs/release-notes*.md")
HISTORICAL_CHECKERS = frozenset({"link", "anchor"})


def _relative_posix(file: Path, root: Path) -> str:
    try:
        return file.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return file.as_posix()


def is_historical(file: Path, root: Path, extra: tuple[str, ...] = ()) -> bool:
    """True for changelog-type files (built-in patterns plus ``extra``), matched case-insensitively."""
    relative = _relative_posix(file, root).lower()
    name = file.name.lower()
    for pattern in (*HISTORICAL_PATTERNS, *extra):
        lowered = pattern.lower()
        if "/" in lowered:
            if fnmatch.fnmatchcase(relative, lowered):
                return True
        elif fnmatch.fnmatchcase(name, lowered) or fnmatch.fnmatchcase(relative, lowered):
            return True
    return False


TEST_DIRS = frozenset({"tests", "test", "fixtures", "testdata"})


def is_test_content(file: Path, root: Path) -> bool:
    """True if ``file`` sits under a tests/test/fixtures/testdata directory inside ``root``."""
    try:
        relative = file.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return any(part in TEST_DIRS for part in relative.parts[:-1])


def _dedupe_findings(findings: Iterable[Finding]) -> list[Finding]:
    """Drop repeats of the same (file, line, checker, value, message), keeping the first."""
    seen: set[tuple[Path, int, str, str, str]] = set()
    unique: list[Finding] = []
    for finding in findings:
        loc = finding.claim.location
        key = (loc.file, loc.line, finding.checker, finding.claim.value, finding.message)
        if key not in seen:
            seen.add(key)
            unique.append(finding)
    return unique


def scan_files(root: Path, files: list[Path], config: Config | None = None) -> list[Finding]:
    cfg = config if config is not None else Config()
    files = [f for f in files if not is_excluded(f, root, cfg.exclude)]
    if not cfg.scan_tests:
        files = [f for f in files if not is_test_content(f, root)]
    findings: list[Finding] = []
    env_claims: list[Claim] = []
    ignores: dict[Path, IgnoreMap] = {}
    labels = frozenset[str]().union(
        *(extract_myst_labels(f.read_text(encoding="utf-8", errors="replace")) for f in files)
    )
    for file in files:
        text = file.read_text(encoding="utf-8", errors="replace")
        ignores[file] = parse_ignores(text)
        doc = parse_markdown(file, text)
        historical = not cfg.historical_full_scan and is_historical(file, root, cfg.historical)
        if historical:
            claims = extract_link_claims(doc)
        else:
            claims = [*extract_path_claims(doc), *extract_link_claims(doc), *extract_command_claims(doc)]
            env_claims.extend(extract_env_claims(doc))
        for name, checker in CHECKERS.items():
            if historical and name not in HISTORICAL_CHECKERS:
                continue
            if name not in cfg.enabled_checkers or isinstance(checker, EnvChecker):
                continue
            if isinstance(checker, LinkChecker):
                findings.extend(checker.check(claims, root, cfg.mkdocs, labels))
            elif isinstance(checker, AnchorChecker):
                findings.extend(checker.check(claims, root, cfg.mkdocs))
            else:
                findings.extend(checker.check(claims, root))
    # The env checker is bidirectional, so it needs every claim and the whole doc corpus at once.
    if files:
        extra = set(cfg.env_stoplist_extra)
        for name, checker in CHECKERS.items():
            if name in cfg.enabled_checkers and isinstance(checker, EnvChecker):
                checked = checker.check(
                    [c for c in env_claims if c.value not in extra], root, files, cfg.ambient_extra
                )
                findings.extend(f for f in checked if f.claim.value not in extra)
    kept = _dedupe_findings(f for f in findings if not _is_ignored(f, ignores))
    localized = dedupe_locales(kept, root) if cfg.locales else kept
    return [_mirror_readme_downgrade(f, root) for f in localized]


README_MIRROR_MESSAGE = (
    "link works on the docs site (target exists under docs/) but is broken on GitHub/PyPI: {value}"
)


def _mirror_readme_downgrade(finding: Finding, root: Path) -> Finding:
    """Root README link whose target only exists under ``docs/``: true on the site, broken on GitHub."""
    if finding.checker not in _FALLBACK_CHECKERS or finding.severity is not Severity.ERROR:
        return finding
    if _relative_posix(finding.claim.location.file, root) != "README.md":
        return finding
    value = unquote(finding.claim.value.split("#", 1)[0].split("?", 1)[0])
    if not value or value.startswith(("/", "~")) or "://" in value:
        return finding
    target = Path(posixpath.normpath((root / "docs" / value).as_posix()))
    if not is_within(target, root / "docs") or not target.exists():
        return finding
    return replace(finding, severity=Severity.WARNING, message=README_MIRROR_MESSAGE.format(value=finding.claim.value))


ISO_639_1 = frozenset(
    "aa ab ae af ak am an ar as av ay az ba be bg bh bi bm bn bo br bs ca ce ch co cr cs cu cv cy da de dv dz "
    "ee el en eo es et eu fa ff fi fj fo fr fy ga gd gl gn gu gv ha he hi ho hr ht hu hy hz ia id ie ig ii ik "
    "io is it iu ja jv ka kg ki kj kk kl km kn ko kr ks ku kv kw ky la lb lg li ln lo lt lu lv mg mh mi mk ml "
    "mn mr ms mt my na nb nd ne ng nl nn no nr nv ny oc oj om or os pa pi pl ps pt qu rm rn ro ru rw sa sc sd "
    "se sg si sk sl sm sn so sq sr ss st su sv sw ta te tg th ti tk tl tn to tr ts tt tw ty ug uk ur uz ve vi "
    "vo wa wo xh yi yo za zh zu".split()
)
_LOCALE_DIR = re.compile(r"^([a-z]{2})(?:[-_][A-Za-z]{2,4})?$")
_LOCALE_PARENTS = frozenset({"docs", "i18n", "locales"})
SOURCE_LOCALE = "en"
LOCALE_FALLBACK_MESSAGE = "missing in this locale, exists in source locale"
_FALLBACK_CHECKERS = frozenset({"path", "link"})


@dataclass(frozen=True)
class LocaleFile:
    """A file's place in a translated tree: ``prefix/<locale>/rest`` (``locale`` is None in a bare docs tree)."""

    prefix: tuple[str, ...]
    locale: str | None
    rest: tuple[str, ...]

    @property
    def stripped(self) -> tuple[str, ...]:
        return (*self.prefix, *self.rest)


def locale_of(relative: str) -> LocaleFile | None:
    """Classify a root-relative posix path as a locale copy or part of a bare ``docs/`` tree."""
    parts = tuple(relative.split("/"))
    for index in range(1, len(parts) - 1):
        match = _LOCALE_DIR.match(parts[index])
        if match and match.group(1) in ISO_639_1 and parts[index - 1] in _LOCALE_PARENTS:
            return LocaleFile(parts[:index], parts[index], parts[index + 1 :])
    for index, part in enumerate(parts[:-1]):
        if part == "docs":
            return LocaleFile(parts[: index + 1], None, parts[index + 1 :])
    return None


def _source_dir(info: LocaleFile, root: Path) -> Path | None:
    """Source-locale directory of ``info``'s tree: ``<prefix>/en`` if it exists, else a bare docs dir."""
    base = root.joinpath(*info.prefix)
    if (base / SOURCE_LOCALE).is_dir():
        return base / SOURCE_LOCALE
    return base if info.prefix[-1] == "docs" else None


def _is_source(info: LocaleFile, root: Path) -> bool:
    source = _source_dir(info, root)
    if source is None:
        return False
    if source.name == SOURCE_LOCALE and source != root.joinpath(*info.prefix):
        return info.locale == SOURCE_LOCALE
    return info.locale is None


def _target_exists_in_source(finding: Finding, info: LocaleFile, source_dir: Path) -> bool:
    value = unquote(finding.claim.value.split("#", 1)[0].split("?", 1)[0])
    if not value or value.startswith(("/", "~")) or "://" in value:
        return False
    here = source_dir.joinpath(*info.rest[:-1])
    target = Path(posixpath.normpath((here / value).as_posix()))
    candidates = (target, target.with_name(target.name + ".md"), target / "index.md", target / "README.md")
    return any(c.exists() for c in candidates)


def _translation_fallback(finding: Finding, info: LocaleFile | None, root: Path) -> Finding:
    if info is None or info.locale in (None, SOURCE_LOCALE):
        return finding
    if finding.checker not in _FALLBACK_CHECKERS or finding.severity is not Severity.ERROR:
        return finding
    source_dir = _source_dir(info, root)
    if source_dir is None or not _target_exists_in_source(finding, info, source_dir):
        return finding
    return replace(finding, severity=Severity.WARNING, message=LOCALE_FALLBACK_MESSAGE)


def dedupe_locales(findings: list[Finding], root: Path) -> list[Finding]:
    """Collapse findings repeated across translation copies and soften ones the source locale satisfies."""
    resolved = root.resolve()
    infos = [locale_of(_relative_posix(f.claim.location.file, root)) for f in findings]
    groups: dict[tuple[tuple[str, ...], int, str, str], list[int]] = defaultdict(list)
    for index, (finding, info) in enumerate(zip(findings, infos, strict=True)):
        if info is not None:
            groups[(info.stripped, finding.claim.location.line, finding.checker, finding.claim.value)].append(index)
    dropped: set[int] = set()
    summaries: dict[int, Finding] = {}
    for members in groups.values():
        copies = [i for i in members if not _is_source(_require(infos[i]), resolved)]
        if len(copies) == len(members) or not copies:
            continue
        names = sorted({str(_require(infos[i]).locale) for i in copies})
        message = f"same finding repeated in {len(copies)} locale copies ({', '.join(names)})"
        dropped.update(copies)
        summaries[copies[0]] = replace(findings[copies[0]], severity=Severity.INFO, message=message)
    result: list[Finding] = []
    for index, finding in enumerate(findings):
        if index in summaries:
            result.append(summaries[index])
        elif index not in dropped:
            result.append(_translation_fallback(finding, infos[index], resolved))
    return result


def _require(info: LocaleFile | None) -> LocaleFile:
    if info is None:
        raise ValueError("finding is not inside a locale tree")
    return info


def scan(root: Path, paths: list[Path], config: Config | None = None) -> list[Finding]:
    return scan_files(root, discover_markdown(paths), config)
