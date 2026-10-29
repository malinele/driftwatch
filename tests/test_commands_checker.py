from pathlib import Path

from driftwatch.check import CHECKERS, CommandChecker
from driftwatch.extract import extract_command_claims
from driftwatch.markdown import parse_markdown
from driftwatch.models import Claim, Finding, Severity


def _write(root: Path, rel: str, text: str = "") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _claims(root: Path, text: str) -> list[Claim]:
    doc_path = _write(root, "README.md", text)
    return extract_command_claims(parse_markdown(doc_path, text))


def _run(root: Path, text: str) -> list[Finding]:
    return CommandChecker().check(_claims(root, text), root)


def _fence(body: str, lang: str = "bash") -> str:
    return f"# Doc\n\n```{lang}\n{body}\n```\n"


def test_registered() -> None:
    assert isinstance(CHECKERS["command"], CommandChecker)


def test_existing_repo_script_passes(tmp_path: Path) -> None:
    _write(tmp_path, "scripts/x.py")
    _write(tmp_path, "tool.sh")
    assert _run(tmp_path, _fence("scripts/x.py --flag\n./tool.sh")) == []


def test_nonsense_command_fails(tmp_path: Path) -> None:
    findings = _run(tmp_path, _fence("frobnicate-the-widgets --now"))
    assert len(findings) == 1
    assert findings[0].severity is Severity.ERROR
    assert findings[0].message == "documented command not found: frobnicate-the-widgets"


def test_path_and_known_commands_pass(tmp_path: Path) -> None:
    assert _run(tmp_path, _fence("ls -la\ndocker compose up\nuvicorn app:app")) == []


def test_python_m_existing_module_passes_src_layout(tmp_path: Path) -> None:
    _write(tmp_path, "src/mypkg/__init__.py")
    _write(tmp_path, "src/mypkg/tool.py")
    text = _fence("python -m mypkg\npython3 -m mypkg.tool\n.venv/bin/python -m mypkg")
    assert _run(tmp_path, text) == []


def test_python_m_missing_module_fails(tmp_path: Path) -> None:
    findings = _run(tmp_path, _fence("python -m nopkg.missing"))
    assert [f.message for f in findings] == ["documented python module not found: nopkg.missing"]


def test_python_m_stdlib_module_passes(tmp_path: Path) -> None:
    assert _run(tmp_path, _fence("python -m venv .venv")) == []


def test_env_var_prefix_claims_the_command(tmp_path: Path) -> None:
    claims = _claims(tmp_path, _fence("FOO=1 BAR=two frobnicate run"))
    assert [c.value for c in claims] == ["frobnicate"]


def test_pure_assignment_skipped(tmp_path: Path) -> None:
    assert _claims(tmp_path, _fence("FOO=bar")) == []


def test_builtins_comments_and_continuations_skipped(tmp_path: Path) -> None:
    body = "cd src\nexport A=1\n# comment\necho hi\nsource env.sh\nmake \\\n  build\nif true; then"
    assert [c.value for c in _claims(tmp_path, _fence(body))] == ["make"]


def test_piped_line_claims_only_first_command(tmp_path: Path) -> None:
    claims = _claims(tmp_path, _fence("cat a.txt | grep x && frobnicate"))
    assert [c.value for c in claims] == ["cat"]


def test_console_fence_with_prompts(tmp_path: Path) -> None:
    body = "$ git status\nOn branch main\n$ make test\n> pytest -q\nall passed"
    claims = _claims(tmp_path, _fence(body, "console"))
    assert [c.value for c in claims] == ["git", "make", "pytest"]


def test_prompt_stripped_in_plain_shell_fence(tmp_path: Path) -> None:
    claims = _claims(tmp_path, _fence("  $ git log", "sh"))
    assert [c.value for c in claims] == ["git"]


def test_non_shell_fences_produce_no_claims(tmp_path: Path) -> None:
    text = _fence("print('hi')", "python") + _fence('{"a": 1}', "json")
    assert _claims(tmp_path, text) == []


def test_untagged_fence_is_scanned(tmp_path: Path) -> None:
    assert [c.value for c in _claims(tmp_path, _fence("git status", ""))] == ["git"]


def test_claim_location_is_the_actual_line(tmp_path: Path) -> None:
    claims = _claims(tmp_path, _fence("git status\n\nmake test"))
    assert [c.location.line for c in claims] == [4, 6]
