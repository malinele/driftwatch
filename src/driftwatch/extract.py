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
    return "/" in token or token.lower().endswith(PATH_EXTENSIONS)


def _source_lines(doc: MarkdownDocument) -> list[str]:
    try:
        return doc.path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []


def extract_path_claims(doc: MarkdownDocument) -> list[Claim]:
    """Return a PATH claim for every inline code span that looks like a repo path."""
    lines = _source_lines(doc)
    claims: list[Claim] = []
    for code in doc.inline_codes:
        token = code.text.strip().rstrip(TRAILING_PUNCTUATION)
        if not _looks_like_path(token):
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


def _fence_commands(fence: CodeFence) -> list[tuple[int, str, str]]:
    """``(line, command, stripped_line)`` for each command line in a shell fence."""
    stripped = [_strip_prompt(line.strip()) for line in fence.content.splitlines()]
    # In console transcripts, unprompted lines are program output, not commands.
    # Bare fences are mostly output (logs, tracebacks), so only prompted lines count there.
    language = fence.language.strip().lower()
    require_prompt = language == "" or (language == "console" and any(had for _, had in stripped))
    results: list[tuple[int, str, str]] = []
    continued = False
    for offset, (line, had_prompt) in enumerate(stripped):
        was_continued, continued = continued, line.endswith("\\")
        if was_continued or not line or line.startswith("#") or (require_prompt and not had_prompt):
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
_ENV_TOKEN = re.compile(r"(?<![A-Za-z0-9_])[A-Z][A-Z0-9_]{2,}(?![A-Za-z0-9_])")
_ENV_CONTEXTS = (
    re.compile(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9_]{2,})="),
    re.compile(r"\bexport\s+([A-Z][A-Z0-9_]{2,})\b"),
    re.compile(r"\$\{?([A-Z][A-Z0-9_]{2,})\b"),
)


def _env_ok(name: str) -> bool:
    return bool(ENV_NAME.match(name)) and name not in ENV_STOPLIST and name not in SYSTEM_ENV_VARS


def _env_names(text: str, *, whole_span: bool) -> list[str]:
    """Env-var-looking names in ``text`` without duplicates.

    An underscore or an assignment/export/``$`` context is required. With ``whole_span`` (inline
    code) a bare token only counts when it is the entire span, so prose-like code is not mined.
    """
    in_context = {m.group(1) for pattern in _ENV_CONTEXTS for m in pattern.finditer(text)}
    stripped = text.strip()
    bare = [stripped] if whole_span and _ENV_TOKEN.fullmatch(stripped) else []
    if not whole_span:
        bare = _ENV_TOKEN.findall(text)
    candidates = sorted(in_context | set(bare), key=text.find)
    return [n for n in candidates if _env_ok(n) and ("_" in n or n in in_context)]


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

    Names match ``^[A-Z][A-Z0-9_]{2,}$`` and must contain ``_`` or appear in an assignment
    (``VAR=...``), ``export VAR`` or ``$VAR`` / ``${VAR}`` context. A small stoplist (``TODO``,
    ``GET``, ``JSON`` ...) and OS-provided variables (``PATH``, ``HOME`` ...) are ignored.
    Inline code only yields a bare name when it is the whole span. Fences follow the command
    extractor's output discipline (see ``_fence_env_lines``).
    """
    lines = _source_lines(doc)
    claims: list[Claim] = []
    for code in doc.inline_codes:
        context = lines[code.line - 1] if 0 < code.line <= len(lines) else ""
        claims.extend(_env_claims(_env_names(code.text, whole_span=True), doc, code.line, context))
    for fence in doc.code_fences:
        if fence.language.strip().lower() not in SHELL_LANGUAGES:
            continue
        for line_no, line in _fence_env_lines(fence):
            claims.extend(_env_claims(_env_names(line, whole_span=False), doc, line_no, line))
    return claims
