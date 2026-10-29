from pathlib import Path

import pytest

from driftwatch.extract import extract_path_claims
from driftwatch.markdown import parse_markdown


def _claims(tmp_path: Path, token: str) -> list[str]:
    text = f"`{token}`\n"
    doc = tmp_path / "doc.md"
    doc.write_text(text, encoding="utf-8")
    return [c.value for c in extract_path_claims(parse_markdown(doc, text))]


@pytest.mark.parametrize(
    "token",
    [
        # IANA media types
        "application/json",
        "multipart/form-data",
        "application/x-www-form-urlencoded",
        "image/png",
        "text/html",
        "text/event-stream",
        "image/svg+xml",
        # CIDR and IP forms
        "0.0.0.0/0",
        "10.100.0.0/16",
        "[2001:db8::]/32",
        "::ffff:10.0.0.0/104",
        "::/0",
        # npm scopes
        "@fastify/cors",
        "@types/node",
        "@solana/web3.js",
        # owner/repo tokens
        "axios/axios",
        "tc39/proposal-x",
        "Kludex/awesome-pydantic",
        "fastify/fastify:HEAD",
        # prose constructs
        "async/await",
        "try/except/finally",
        "expand/unexpand",
        # bare extensions
        ".json",
        ".yaml",
        ".md",
        ".html",
        ".py",
        # regex and shell redirection
        r"^/foo\.py$",
        "2>/dev/null",
        "a|b/c.py",
        # single-segment directory that is not a common source dir
        "foo/",
    ],
)
def test_token_is_not_claimed(tmp_path: Path, token: str) -> None:
    assert _claims(tmp_path, token) == []


@pytest.mark.parametrize(
    "token",
    [
        "src/driftwatch/cli.py",
        "./run.sh",
        "../x.md",
        "docs/user-guide.md",
        "scripts/run.sh",
        "README.md",
        "docs/",
        "test/test_types/",
        "foo/bar/",
        "app/",
        "./build/",
        "~/notes/",
        "./bin/tool",
        "/abs/path",
        "image/logo.png",
    ],
)
def test_token_is_claimed(tmp_path: Path, token: str) -> None:
    assert _claims(tmp_path, token) == [token]
