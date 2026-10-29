"""Core data model shared by extractors and checkers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ClaimKind(StrEnum):
    PATH = "path"
    COMMAND = "command"
    ENV_VAR = "env_var"
    LINK = "link"
    ANCHOR = "anchor"


@dataclass(frozen=True)
class Location:
    """Position in a source file. ``line`` is 1-based."""

    file: Path
    line: int


@dataclass(frozen=True)
class Claim:
    """A verifiable statement a document makes about the repository."""

    kind: ClaimKind
    value: str
    location: Location
    context: str  # the source line text the claim was found on


@dataclass(frozen=True)
class Finding:
    """The outcome of checking a claim that did not hold up."""

    claim: Claim
    severity: Severity
    message: str
    checker: str
