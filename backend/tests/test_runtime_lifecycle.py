"""Focused tests for the allowlisted desktop Compose lifecycle wrapper.

The subprocess boundary is always mocked: no production service is ever
started here and no real ``docker`` call is made. The tests assert the exact
argument arrays (including the explicit ``--env-file .env.example``), the
no-shell invariant, the closed-set profile/service/verb allowlists, Hardened
rejection, bounded/redacted output, timeout/executable-not-found behavior and
Windows path handling without command injection.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from omnibase.runtime import lifecycle
from omnibase.runtime.capabilities import ProductMode
from omnibase.runtime.diagnostics import MAX_STRING_LENGTH

# Opaque secrets that carry no token/secret/password keyword: they must be
# absent from every redacted stdout/stderr bundle.
OPAQUE_STDOUT = (
    "postgres  Running  postgres://app:zq7x2m9k4v@db.internal:5432/omnibase\n"
    "warning: OPENAI_API_KEY=sk-proj-abc123xyz will be ignored\n"
    "backend  Running  X-Api-Key: abc123xyz\n"
)
OPAQUE_SECRETS = ("zq7x2m9k4v", "sk-proj-abc123xyz", "abc123xyz")


class FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _make_repo(tmp_path: Path, *, with_env_example: bool = True) -> Path:
    repo = tmp_path / "omnibase"
    repo.mkdir(parents=True, exist_ok=True)
    if with_env_example:
        (repo / ".env.example").write_text(
            "POSTGRES_PASSWORD=example-only\nOPENAI_API_KEY=example-only\n",
            encoding="utf-8",
        )
    (repo / ".env").write_text("POSTGRES_PASSWORD=REAL-SECRET\n", encoding="utf-8")
    return repo


def _patch_subprocess(monkeypatch: pytest.MonkeyPatch, fake_run: object) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def _run(command: object, **kwargs: object) -> object:
        calls.append({"command": command, **kwargs})
        result = fake_run
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(lifecycle.subprocess, "run", _run)
    monkeypatch.setattr(lifecycle.shutil, "which", lambda _name: "docker")
    return calls


@pytest.mark.parametrize(
    ("verb", "extra", "services"),
    [
        ("ps", [], ("backend",)),
        ("config", [], ()),
        ("down", [], ("backend", "redis")),
        ("stop", [], ("frontend",)),
        ("start", [], ("celery-worker",)),
        ("restart", [], ("minio",)),
    ],
)
def test_compose_command_exact_array_with_env_file_for_every_verb(
    monkeypatch, tmp_path, verb: str, extra: list[str], services: tuple[str, ...]
) -> None:
    monkeypatch.setattr(lifecycle.shutil, "which", lambda _name: "docker")
    repo = _make_repo(tmp_path)
    command = lifecycle._compose_command(verb, repo_root=repo, services=services, extra_args=extra)
    assert command == [
        "docker",
        "compose",
        "--env-file",
        str(repo / ".env.example"),
        "-f",
        str(repo / "docker-compose.yml"),
        verb,
        *extra,
        *services,
    ]
    # The env-file argument is the explicit .env.example; the root .env is never
    # passed and never selected.
    assert command[command.index("--env-file") + 1] == str(repo / ".env.example")
    assert str(repo / ".env") not in command


def test_start_uses_detached_up_with_explicit_env_file(monkeypatch, tmp_path) -> None:
    repo = _make_repo(tmp_path)
    calls = _patch_subprocess(monkeypatch, FakeCompleted())
    request = lifecycle.validate_request("local", ["backend", "redis"])
    result = lifecycle.start(request, repo_root=repo)
    assert result.exit_code == 0
    assert calls[0]["command"] == [
        "docker",
        "compose",
        "--env-file",
        str(repo / ".env.example"),
        "-f",
        str(repo / "docker-compose.yml"),
        "up",
        "-d",
        "backend",
        "redis",
    ]


def test_no_shell_invocation_ever(monkeypatch, tmp_path) -> None:
    repo = _make_repo(tmp_path)
    calls = _patch_subprocess(monkeypatch, FakeCompleted())
    for request in (
        lifecycle.validate_request("lite", ["backend"]),
        lifecycle.validate_request("local", ["backend", "postgres"]),
    ):
        lifecycle.start(request, repo_root=repo)
        lifecycle.status(request, repo_root=repo)
        lifecycle.logs(request, repo_root=repo)
        lifecycle.stop(request, repo_root=repo)
    assert calls
    for call in calls:
        assert call["shell"] is False
        assert isinstance(call["command"], list)
        # The command is an argument array; no element is a joined shell line.
        for element in call["command"]:
            assert isinstance(element, str)
            assert not element.lstrip().startswith(("cmd /c", "cmd.exe", "/bin/sh", "bash"))


def test_verb_allowlist_rejects_arbitrary_verbs(tmp_path) -> None:
    repo = _make_repo(tmp_path)
    for bad_verb in ("exec", "run", "kill", "rm", "push", "pull", "build", "attach"):
        with pytest.raises(ValueError, match="verb_not_allowed"):
            lifecycle._compose_command(bad_verb, repo_root=repo)


def test_service_allowlist_rejects_arbitrary_services(tmp_path) -> None:
    repo = _make_repo(tmp_path)
    with pytest.raises(ValueError, match="service_not_allowed"):
        lifecycle._compose_command("ps", repo_root=repo, services=("evil-service",))
    with pytest.raises(ValueError, match="service_not_allowed"):
        lifecycle.validate_request("lite", ["backend; rm -rf /"])
    with pytest.raises(ValueError, match="service_not_allowed"):
        lifecycle.LifecycleRequest(ProductMode.LITE, ("../../etc",))


def test_profile_allowlist_and_hardened_rejection(tmp_path) -> None:
    _make_repo(tmp_path)
    with pytest.raises(ValueError, match="hardened_mode_blocked"):
        lifecycle.validate_request("hardened", ["backend"])
    with pytest.raises(ValueError, match="profile_not_allowed"):
        lifecycle.validate_request("enterprise", ["backend"])
    with pytest.raises(ValueError, match="hardened_mode_blocked"):
        lifecycle.LifecycleRequest(ProductMode.HARDENED, ())
    # Approved profiles map to lite/local only.
    assert lifecycle.validate_request("lite", []).profile is ProductMode.LITE
    assert lifecycle.validate_request("local", []).profile is ProductMode.LOCAL


def test_timeout_returns_124_with_bounded_redacted_output(monkeypatch, tmp_path) -> None:
    repo = _make_repo(tmp_path)
    calls = _patch_subprocess(
        monkeypatch,
        subprocess.TimeoutExpired(cmd="docker", timeout=1.0, output="secret", stderr=""),
    )
    request = lifecycle.validate_request("lite", ["backend"])
    result = lifecycle.status(request, repo_root=repo)
    assert result.exit_code == 124
    assert calls[0]["timeout"] > 0
    # stdout/stderr are bounded and passed through the redactor.
    assert "secret" not in json.dumps(result.to_dict())


def test_executable_not_found_returns_127(monkeypatch, tmp_path) -> None:
    repo = _make_repo(tmp_path)
    calls = _patch_subprocess(monkeypatch, FileNotFoundError())
    request = lifecycle.validate_request("lite", ["backend"])
    result = lifecycle.status(request, repo_root=repo)
    assert result.exit_code == 127
    assert result.stderr == {"lines": "executable_not_found"}
    assert calls


def test_missing_docker_executable_raises_before_subprocess(monkeypatch, tmp_path) -> None:
    repo = _make_repo(tmp_path)
    monkeypatch.setattr(lifecycle.shutil, "which", lambda _name: None)
    with pytest.raises(FileNotFoundError, match="docker_executable_not_found"):
        lifecycle._compose_command("ps", repo_root=repo)


def test_stdout_and_stderr_are_bounded_and_redacted(monkeypatch, tmp_path) -> None:
    repo = _make_repo(tmp_path)
    big_stdout = "x" * (8192 + 200)
    fake = FakeCompleted(returncode=0, stdout=OPAQUE_STDOUT, stderr=big_stdout)
    _patch_subprocess(monkeypatch, fake)
    request = lifecycle.validate_request("lite", ["backend"])
    result = lifecycle.start(request, repo_root=repo)
    assert result.redacted is True
    assert result.exit_code == 0
    serialized = json.dumps(result.to_dict())
    for secret in OPAQUE_SECRETS:
        assert secret not in serialized
    # The compose line is structurally parsed; the assignment is redacted.
    assert "postgres://app:[REDACTED]@db.internal:5432/omnibase" in result.stdout["lines"]
    assert "OPENAI_API_KEY=[REDACTED]" in result.stdout["lines"]
    assert "X-Api-Key: [REDACTED]" in result.stdout["lines"]
    # Oversized stderr is bounded by the wrapper before redaction.
    assert len(result.stderr["lines"]) <= 8192 + 64
    assert "TRUNCATED" in result.stderr["lines"]


def test_start_bind_failure_propagation(monkeypatch, tmp_path) -> None:
    repo = _make_repo(tmp_path)
    _patch_subprocess(monkeypatch, FakeCompleted(returncode=1, stderr="port 8000 in use"))
    request = lifecycle.validate_request("local", ["backend"])
    result = lifecycle.start(request, repo_root=repo)
    assert result.exit_code == 1
    assert "port 8000 in use" in result.stderr["lines"]


def test_logs_tail_bounds_and_command(monkeypatch, tmp_path) -> None:
    repo = _make_repo(tmp_path)
    calls = _patch_subprocess(monkeypatch, FakeCompleted(stdout=OPAQUE_STDOUT))
    for bad_tail in (0, -5, 5001):
        with pytest.raises(ValueError, match="tail_lines_out_of_range"):
            lifecycle.validate_request("lite", ["backend"], tail_lines=bad_tail)
    request = lifecycle.validate_request("lite", ["backend"], tail_lines=300)
    result = lifecycle.logs(request, repo_root=repo)
    assert result.exit_code == 0
    assert calls[0]["command"][-4:] == ["logs", "--tail", "300", "backend"]
    for secret in OPAQUE_SECRETS:
        assert secret not in json.dumps(result.to_dict())


def test_status_failure_and_health_failure_behavior(monkeypatch, tmp_path) -> None:
    repo = _make_repo(tmp_path)
    _patch_subprocess(monkeypatch, FakeCompleted(returncode=2, stderr="compose failed"))
    request = lifecycle.validate_request("lite", ["backend"])
    result = lifecycle.status(request, repo_root=repo)
    assert result.exit_code == 2
    assert "compose failed" in result.stderr["lines"]

    # health() reports an unavailable compose_ps service and stays advisory.
    health_payload = lifecycle.health(repo_root=repo)
    assert health_payload["advisory"] is True
    assert any(
        service["name"] == "compose_ps" and service["state"] == "unavailable"
        for service in health_payload["services"]
    )
    assert "compose failed" not in json.dumps(health_payload)


def test_status_parses_present_services_from_ps_output(monkeypatch, tmp_path) -> None:
    repo = _make_repo(tmp_path)
    _patch_subprocess(
        monkeypatch,
        FakeCompleted(stdout="backend  Running (healthy)  0.0.0.0:8000->8000/tcp\n"),
    )
    statuses = lifecycle._service_status_from_ps(repo_root=repo, timeout=20.0)
    assert any(item.name == "backend" and item.state == "present" for item in statuses)


def test_windows_paths_never_allow_command_injection(monkeypatch, tmp_path) -> None:
    # A Windows-style root with spaces and backslashes must be passed as an
    # exact array element; nothing is joined into a shell string.
    repo = tmp_path / "omnibase repo"
    repo.mkdir()
    (repo / ".env.example").write_text("A=B\n", encoding="utf-8")
    calls = _patch_subprocess(monkeypatch, FakeCompleted())
    request = lifecycle.validate_request("lite", ["backend"])
    result = lifecycle.start(request, repo_root=repo)
    assert result.exit_code == 0
    command = calls[0]["command"]
    assert isinstance(command, list)
    assert str(repo / ".env.example") in command
    assert str(repo / "docker-compose.yml") in command
    joined = " ".join(str(part) for part in command)
    for dangerous in ("&", "|", ";", "`", "$(", "&&", "||", ">", "<"):
        # The path itself may legitimately contain characters; only the
        # joined invocation must not introduce shell metacharacter semantics.
        if dangerous in str(repo / ".env.example"):
            continue
        assert dangerous not in joined
    assert "shell" not in command


def test_root_env_is_never_selected(tmp_path) -> None:
    # Only .env.example exists: it is selected.
    repo = _make_repo(tmp_path / "with_example", with_env_example=True)
    env_file = lifecycle._resolve_compose_env_file(repo)
    assert env_file == repo / ".env.example"

    # .env.example missing: fail closed even though .env exists.
    repo2 = _make_repo(tmp_path / "without_example", with_env_example=False)
    with pytest.raises(FileNotFoundError, match="compose_env_file_missing"):
        lifecycle._resolve_compose_env_file(repo2)


def test_lifecycle_request_bounds() -> None:
    with pytest.raises(ValueError, match="timeout_out_of_range"):
        lifecycle.LifecycleRequest(ProductMode.LITE, (), timeout_seconds=0.5)
    with pytest.raises(ValueError, match="timeout_out_of_range"):
        lifecycle.LifecycleRequest(ProductMode.LITE, (), timeout_seconds=601.0)
    assert lifecycle.LifecycleRequest(ProductMode.LITE, ()).tail_lines == 200


def test_lifecycle_result_to_dict_and_redaction_flag() -> None:
    result = lifecycle.LifecycleResult(
        exit_code=0,
        stdout={"lines": "OK"},
        stderr={"lines": ""},
    )
    payload = result.to_dict()
    assert payload["exit_code"] == 0
    assert payload["stdout"] == {"lines": "OK"}
    assert payload["redacted"] is True


def test_validate_request_services_and_tail_defaults() -> None:
    request = lifecycle.validate_request("lite", ["redis", "postgres"])
    assert request.services == ("redis", "postgres")
    assert request.tail_lines == lifecycle.DEFAULT_LOG_TAIL
    assert request.timeout_seconds == lifecycle.DEFAULT_LIFECYCLE_TIMEOUT


def test_capabilities_doctor_and_health_output_are_redacted(monkeypatch, tmp_path) -> None:
    repo = _make_repo(tmp_path)
    _patch_subprocess(
        monkeypatch,
        FakeCompleted(stdout="backend Running OPENAI_API_KEY=sk-proj-abc123xyz\n"),
    )
    for payload in (
        lifecycle.doctor(repo_root=repo),
        lifecycle.capabilities(repo_root=repo),
        lifecycle.health(repo_root=repo),
    ):
        serialized = json.dumps(payload)
        assert "sk-proj-abc123xyz" not in serialized
    # The ps line never reaches the payload: _service_status_from_ps only
    # records service names, and the whole health payload still passes the
    # redactor (fail-closed even if a future detail field carried a secret).
    health_payload = lifecycle.health(repo_root=repo)
    assert "OPENAI_API_KEY" not in json.dumps(health_payload)


def test_long_string_redaction_stays_bounded() -> None:
    # LifecycleResult stores already-redacted bundles; build one the same way
    # the wrapper does (via redact_mapping) and check serialization bounds.
    from omnibase.runtime.diagnostics import redact_mapping

    payload = lifecycle.LifecycleResult(
        exit_code=0,
        stdout=redact_mapping({"lines": "A" * (MAX_STRING_LENGTH + 50)}),
        stderr={"lines": ""},
    )
    serialized = json.dumps(payload.to_dict())
    assert f"[TRUNCATED:{MAX_STRING_LENGTH + 50}]" in serialized
    assert "A" * (MAX_STRING_LENGTH + 1) not in serialized


def _load_cli() -> object:
    import importlib.util

    script = Path(__file__).resolve().parents[2] / "scripts" / "runtime" / "omnibase_desktop.py"
    spec = importlib.util.spec_from_file_location("omnibase_desktop_cli", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_capabilities_and_doctor_parse_ports(capsys) -> None:
    # Regression: the capabilities verb previously crashed with
    # "Namespace has no attribute 'port'".
    cli = _load_cli()
    for verb in ("doctor", "capabilities"):
        assert cli.main([verb]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert "modes" in payload


def test_cli_hardened_start_is_rejected(capsys) -> None:
    cli = _load_cli()
    # argparse rejects the hardened choice before any lifecycle call and exits
    # with status 2; the wrapper never reaches a Compose command.
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["start", "--profile", "hardened"])
    assert exc_info.value.code == 2
    stderr = capsys.readouterr().err
    assert "invalid choice" in stderr
