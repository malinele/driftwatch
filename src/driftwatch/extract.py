"""Extract verifiable claims from parsed markdown documents."""

from __future__ import annotations

import re
import shlex
from urllib.parse import unquote

from driftwatch.envscan import ENV_NAME, SYSTEM_ENV_VARS
from driftwatch.markdown import CodeFence, MarkdownDocument
from driftwatch.models import Claim, ClaimKind, Location

PATH_EXTENSIONS = (
    ".py", ".md", ".toml", ".yaml", ".yml", ".json", ".txt", ".sh",
    ".cfg", ".ini", ".html", ".css", ".js", ".ts", ".png", ".lock",
)  # fmt: skip
MAX_TOKEN_LENGTH = 200
TRAILING_PUNCTUATION = ".,;:!?)]}'\""
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:(//|[^/\s]*@)")
_WHITESPACE = re.compile(r"\s")
_ANY_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*:|//)")
COMMON_SOURCE_DIRS = frozenset({"docs", "src", "tests", "scripts", "app"})
_PLAUSIBLE_DIR_NAME = re.compile(r"^[a-z0-9_-]{3,}$")
MIN_DIR_SEGMENTS = 2
_LEADING_MARKERS = ("./", "../", "~/", "/")
_EXTENSION = re.compile(r"^.+\.[A-Za-z0-9]+$")
_BARE_EXTENSION = re.compile(r"^\.[a-z0-9]+$")
_MYST_TARGET = re.compile(r"^\(([^)]+)\)=\s*$")


def extract_myst_labels(text: str) -> frozenset[str]:
    """Names defined by MyST target lines such as ``(labels/stability-policy)=``."""
    matches = (_MYST_TARGET.match(line) for line in text.splitlines())
    return frozenset(m.group(1).strip() for m in matches if m is not None)


_REGEX_OR_REDIRECT = re.compile(r"[\^$|]|\d?>")
_MEDIA_TYPE = re.compile(
    r"^(application|audio|font|image|message|model|multipart|text|video)/[A-Za-z0-9.+_-]+$", re.IGNORECASE
)
_CIDR = re.compile(
    r"^\[?(\d{1,3}(\.\d{1,3}){3}|[0-9a-fA-F:.]*:[0-9a-fA-F:.]*)\]?/\d{1,3}$"
)


def _looks_like_path(token: str) -> bool:
    if not token or len(token) >= MAX_TOKEN_LENGTH:
        return False
    if _WHITESPACE.search(token) or token.startswith("-"):
        return False
    if "*" in token or "{" in token or _SCHEME.match(token):
        return False
    # String literals such as b"HTTP/1.1" are code, not paths.
    if "'" in token or '"' in token:
        return False
    if token.startswith("@") or _BARE_EXTENSION.match(token):
        return False
    if _REGEX_OR_REDIRECT.search(token) or _CIDR.match(token):
        return False
    if "/" not in token:
        return token.lower().endswith(PATH_EXTENSIONS)
    return _slash_token_is_path(token)


def _is_plausible_dir(token: str, first: str) -> bool:
    """A multi-segment trailing-slash token whose first segment looks like a directory name."""
    segments = [part for part in token.split("/") if part]
    return len(segments) >= MIN_DIR_SEGMENTS and _PLAUSIBLE_DIR_NAME.match(first) is not None


def _slash_token_is_path(token: str) -> bool:
    """Decide whether a token containing a slash is a repo path rather than MIME/owner-repo/prose."""
    first, _, rest = token.partition("/")
    if ":" in rest:
        return False
    has_marker = token.startswith(_LEADING_MARKERS)
    last = token.rstrip("/").rsplit("/", 1)[-1]
    if token.endswith("/"):
        return has_marker or first in COMMON_SOURCE_DIRS or _is_plausible_dir(token, first)
    if _EXTENSION.search(last):
        return not (_MEDIA_TYPE.match(token) and not last.lower().endswith(PATH_EXTENSIONS))
    return has_marker and not _MEDIA_TYPE.match(token)


def _source_lines(doc: MarkdownDocument) -> list[str]:
    try:
        return doc.path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []


def extract_path_claims(doc: MarkdownDocument) -> list[Claim]:
    """Return a PATH claim for every inline code span that looks like a repo path."""
    lines = _source_lines(doc)
    titles = {fence.title for fence in doc.code_fences if fence.title}
    claims: list[Claim] = []
    for code in doc.inline_codes:
        token = code.text.strip().rstrip(TRAILING_PUNCTUATION)
        if not _looks_like_path(token) or token in titles:
            continue
        context = lines[code.line - 1] if 0 < code.line <= len(lines) else ""
        claims.append(
            Claim(
                kind=ClaimKind.PATH,
                value=token,
                location=Location(file=doc.path, line=code.line),
                context=context,
            )
        )
    return claims


def _split_link_target(target: str) -> tuple[str, str] | None:
    """Split a markdown link target into ``(file, anchor)``; None for external or empty targets."""
    target = target.strip()
    if not target or _ANY_SCHEME.match(target):
        return None
    file_part, _, anchor = target.partition("#")
    file_part = unquote(file_part.partition("?")[0])
    return file_part, unquote(anchor)


def extract_link_claims(doc: MarkdownDocument) -> list[Claim]:
    """Return LINK claims for relative file targets and ANCHOR claims (``path#anchor``) for fragments.

    External links (http/https/mailto and any other scheme) are out of v1 scope and skipped.
    """
    lines = _source_lines(doc)
    claims: list[Claim] = []
    for link in doc.links:
        parts = _split_link_target(link.target)
        if parts is None:
            continue
        file_part, anchor = parts
        location = Location(file=doc.path, line=link.line)
        context = lines[link.line - 1] if 0 < link.line <= len(lines) else ""
        if file_part:
            claims.append(Claim(kind=ClaimKind.LINK, value=file_part, location=location, context=context))
        if anchor:
            claims.append(
                Claim(kind=ClaimKind.ANCHOR, value=f"{file_part}#{anchor}", location=location, context=context)
            )
    return claims


SHELL_LANGUAGES = frozenset({"bash", "sh", "shell", "zsh", "console", ""})
SHELL_BUILTINS = frozenset({
    "cd", "export", "source", "set", "echo", "exit", "if", "then", "fi", "for", "while", "do",
    "done", "case", "esac", "alias", "unset", "true", "false", ".", "elif", "else", "in",
})  # fmt: skip
_PROMPTS = ("$ ", "> ")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_CONTROL_START = re.compile(r"^[|&;<>(){}!]")


def _strip_prompt(line: str) -> tuple[str, bool]:
    """Return ``(line, had_prompt)`` with one leading ``$ `` or ``> `` prompt removed."""
    for prompt in _PROMPTS:
        if line.startswith(prompt):
            return line[len(prompt) :].strip(), True
    return line, False


def _tokens(line: str) -> list[str]:
    try:
        return shlex.split(line, comments=False, posix=True)
    except ValueError:  # unbalanced quotes: fall back to naive splitting
        return line.split()


def _is_output_token(token: str) -> bool:
    """True for tokens that are log levels, tracebacks or dotted identifiers rather than commands."""
    if len(token) >= 2 and token.isupper():
        return True
    if token.endswith((":", ".")) or "(" in token or ")" in token:
        return True
    return "." in token and "/" not in token


def _command_token(line: str) -> str | None:
    """First command word of ``line`` after leading ``VAR=value`` assignments, or None to skip."""
    for token in _tokens(line):
        if _ASSIGNMENT.match(token):
            continue
        if _CONTROL_START.match(token) or token in SHELL_BUILTINS:
            return None
        return None if _is_output_token(token) else token
    return None


_ANNOTATION = re.compile(r"\s+#\s*\(\d+\)!\s*$")
_POWERSHELL_ENV = "$env:"


def _is_non_command_line(line: str) -> bool:
    """Tree-diagram lines (box-drawing glyphs) and PowerShell ``$Env:`` lines are never commands."""
    return line.lower().startswith(_POWERSHELL_ENV) or ("\u2500" <= line[:1] <= "\u257f")


def _logical_lines(fence: CodeFence) -> list[tuple[int, str, bool]]:
    """``(offset, line, had_prompt)`` with backslash-continued lines joined and ``# (N)!`` stripped."""
    results: list[tuple[int, str, bool]] = []
    pending: tuple[int, str, bool] | None = None
    for offset, raw in enumerate(fence.content.splitlines()):
        text = _ANNOTATION.sub("", raw.strip())
        if pending is not None:
            start, joined, had_prompt = pending
            line = f"{joined} {text}".strip()
        else:
            start = offset
            line, had_prompt = _strip_prompt(text)
        if line.endswith("\\"):
            pending = (start, line[:-1].rstrip(), had_prompt)
            continue
        pending = None
        results.append((start, line, had_prompt))
    if pending is not None:
        results.append(pending)
    return results


def _fence_commands(fence: CodeFence) -> list[tuple[int, str, str]]:
    """``(line, command, stripped_line)`` for each command line in a shell fence."""
    logical = _logical_lines(fence)
    # In console transcripts, unprompted lines are program output, not commands.
    # Bare fences are mostly output (logs, tracebacks), so only prompted lines count there.
    language = fence.language.strip().lower()
    require_prompt = language == "" or (language == "console" and any(had for _, _, had in logical))
    results: list[tuple[int, str, str]] = []
    for offset, line, had_prompt in logical:
        if not line or line.startswith("#") or (require_prompt and not had_prompt) or _is_non_command_line(line):
            continue
        command = _command_token(line)
        if command is not None:
            results.append((fence.start_line + 1 + offset, command, line))
    return results


def extract_command_claims(doc: MarkdownDocument) -> list[Claim]:
    """Return a COMMAND claim for the first command of each line in shell-like code fences.

    v1 limitation: only the first command of a line is claimed; anything after a pipe,
    ``&&``, ``||`` or ``;`` is ignored. Nothing is ever executed. The claim context is the
    line with its prompt removed, so checkers can inspect arguments (e.g. ``python -m``).
    """
    claims: list[Claim] = []
    for fence in doc.code_fences:
        if fence.language.strip().lower() not in SHELL_LANGUAGES:
            continue
        for line_no, command, line in _fence_commands(fence):
            claims.append(
                Claim(
                    kind=ClaimKind.COMMAND,
                    value=command,
                    location=Location(file=doc.path, line=line_no),
                    context=line,
                )
            )
    return claims


ENV_STOPLIST = frozenset({
    "TODO", "FIXME", "NOTE", "WARNING", "ERROR", "INFO", "DEBUG", "GET", "POST", "PUT", "DELETE",
    "PATCH", "HTTP", "HTTPS", "API", "URL", "JSON", "YAML", "README", "LICENSE", "CLI", "SQL",
    "HTML", "CSS", "TRUE", "FALSE", "NULL",
})  # fmt: skip
_ENV_CONTEXTS = (
    re.compile(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9_]{2,})="),
    re.compile(r"\bexport\s+([A-Z][A-Z0-9_]{2,})\b"),
    re.compile(r"\$\{?([A-Z][A-Z0-9_]{2,})\b"),
)
_ENV_ERROR_CODE = re.compile(r"^(?:ERR_|FST_ERR_|E[A-Z]{2,}_)")


def _env_ok(name: str) -> bool:
    return (
        bool(ENV_NAME.match(name))
        and name not in ENV_STOPLIST
        and name not in SYSTEM_ENV_VARS
        and not _ENV_ERROR_CODE.match(name)
    )


def _env_names(text: str) -> list[str]:
    """Env-var names in ``text`` that appear in an explicit shell/env context, without duplicates.

    Only ``VAR=value``, ``export VAR``, ``$VAR`` / ``${VAR}`` (which also covers ``env VAR=``)
    count; a bare upper-case token (a constant name in prose or code) is never a claim. Error-code
    prefixes (``ERR_``, ``FST_ERR_``, ``E[A-Z]{2,}_``) are skipped even in shell context.
    """
    in_context = {m.group(1) for pattern in _ENV_CONTEXTS for m in pattern.finditer(text)}
    return [n for n in sorted(in_context, key=text.find) if _env_ok(n)]


def _env_claims(names: list[str], doc: MarkdownDocument, line: int, context: str) -> list[Claim]:
    location = Location(file=doc.path, line=line)
    return [Claim(kind=ClaimKind.ENV_VAR, value=n, location=location, context=context) for n in names]


_TRACEBACK_HEADER = "Traceback (most recent call last)"


def _looks_like_output(line: str) -> bool:
    """True for error-message style lines: an ellipsis or a first token ending in ``:``."""
    tokens = line.split()
    return "..." in line or bool(tokens and tokens[0].endswith(":"))


def _fence_env_lines(fence: CodeFence) -> list[tuple[int, str]]:
    """``(line, text)`` for fence lines that may carry env claims, skipping program output.

    Bare fences only count prompted lines (prompt removed). Shell-language fences skip
    output-looking lines and everything after a traceback header.
    """
    bare = fence.language.strip() == ""
    results: list[tuple[int, str]] = []
    in_traceback = False
    for offset, raw in enumerate(fence.content.splitlines()):
        line, had_prompt = _strip_prompt(raw.strip())
        in_traceback = in_traceback or _TRACEBACK_HEADER in line
        if in_traceback or (bare and not had_prompt) or _looks_like_output(line):
            continue
        results.append((fence.start_line + 1 + offset, line))
    return results


def extract_env_claims(doc: MarkdownDocument) -> list[Claim]:
    """Return an ENV_VAR claim for each env-var-looking name in inline code and shell fences.

    Names match ``^[A-Z][A-Z0-9_]{2,}$`` and must appear in an assignment (``VAR=...``),
    ``export VAR`` or ``$VAR`` / ``${VAR}`` context; bare tokens are never claims. A small stoplist
    (``TODO``, ``GET``, ``JSON`` ...), OS-provided variables (``PATH``, ``HOME`` ...) and
    error-code names (``ERR_*``) are ignored. Fences follow the command extractor's output
    discipline (see ``_fence_env_lines``).
    """
    lines = _source_lines(doc)
    claims: list[Claim] = []
    for code in doc.inline_codes:
        context = lines[code.line - 1] if 0 < code.line <= len(lines) else ""
        claims.extend(_env_claims(_env_names(code.text), doc, code.line, context))
    for fence in doc.code_fences:
        if fence.language.strip().lower() not in SHELL_LANGUAGES:
            continue
        for line_no, line in _fence_env_lines(fence):
            claims.extend(_env_claims(_env_names(line), doc, line_no, line))
    return claims
