from pathlib import Path

from driftwatch.config import Config
from driftwatch.extract import extract_env_claims
from driftwatch.markdown import parse_markdown
from driftwatch.models import Severity
from driftwatch.scanner import scan


def _names(text: str) -> list[str]:
    return [c.value for c in extract_env_claims(parse_markdown(Path("README.md"), text))]


def test_error_code_in_prose_makes_no_claim() -> None:
    assert _names("Fails with `ERR_BAD_RESPONSE` or ERR_INVALID_URL.\n") == []


def test_error_code_in_shell_fence_makes_no_claim() -> None:
    text = "```bash\nexport ERR_BAD_RESPONSE=1\necho $FST_ERR_CTP_BODY_TOO_LARGE\nEACCES_X=1 ./run\n```\n"
    assert _names(text) == []


def test_constant_name_in_prose_makes_no_claim() -> None:
    assert _names("See `CUSTOM_MESSAGES`, DEFAULT_MAP and `DEFAULT_EXCLUDED_CONTENT_TYPES`.\n") == []


def test_export_assignment_claims() -> None:
    assert _names("```bash\nexport MY_APP_TOKEN=x\n```\n") == ["MY_APP_TOKEN"]


def test_dollar_reference_in_fence_claims() -> None:
    assert _names("```sh\necho $MY_VAR\n```\n") == ["MY_VAR"]


def test_braced_reference_claims() -> None:
    assert _names("Use `${MY_VAR}` here.\n") == ["MY_VAR"]


def test_assignment_before_command_claims() -> None:
    assert _names("```bash\nMY_VAR=1 ./run\n```\n") == ["MY_VAR"]


def test_env_prefix_assignment_claims() -> None:
    assert _names("```bash\nenv MY_VAR=1 ./run\n```\n") == ["MY_VAR"]


def test_inline_code_with_assignment_claims() -> None:
    assert _names("Run `MY_VAR=1 ./run`.\n") == ["MY_VAR"]


def test_documented_interpreter_var_is_info_not_error(tmp_path: Path) -> None:
    text = "```bash\nexport PYTHONUTF8=1\nNODE_OPTIONS=--x node a.js\n```\n"
    (tmp_path / "README.md").write_text(text, encoding="utf-8")
    findings = [f for f in scan(tmp_path, [tmp_path], Config()) if f.checker == "env"]
    assert {f.claim.value for f in findings} == {"PYTHONUTF8", "NODE_OPTIONS"}
    assert {f.severity for f in findings} == {Severity.INFO}
