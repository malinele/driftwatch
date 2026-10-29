from pathlib import Path

from driftwatch.extract import extract_env_claims
from driftwatch.markdown import parse_markdown

_DOC = Path("README.md")


def _names(text: str) -> list[str]:
    return [c.value for c in extract_env_claims(parse_markdown(_DOC, text))]


def test_bare_fence_error_output_makes_no_claim() -> None:
    text = "```\nssl.SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] failed ...\n```\n"
    assert _names(text) == []


def test_bare_fence_unprompted_assignment_makes_no_claim() -> None:
    assert _names("```\nFOO_BAR=1 ./run\n```\n") == []


def test_bare_fence_prompted_assignment_claims() -> None:
    assert _names("```\n$ FOO_BAR=1 ./run\n```\n") == ["FOO_BAR"]
    assert _names("```\n> FOO_BAR=1 ./run\n```\n") == ["FOO_BAR"]


def test_shell_fence_export_claims() -> None:
    assert _names("```bash\nexport MY_VAR=1\n```\n") == ["MY_VAR"]


def test_shell_fence_error_prefix_line_skipped() -> None:
    assert _names("```bash\nsomething: PRE_MAC_LENGTH_TOO_LONG\n```\n") == []


def test_shell_fence_ellipsis_line_skipped() -> None:
    assert _names("```sh\necho ... $MY_VAR\n```\n") == []


def test_shell_fence_lines_after_traceback_skipped() -> None:
    text = (
        "```console\nexport MY_VAR=1\nTraceback (most recent call last):\n"
        '  File "x.py", line 1\nexport OTHER_VAR=2\n```\n'
    )
    assert _names(text) == ["MY_VAR"]


def test_non_shell_fences_never_claim() -> None:
    for lang in ("python", "json", "text"):
        assert _names(f"```{lang}\nexport MY_VAR=1\nFOO_BAR=1\n```\n") == []


def test_inline_code_unchanged() -> None:
    # Bare `MY_VAR` is constant-like, not a claim; `X=1` is too short to be a name.
    assert _names("Set `MY_VAR` and `X=1` and `MY_OTHER=1`.\n") == ["MY_OTHER"]
