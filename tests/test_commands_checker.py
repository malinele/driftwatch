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


def test_unknown_bare_command_is_info_not_error(tmp_path: Path) -> None:
    findings = _run(tmp_path, _fence("frobnicate-the-widgets --now"))
    assert len(findings) == 1
    assert findings[0].severity is Severity.INFO
    assert findings[0].message == "command not found in repo or on PATH (external tool?)"


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


def test_bare_fence_with_prompt_claims_command(tmp_path: Path) -> None:
    assert [c.value for c in _claims(tmp_path, _fence("$ git status", ""))] == ["git"]


def test_bare_fence_gt_prompt_claims_command(tmp_path: Path) -> None:
    assert [c.value for c in _claims(tmp_path, _fence("> make test", ""))] == ["make"]


def test_bare_fence_unprompted_line_is_output(tmp_path: Path) -> None:
    assert _claims(tmp_path, _fence("git status", "")) == []


def test_bare_fence_log_output_produces_no_claims(tmp_path: Path) -> None:
    body = "DEBUG connecting to host\nTraceback (most recent call last):\nhttpx.ProxyError: nope\nPassword:"
    assert _claims(tmp_path, _fence(body, "")) == []


def test_bare_fence_mixes_prompted_and_output(tmp_path: Path) -> None:
    claims = _claims(tmp_path, _fence("$ git log\nDEBUG x\nsome output", ""))
    assert [c.value for c in claims] == ["git"]


def test_shell_fence_dotted_identifier_not_claimed(tmp_path: Path) -> None:
    assert _claims(tmp_path, _fence("httpx.ProxyError: blah")) == []


def test_shell_fence_all_caps_word_not_claimed(tmp_path: Path) -> None:
    assert _claims(tmp_path, _fence("DEBUG something")) == []


def test_shell_fence_trailing_colon_or_paren_not_claimed(tmp_path: Path) -> None:
    assert _claims(tmp_path, _fence("Password:\nfoo(bar)\nDone.")) == []


def test_shell_fence_relative_script_still_claimed(tmp_path: Path) -> None:
    assert [c.value for c in _claims(tmp_path, _fence("./scripts/run.sh"))] == ["./scripts/run.sh"]


def test_claim_location_is_the_actual_line(tmp_path: Path) -> None:
    claims = _claims(tmp_path, _fence("git status\n\nmake test"))
    assert [c.location.line for c in claims] == [4, 6]


_SCRIPTS_TOML = '[project]\nname = "x"\n[project.scripts]\nmycli = "x:main"\n'


def test_known_tool_has_no_finding(tmp_path: Path) -> None:
    assert _run(tmp_path, _fence("uv sync\nuvx ruff\npnpm i\ngunicorn app:app\ndeactivate")) == []


def test_missing_relative_script_is_error(tmp_path: Path) -> None:
    _write(tmp_path, "scripts/run.sh")  # policy v3: ERROR needs an existing parent directory
    findings = _run(tmp_path, _fence("./scripts/missing.sh"))
    assert [(f.severity, f.message) for f in findings] == [
        (Severity.ERROR, "documented command not found: ./scripts/missing.sh")
    ]


def test_missing_path_without_dot_prefix_is_info(tmp_path: Path) -> None:
    # Policy v3: only ./ and ../ paths are ERROR candidates; a bare slash path is just unknown.
    findings = _run(tmp_path, _fence("scripts/missing.sh"))
    assert [f.severity for f in findings] == [Severity.INFO]


def test_repo_defined_console_script_passes(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", _SCRIPTS_TOML)
    _write(tmp_path, "x.py")  # the entry point target exists; a missing target is an ERROR (v3)
    assert _run(tmp_path, _fence("mycli --help")) == []


def test_unlisted_command_with_scripts_defined_is_info(tmp_path: Path) -> None:
    # Policy v3: an unknown name is INFO even when the repo defines other commands.
    _write(tmp_path, "pyproject.toml", _SCRIPTS_TOML)
    _write(tmp_path, "x.py")
    findings = _run(tmp_path, _fence("othercli --help"))
    assert [f.severity for f in findings] == [Severity.INFO]


def test_known_tool_passes_even_when_scripts_defined(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", _SCRIPTS_TOML)
    assert _run(tmp_path, _fence("git status\nuv sync")) == []


def test_package_json_bin_defines_commands(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", '{"name": "pkg", "bin": {"hello": "cli.js"}}')
    _write(tmp_path, "cli.js")
    findings = _run(tmp_path, _fence("hello\nnope"))
    assert [(f.claim.value, f.severity) for f in findings] == [("nope", Severity.INFO)]


def test_package_json_bin_string_uses_package_name(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", '{"name": "@scope/tool", "bin": "cli.js"}')
    _write(tmp_path, "cli.js")
    assert _run(tmp_path, _fence("tool run")) == []


def test_command_after_install_line_has_no_finding(tmp_path: Path) -> None:
    assert _run(tmp_path, _fence("npm install -g express-generator\nexpress myapp")) == []


def test_install_line_in_other_fence_does_not_count(tmp_path: Path) -> None:
    text = _fence("npm install -g express-generator") + "\n" + _fence("express myapp")
    assert [f.severity for f in _run(tmp_path, text)] == [Severity.INFO]


def test_tree_glyphs_and_powershell_env_not_claimed(tmp_path: Path) -> None:
    body = "│   file.txt\n├── src\n└── README\n$Env:APP_NAME = 'x'"
    assert _claims(tmp_path, _fence(body)) == []


def test_continuation_lines_are_joined(tmp_path: Path) -> None:
    claims = _claims(tmp_path, _fence("docker run \\\n  --rm \\\n  image\nmake"))
    assert [(c.value, c.context, c.location.line) for c in claims] == [
        ("docker", "docker run --rm image", 4),
        ("make", "make", 7),
    ]


def test_mkdocs_annotation_is_stripped(tmp_path: Path) -> None:
    claims = _claims(tmp_path, _fence("pip install foo  # (1)!\nmake test # (12)!"))
    assert [c.context for c in claims] == ["pip install foo", "make test"]
