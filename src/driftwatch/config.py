"""Project configuration: ``[tool.driftwatch]`` in pyproject.toml and/or a root ``driftwatch.toml``."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from driftwatch.check import CHECKERS, MkdocsSetting
from driftwatch.models import Severity

PYPROJECT = "pyproject.toml"
DRIFTWATCH_TOML = "driftwatch.toml"
KNOWN_KEYS = frozenset({
    "enabled_checkers", "exclude", "env_stoplist_extra", "fail_on",
    "ambient_extra", "historical", "historical_full_scan", "mkdocs",
})  # fmt: skip
_SEVERITY_RANK = {Severity.INFO: 0, Severity.WARNING: 1, Severity.ERROR: 2}


class ConfigError(Exception):
    """Raised for unreadable, malformed or unknown configuration."""


def severity_at_least(severity: Severity, threshold: Severity) -> bool:
    return _SEVERITY_RANK[severity] >= _SEVERITY_RANK[threshold]


def _default_checkers() -> frozenset[str]:
    return frozenset(CHECKERS)


@dataclass(frozen=True)
class Config:
    enabled_checkers: frozenset[str] = field(default_factory=_default_checkers)
    exclude: tuple[str, ...] = ()
    env_stoplist_extra: tuple[str, ...] = ()
    fail_on: Severity = Severity.ERROR
    ambient_extra: tuple[str, ...] = ()
    historical: tuple[str, ...] = ()
    historical_full_scan: bool = False
    mkdocs: MkdocsSetting = "auto"


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path.name}: invalid TOML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"{path.name}: cannot read: {exc}") from exc


def _raw_settings(root: Path) -> dict[str, Any]:
    """Merge pyproject's ``[tool.driftwatch]`` with ``driftwatch.toml``; the latter wins per key."""
    merged: dict[str, Any] = {}
    pyproject = root / PYPROJECT
    if pyproject.is_file():
        table = _read_toml(pyproject).get("tool", {}).get("driftwatch", {})
        if not isinstance(table, dict):
            raise ConfigError(f"{PYPROJECT}: [tool.driftwatch] must be a table")
        merged.update(table)
    dedicated = root / DRIFTWATCH_TOML
    if dedicated.is_file():
        merged.update(_read_toml(dedicated))
    return merged


def _string_list(key: str, value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"driftwatch config: '{key}' must be a list of strings")
    return tuple(value)


def _parse_bool(key: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"driftwatch config: '{key}' must be true or false")
    return value


def _parse_mkdocs(value: object) -> MkdocsSetting:
    if isinstance(value, bool):
        return value
    if value == "auto":
        return "auto"
    raise ConfigError("driftwatch config: 'mkdocs' must be true, false or \"auto\"")


def _parse_fail_on(value: object) -> Severity:
    if isinstance(value, str):
        try:
            return Severity(value.lower())
        except ValueError:
            pass
    allowed = ", ".join(s.value for s in Severity)
    raise ConfigError(f"driftwatch config: 'fail_on' must be one of: {allowed}")


def _parse_checkers(value: object) -> frozenset[str]:
    names = frozenset(_string_list("enabled_checkers", value))
    unknown = sorted(names - set(CHECKERS))
    if unknown:
        raise ConfigError(
            f"driftwatch config: unknown checker(s) in 'enabled_checkers': {', '.join(unknown)} "
            f"(available: {', '.join(sorted(CHECKERS))})"
        )
    return names


def load_config(root: Path) -> Config:
    raw = _raw_settings(root)
    unknown = sorted(set(raw) - KNOWN_KEYS)
    if unknown:
        raise ConfigError(
            f"driftwatch config: unknown key(s): {', '.join(unknown)} (valid keys: {', '.join(sorted(KNOWN_KEYS))})"
        )
    defaults = Config()
    checkers = _parse_checkers(raw["enabled_checkers"]) if "enabled_checkers" in raw else defaults.enabled_checkers
    exclude = _string_list("exclude", raw["exclude"]) if "exclude" in raw else defaults.exclude
    stoplist = (
        _string_list("env_stoplist_extra", raw["env_stoplist_extra"])
        if "env_stoplist_extra" in raw
        else defaults.env_stoplist_extra
    )
    fail_on = _parse_fail_on(raw["fail_on"]) if "fail_on" in raw else defaults.fail_on
    ambient = _string_list("ambient_extra", raw["ambient_extra"]) if "ambient_extra" in raw else defaults.ambient_extra
    historical = _string_list("historical", raw["historical"]) if "historical" in raw else defaults.historical
    full_scan = (
        _parse_bool("historical_full_scan", raw["historical_full_scan"])
        if "historical_full_scan" in raw
        else defaults.historical_full_scan
    )
    mkdocs = _parse_mkdocs(raw["mkdocs"]) if "mkdocs" in raw else defaults.mkdocs
    return Config(
        enabled_checkers=checkers,
        exclude=exclude,
        env_stoplist_extra=stoplist,
        fail_on=fail_on,
        ambient_extra=ambient,
        historical=historical,
        historical_full_scan=full_scan,
        mkdocs=mkdocs,
    )
