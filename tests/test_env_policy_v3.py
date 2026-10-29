from pathlib import Path

from driftwatch.envscan import scan_auto_envvar_prefixes
from driftwatch.models import Finding, Severity
from driftwatch.scanner import scan


def _write(root: Path, rel: str, text: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _env(root: Path) -> list[Finding]:
    return [f for f in scan(root, [root]) if f.checker == "env"]


def test_auto_envvar_prefix_keyword_and_dict_forms_detected(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", 'main(auto_envvar_prefix="ALPHA")\n')
    _write(tmp_path, "b.py", 'main(context_settings={"auto_envvar_prefix": "BETA"})\n')
    assert scan_auto_envvar_prefixes(tmp_path) == {"ALPHA", "BETA"}


def test_auto_envvar_prefix_makes_documented_var_read(tmp_path: Path) -> None:
    _write(tmp_path, "cli.py", 'cli(context_settings={"auto_envvar_prefix": "UVICORN"})\n')
    _write(tmp_path, "README.md", "Set `UVICORN_HOST=0.0.0.0` and `UVICORN_PORT=80`.\n")
    assert _env(tmp_path) == []


def test_prefix_requires_underscore_boundary(tmp_path: Path) -> None:
    _write(tmp_path, "cli.py", 'cli(auto_envvar_prefix="UVI")\n')
    _write(tmp_path, "README.md", "Set `UVICORN_HOST=0.0.0.0`.\n")
    assert [f.severity for f in _env(tmp_path)] == [Severity.ERROR]


def test_read_in_docs_src_counts_as_code(tmp_path: Path) -> None:
    _write(tmp_path, "docs_src/app.py", 'import os\nname = os.getenv("AWESOME_NAME")\n')
    _write(tmp_path, "README.md", "Set `AWESOME_NAME=x`.\n")
    assert _env(tmp_path) == []


def test_read_in_examples_dir_counts_as_code(tmp_path: Path) -> None:
    _write(tmp_path, "examples/app.py", 'import os\nname = os.getenv("GOD_NAME")\n')
    _write(tmp_path, "README.md", "Set `GOD_NAME=x`.\n")
    assert _env(tmp_path) == []


def test_doc_local_env_definition_makes_var_user_defined(tmp_path: Path) -> None:
    text = (
        "```ini\nDATABASE_URL=sqlite:///x\nSECRET_KEY=abc\n```\n\n"
        "Then run `echo $DATABASE_URL` and `echo $SECRET_KEY`.\n"
    )
    _write(tmp_path, "README.md", text)
    assert _env(tmp_path) == []


def test_lone_export_is_still_checked(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "```bash\nexport MY_APP_TOKEN=x\n```\n")
    assert [f.severity for f in _env(tmp_path)] == [Severity.ERROR]


def test_inline_prefix_for_other_program_is_not_claimed(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "Run `PYTHONHASHSEED=0 GLIBC_TUNABLES=x python app.py`.\n")
    assert _env(tmp_path) == []


def test_inline_prefix_in_fence_is_not_claimed(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "```bash\nGLIBC_TUNABLES=glibc.x=1 ./run\n```\n")
    assert _env(tmp_path) == []


def test_ci_workflow_var_is_not_flagged(tmp_path: Path) -> None:
    _write(tmp_path, ".github/workflows/pub.yml", "env:\n  UV_PUBLISH_TOKEN: ${{ secrets.T }}\n")
    _write(tmp_path, "README.md", "Set `UV_PUBLISH_TOKEN=x`.\n")
    assert _env(tmp_path) == []


def test_undocumented_read_still_warns(tmp_path: Path) -> None:
    _write(tmp_path, "src/app.py", 'import os\nos.environ["SECRET_THING"]\n')
    _write(tmp_path, "README.md", "Nothing here.\n")
    findings = _env(tmp_path)
    assert [f.severity for f in findings] == [Severity.WARNING]
    assert findings[0].claim.value == "SECRET_THING"


def test_documented_never_read_still_errors(tmp_path: Path) -> None:
    _write(tmp_path, ".github/workflows/ci.yml", "env:\n  OTHER_VAR: 1\n")
    _write(tmp_path, "README.md", "Set `MY_APP_TOKEN=x`.\n")
    assert [f.severity for f in _env(tmp_path)] == [Severity.ERROR]
