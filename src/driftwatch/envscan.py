"""Statically find the environment variables the code base reads.

Everything here is regex based: no source file is imported or executed. Known limitations:

* only string-literal names are seen (``os.environ[name]`` with a variable is invisible);
* reads through helper wrappers (``get_env("X")``, ``env("X")``) are not recognised;
* shell ``$X`` includes script-local variables unless they are assigned in the same script
  (``X=...``, ``for X in``, ``read X``); variables set in a sourced file may be reported;
* pydantic-settings ``env_prefix``, ``alias`` and ``validation_alias`` are ignored: the upper-cased
  field name is taken as the variable name.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
# Variables provided by the OS or shell; documenting or reading them says nothing about the project.
SYSTEM_ENV_VARS = frozenset({
    "PATH", "HOME", "USER", "SHELL", "PWD", "OLDPWD", "TERM", "LANG", "TMPDIR", "HOSTNAME",
    "EDITOR", "LOGNAME", "IFS", "PS1", "SHLVL", "UID", "EUID", "RANDOM", "LINENO", "OPTARG",
    "OPTIND", "BASH_SOURCE", "BASH_VERSION",
})  # fmt: skip

_QUOTED = r"""["']([A-Z][A-Z0-9_]*)["']"""
_PY_READS = (
    re.compile(r"os\.environ\s*\[\s*" + _QUOTED + r"\s*\]"),
    re.compile(r"os\.environ\.(?:get|setdefault|pop)\(\s*" + _QUOTED),
    re.compile(r"os\.getenv\(\s*" + _QUOTED),
)
_JS_READS = (
    re.compile(r"process\.env\.([A-Z][A-Z0-9_]*)\b"),
    re.compile(r"process\.env\s*\[\s*" + _QUOTED + r"\s*\]"),
)
_SH_READ = re.compile(r"\$(?:\{([A-Z][A-Z0-9_]*)(?:[:#%/^,@\-+=?][^}]*)?\}|([A-Z][A-Z0-9_]*)\b)")
_SH_LOCAL = re.compile(r"(?:^|[\s;&|(])(?:export\s+|local\s+|readonly\s+)?([A-Z][A-Z0-9_]*)=")
_SH_LOOP_VAR = re.compile(r"\b(?:for|read(?:\s+-\w+)*)\s+([A-Z][A-Z0-9_]*)\b")
_SETTINGS_CLASS = re.compile(r"^class\s+\w+\s*\(([^)]*)\)\s*:", re.MULTILINE)
_SETTINGS_BASE = re.compile(r"(?:^|[\s,.])BaseSettings\s*(?:$|[,\[])")
_FIELD = re.compile(r"^(\s+)([A-Za-z][A-Za-z0-9_]*)\s*:")

_PY_SUFFIX = ".py"
_JS_SUFFIXES = (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs")
_SH_SUFFIX = ".sh"


def _is_skipped_dir(name: str) -> bool:
    # Imported lazily: scanner imports check, which imports this module.
    from driftwatch.scanner import _is_skipped_dir as skipped

    return skipped(name)


def _python_reads(text: str) -> set[str]:
    found = {m.group(1) for pattern in _PY_READS for m in pattern.finditer(text)}
    return found | _settings_fields(text)


def _settings_fields(text: str) -> set[str]:
    """Upper-cased annotated field names of classes that list ``BaseSettings`` as a base.

    Only fields at the class body's own indentation count (a nested ``class Config`` is skipped).
    """
    names: set[str] = set()
    lines = text.splitlines()
    for match in _SETTINGS_CLASS.finditer(text):
        if not _SETTINGS_BASE.search(match.group(1)):
            continue
        body_indent: str | None = None
        for line in lines[text.count("\n", 0, match.end()) + 1 :]:
            if line.strip() and not line[0].isspace():
                break
            field = _FIELD.match(line)
            if field is None:
                continue
            indent, name = field.groups()
            body_indent = indent if body_indent is None else body_indent
            if indent == body_indent and not name.startswith("model_"):
                names.add(name.upper())
    return names


def _js_reads(text: str) -> set[str]:
    return {m.group(1) for pattern in _JS_READS for m in pattern.finditer(text)}


def _shell_reads(text: str) -> set[str]:
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    local = {m.group(1) for m in _SH_LOCAL.finditer(code)} | {m.group(1) for m in _SH_LOOP_VAR.finditer(code)}
    used = {m.group(1) or m.group(2) for m in _SH_READ.finditer(code)}
    return used - local


def _reads_for(path: Path, text: str) -> set[str]:
    if path.suffix == _PY_SUFFIX:
        return _python_reads(text)
    if path.suffix in _JS_SUFFIXES:
        return _js_reads(text)
    if path.suffix == _SH_SUFFIX:
        return _shell_reads(text)
    return set()


def _source_files(root: Path) -> list[Path]:
    suffixes = (_PY_SUFFIX, _SH_SUFFIX, *_JS_SUFFIXES)
    files: list[Path] = []
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _is_skipped_dir(d)]
        files.extend(Path(current) / f for f in filenames if f.endswith(suffixes))
    return files


def scan_code_env_reads(root: Path) -> set[str]:
    """Names of environment variables read by source files under ``root``.

    Handles ``*.py`` (``os.environ[...]``, ``os.environ.get/setdefault/pop(...)``, ``os.getenv(...)``,
    upper-cased fields of ``BaseSettings`` subclasses), the ``*.js/*.ts`` family (``process.env.X``,
    ``process.env["X"]``) and ``*.sh`` (``$X``, ``${X}``). Directories skipped by the scanner
    (hidden, ``.venv``, ``node_modules`` ...) are skipped. See the module docstring for limitations.
    """
    reads: set[str] = set()
    for path in _source_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        reads |= _reads_for(path, text)
    return {n for n in reads if ENV_NAME.match(n) and n not in SYSTEM_ENV_VARS}
