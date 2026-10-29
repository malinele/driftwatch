from pathlib import Path

from driftwatch.check import CHECKERS, EnvChecker
from driftwatch.envscan import scan_code_env_reads
from driftwatch.extract import extract_env_claims
from driftwatch.markdown import parse_markdown
from driftwatch.models import Claim, ClaimKind, Finding, Location, Severity
from driftwatch.scanner import scan


def _write(root: Path, rel: str, text: str = "") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _claims(root: Path, text: str) -> list[Claim]:
    doc = _write(root, "README.md", text)
    return extract_env_claims(parse_markdown(doc, text))


def _names(claims: list[Claim]) -> list[str]:
    return [c.value for c in claims]


def _messages(findings: list[Finding], severity: Severity) -> list[str]:
    return [f.message for f in findings if f.severity is severity]


def test_registered() -> None:
    assert isinstance(CHECKERS["env"], EnvChecker)


def test_documented_and_read_passes(tmp_path: Path) -> None:
    _write(tmp_path, "app.py", 'import os\nDB = os.environ["DATABASE_URL"]\n')
    _write(tmp_path, "README.md", "Set `DATABASE_URL` first.\n")
    assert scan(tmp_path, [tmp_path]) == []


def test_documented_not_read_errors(tmp_path: Path) -> None:
    _write(tmp_path, "app.py", "print('hi')\n")
    _write(tmp_path, "README.md", "Set `OLD_API_KEY` first.\n")
    findings = scan(tmp_path, [tmp_path])
    assert _messages(findings, Severity.ERROR) == ["documented env var is not read by the code: OLD_API_KEY"]


def test_read_not_documented_warns_once(tmp_path: Path) -> None:
    _write(tmp_path, "app.py", 'import os\na = os.getenv("SECRET_TOKEN")\nb = os.environ.get("SECRET_TOKEN")\n')
    _write(tmp_path, "README.md", "# Project\n")
    _write(tmp_path, "docs/guide.md", "# Guide\n")
    findings = scan(tmp_path, [tmp_path])
    assert _messages(findings, Severity.WARNING) == ["env var read by code but undocumented: SECRET_TOKEN"]
    assert findings[0].claim.location == Location(file=tmp_path / "README.md", line=1)


def test_warning_falls_back_to_first_doc_without_root_readme(tmp_path: Path) -> None:
    _write(tmp_path, "app.py", 'import os\nos.getenv("SECRET_TOKEN")\n')
    doc = _write(tmp_path, "docs/guide.md", "# Guide\n")
    findings = scan(tmp_path, [tmp_path])
    assert [f.claim.location.file for f in findings] == [doc]


def test_mention_in_prose_counts_as_documented(tmp_path: Path) -> None:
    _write(tmp_path, "app.py", 'import os\nos.getenv("PORT_NUMBER")\nos.getenv("DEBUGGING")\n')
    _write(tmp_path, "README.md", "The PORT_NUMBER variable and DEBUGGING flag.\n")
    assert scan(tmp_path, [tmp_path]) == []


def test_stoplist_tokens_ignored(tmp_path: Path) -> None:
    text = "Use `TODO`, `GET`, `JSON`, `NOTE` and `HTTP_STATUS`.\n\n```bash\nexport TODO=1\ncurl -X GET $API\n```\n"
    assert _names(_claims(tmp_path, text)) == ["HTTP_STATUS"]


def test_underscore_or_assignment_context_required(tmp_path: Path) -> None:
    text = "Plain `TOKEN` and `MY_VAR` and `PORT=80` and `$HOST` and `${NAME}`.\n"
    assert _names(_claims(tmp_path, text)) == ["MY_VAR", "PORT", "HOST", "NAME"]


def test_shell_fence_claims_have_fence_lines(tmp_path: Path) -> None:
    claims = _claims(tmp_path, "# T\n\n```sh\nexport APP_MODE=prod\n./run.sh\n```\n")
    assert [(c.value, c.location.line) for c in claims] == [("APP_MODE", 4)]


def test_non_shell_fence_ignored(tmp_path: Path) -> None:
    assert _claims(tmp_path, "```python\nx = 'SOME_CONSTANT'\n```\n") == []


def test_pydantic_basesettings_fields_counted(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "config.py",
        "from pydantic_settings import BaseSettings\n\n\n"
        "class Settings(BaseSettings):\n"
        "    database_url: str\n"
        "    redis_host: str = 'x'\n\n"
        "    class Config:\n"
        "        env_prefix = 'x'\n"
        "        nested: int\n\n"
        "class Other:\n"
        "    not_a_setting: int\n",
    )
    assert scan_code_env_reads(tmp_path) == {"DATABASE_URL", "REDIS_HOST"}


def test_process_env_detected(tmp_path: Path) -> None:
    _write(tmp_path, "src/a.ts", 'const a = process.env.API_TOKEN;\nconst b = process.env["OTHER_KEY"];\n')
    _write(tmp_path, "b.js", "const c = process.env.THIRD_VAR\n")
    assert scan_code_env_reads(tmp_path) == {"API_TOKEN", "OTHER_KEY", "THIRD_VAR"}


def test_shell_script_reads_exclude_locals(tmp_path: Path) -> None:
    _write(tmp_path, "run.sh", 'LOCAL_DIR=/x\necho "$LOCAL_DIR ${DEPLOY_ENV:-dev} $HOME"\n# $COMMENTED_OUT\n')
    assert scan_code_env_reads(tmp_path) == {"DEPLOY_ENV"}


def test_ignored_dirs_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "node_modules/x.js", "process.env.HIDDEN_VAR\n")
    _write(tmp_path, ".venv/x.py", 'import os\nos.getenv("HIDDEN_TWO")\n')
    assert scan_code_env_reads(tmp_path) == set()


def test_check_without_docs_reports_only_errors(tmp_path: Path) -> None:
    _write(tmp_path, "app.py", 'import os\nos.getenv("UNDOC_VAR")\n')
    claim = Claim(ClaimKind.ENV_VAR, "GONE_VAR", Location(file=tmp_path / "README.md", line=1), "")
    findings = EnvChecker().check([claim], tmp_path)
    assert _messages(findings, Severity.ERROR) == ["documented env var is not read by the code: GONE_VAR"]
    assert _messages(findings, Severity.WARNING) == []
