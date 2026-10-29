"""Extract verifiable claims from parsed markdown documents."""

from __future__ import annotations

import re
import shlex
from urllib.parse import unquote

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


def _command_token(line: str) -> str | None:
    """First command word of ``line`` after leading ``VAR=value`` assignments, or None to skip."""
    for token in _tokens(line):
        if _ASSIGNMENT.match(token):
            continue
        if _CONTROL_START.match(token) or token in SHELL_BUILTINS:
            return None
        return token
    return None


def _fence_commands(fence: CodeFence) -> list[tuple[int, str, str]]:
    """``(line, command, stripped_line)`` for each command line in a shell fence."""
    stripped = [_strip_prompt(line.strip()) for line in fence.content.splitlines()]
    # In console transcripts, unprompted lines are program output, not commands.
    require_prompt = fence.language == "console" and any(had for _, had in stripped)
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
