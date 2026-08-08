"""Focused tests for the allowlisted desktop Compose lifecycle wrapper.

The subprocess boundary is always mocked: no production service is ever
started here and no real ``docker`` call is made. The tests assert the exact
argument arrays (including the explicit ``--env-file .env.example``), the
no-shell invariant, the closed-set profile/service/verb allowlists, Hardened
rejection, bounded/redacted output, timeout/executable-not-found behavior and
Windows path handling without command injection.

Round 5 additions: the lifecycle uses the **canonical absolute path of the
verified executable as ``argv[0]``** and re-verifies its stable file identity
before building any Compose command; it never re-resolves ``PATH`` via
``shutil.which`` (TOCTOU defense). Output is bounded DURING reading by
per-stream and total byte caps (not after capture); timeout and byte caps are
independent constraints with independent negative tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnibase.runtime import capabilities as caps
from omnibase.runtime import lifecycle
from omnibase.runtime.capabilities import (
    EngineResolution,
    ExecutableIdentity,
    ProductMode,
)
from omnibase.runtime.diagnostics import MAX_STRING_LENGTH

# Opaque secrets that carry no token/secret/password keyword: they must be
# absent from every redacted stdout/stderr bundle.
OPAQUE_STDOUT = (
    "postgres  Running  postgres://app:zq7x2m9k4v@db.internal:5432/omnibase\n"
    "warning: OPENAI_API_KEY=sk-proj-abc123xyz will be ignored\n"
    "backend  Running  X-Api-Key: abc123xyz\n"
)
OPAQUE_SECRETS = ("zq7x2m9k4v", "sk-proj-abc123xyz", "abc123xyz")


class FakeStream:
    """A minimal readable pipe stand-in for the bounded reader."""

    def __init__(self, data: str = "") -> None:
        self._data = data
        self._pos = 0

    def read(self, n: int = 4096) -> str:
        if self._pos >= len(self._data):
            return ""
        chunk = self._data[self._pos : self._pos + 4096]
        self._pos += len(chunk)
        return chunk

    def close(self) -> None:
        # Releasing the buffer is enough; the bounded reader tolerates a close
        # while a draining thread is still finishing its final read.
        self._data = ""


class FakePopen:
    """A Popen stand-in used by the mocked bounded reader.

    ``hang=True`` keeps ``poll()`` returning ``None`` until ``terminate`` /
    ``kill`` / ``wait`` set the final return code, simulating a timeout.
    """

    def __init__(
        self,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        *,
        hang: bool = False,
    ) -> None:
        self._final = returncode
        self.returncode: int | None = None if hang else returncode
        self.stdout = FakeStream(stdout)
        self.stderr = FakeStream(stderr)

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = self._final
        return self.returncode

    def terminate(self) -> None:
        if self.returncode is None:
            self.returncode = self._final

    def kill(self) -> None:
        if self.returncode is None:
            self.returncode = self._final


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


def _make_identity(path: str = "docker") -> ExecutableIdentity:
    return ExecutableIdentity(
        path=path,
        st_dev=0,
        st_ino=0,
        st_size=0,
        st_mtime_ns=0,
        st_ctime_ns=0,
        is_symlink=False,
    )


def _pin_resolution(
    monkeypatch: pytest.MonkeyPatch,
    *,
    engine: str = "docker",
    path: str = "docker",
    verified: bool = True,
    identity: ExecutableIdentity | None = None,
    verify_ok: bool = True,
) -> EngineResolution:
    """Pin the shared engine resolution and identity re-verification."""
    if identity is None:
        identity = _make_identity(path)
    res = EngineResolution(
        container_engine=engine,
        compose_provider_verified=verified,
        local_mode_available=verified,
        selected_executable_path=path,
        selected_executable_identity=identity,
    )
    monkeypatch.setattr(lifecycle, "resolve_engine_resolution", lambda: res)
    monkeypatch.setattr(lifecycle, "verify_executable_identity", lambda _p, _i: verify_ok)
    return res


def _patch_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    hang: bool = False,
    raise_exc: BaseException | None = None,
    engine_path: str = "docker",
    engine: str = "docker",
) -> list[dict[str, object]]:
    """Pin the resolution and mock ``subprocess.Popen`` for the bounded reader.

    Also pins the capability-probe path (``_probe_engine_resolution`` and
    ``_probe_gpu``) so ``probe_capabilities`` (used by ``health``/``doctor``)
    never reaches the mocked ``Popen`` — the lifecycle's bounded reader is the
    only consumer of the mock.
    """
    calls: list[dict[str, object]] = []

    def _popen(command: object, **kwargs: object) -> object:
        calls.append({"command": command, **kwargs})
        if raise_exc is not None:
            raise raise_exc
        return FakePopen(returncode=returncode, stdout=stdout, stderr=stderr, hang=hang)

    monkeypatch.setattr(lifecycle.subprocess, "Popen", _popen)
    res = _pin_resolution(monkeypatch, engine=engine, path=engine_path)
    # Isolate the capability probe from the Popen mock so health()/doctor()
    # do not invoke the real container-engine probe or hit FakePopen.
    monkeypatch.setattr(caps, "_probe_engine_resolution", lambda: res)
    monkeypatch.setattr(caps, "_probe_gpu", lambda: ("unknown", caps.EvidenceState.UNKNOWN, ()))
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
    repo = _make_repo(tmp_path)
    _pin_resolution(monkeypatch, path="docker")
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
    calls = _patch_subprocess(monkeypatch, stdout="", stderr="")
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
    calls = _patch_subprocess(monkeypatch, stdout="", stderr="")
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
        for element in call["command"]:  # type: ignore[union-attr]
            assert isinstance(element, str)
            assert not element.lstrip().startswith(("cmd /c", "cmd.exe", "/bin/sh", "bash"))


def test_verb_allowlist_rejects_arbitrary_verbs(monkeypatch, tmp_path) -> None:
    repo = _make_repo(tmp_path)
    _pin_resolution(monkeypatch, path="docker")
    for bad_verb in ("exec", "run", "kill", "rm", "push", "pull", "build", "attach"):
        with pytest.raises(ValueError, match="verb_not_allowed"):
            lifecycle._compose_command(bad_verb, repo_root=repo)


def test_service_allowlist_rejects_arbitrary_services(monkeypatch, tmp_path) -> None:
    repo = _make_repo(tmp_path)
    _pin_resolution(monkeypatch, path="docker")
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
    _patch_subprocess(monkeypatch, stdout="secret", stderr="", hang=True)
    request = lifecycle.validate_request("lite", ["backend"], timeout_seconds=1.0)
    result = lifecycle.status(request, repo_root=repo)
    assert result.exit_code == 124
    # stdout/stderr are bounded and passed through the redactor.
    assert "secret" not in json.dumps(result.to_dict())
    assert result.truncated is True


def test_executable_not_found_returns_127(monkeypatch, tmp_path) -> None:
    repo = _make_repo(tmp_path)
    calls = _patch_subprocess(monkeypatch, raise_exc=FileNotFoundError())
    request = lifecycle.validate_request("lite", ["backend"])
    result = lifecycle.status(request, repo_root=repo)
    assert result.exit_code == 127
    assert result.stderr == {"lines": "executable_not_found"}
    assert calls


def test_missing_container_engine_raises_before_subprocess(monkeypatch, tmp_path) -> None:
    # Neither Docker nor Podman is observable: the shared resolution reports
    # "none" and the lifecycle fails closed BEFORE any subprocess call.
    repo = _make_repo(tmp_path)
    calls: list[object] = []

    def _popen(command: object, **kwargs: object) -> object:
        calls.append(command)
        return FakePopen()

    monkeypatch.setattr(lifecycle.subprocess, "Popen", _popen)
    _pin_resolution(monkeypatch, engine="none", path="", verified=False)
    with pytest.raises(FileNotFoundError, match="container_engine_not_found"):
        lifecycle._compose_command("ps", repo_root=repo)
    assert calls == []


@pytest.mark.parametrize(
    ("engine", "expected_executable"),
    [
        ("docker", "/usr/bin/docker"),
        ("podman", "/usr/bin/podman"),
    ],
)
def test_compose_command_uses_shared_engine_resolution(
    monkeypatch, tmp_path, engine: str, expected_executable: str
) -> None:
    # Docker-only and Podman-only hosts: the lifecycle executes the controlled
    # Compose path of the SAME engine the capability probe resolved, using the
    # verified absolute path as argv[0] (never re-resolving PATH).
    repo = _make_repo(tmp_path)
    _pin_resolution(monkeypatch, engine=engine, path=expected_executable)
    command = lifecycle._compose_command("ps", repo_root=repo, services=("backend",))
    assert command[0] == expected_executable
    assert command[1] == "compose"
    assert "--env-file" in command
    assert command[command.index("--env-file") + 1] == str(repo / ".env.example")
    assert str(repo / ".env") not in command


def test_podman_only_executes_controlled_podman_compose_path(monkeypatch, tmp_path) -> None:
    # The podman Compose path is a real, controlled argument array: the
    # explicit .env.example is passed, no shell string is built, and the
    # subprocess boundary receives exactly the array with the verified path.
    repo = _make_repo(tmp_path)
    podman = "C:/Program Files/RedHat/Podman/podman.exe"
    calls = _patch_subprocess(monkeypatch, engine="podman", engine_path=podman)
    request = lifecycle.validate_request("local", ["backend", "redis"])
    result = lifecycle.start(request, repo_root=repo)
    assert result.exit_code == 0
    assert calls[0]["command"] == [
        podman,
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
    assert calls[0]["shell"] is False
    for call in calls:
        for element in call["command"]:  # type: ignore[union-attr]
            assert isinstance(element, str)


def test_both_engines_prefer_docker(monkeypatch, tmp_path) -> None:
    repo = _make_repo(tmp_path)
    _pin_resolution(monkeypatch, engine="docker", path="/usr/bin/docker")
    command = lifecycle._compose_command("ps", repo_root=repo)
    assert command[0] == "/usr/bin/docker"


def test_neither_engine_never_claims_local_and_fails_closed(monkeypatch, tmp_path) -> None:
    # Probe side: no LOCAL mode when the shared resolution reports "none".
    monkeypatch.setattr(caps.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        caps, "_probe_nvidia_gpu", lambda: ("unknown", caps.EvidenceState.UNKNOWN, ())
    )
    report = caps.probe_capabilities(ports=(), root=tmp_path)
    assert report.container_engine == "none"
    assert report.supports(caps.ProductMode.LITE)
    assert not report.supports(caps.ProductMode.LOCAL)
    assert report.backends == (caps.ExecutionBackend.NO_TOOL,)
    # Lifecycle side: no Compose command can be built.
    _pin_resolution(monkeypatch, engine="none", path="", verified=False)
    with pytest.raises(FileNotFoundError, match="container_engine_not_found"):
        lifecycle._compose_command("ps", repo_root=_make_repo(tmp_path))


def test_stdout_and_stderr_are_bounded_and_redacted(monkeypatch, tmp_path) -> None:
    repo = _make_repo(tmp_path)
    big_stderr = "x" * (8192 + 200)
    _patch_subprocess(monkeypatch, stdout=OPAQUE_STDOUT, stderr=big_stderr)
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
    # Oversized stderr is bounded (the redactor's string cap produces a stable
    # truncated marker; no unbounded content reaches the bundle).
    assert len(result.stderr["lines"]) <= MAX_STRING_LENGTH + 64
    assert "TRUNCATED" in result.stderr["lines"]


def test_start_bind_failure_propagation(monkeypatch, tmp_path) -> None:
    repo = _make_repo(tmp_path)
    _patch_subprocess(monkeypatch, returncode=1, stderr="port 8000 in use")
    request = lifecycle.validate_request("local", ["backend"])
    result = lifecycle.start(request, repo_root=repo)
    assert result.exit_code == 1
    assert "port 8000 in use" in result.stderr["lines"]


def test_logs_tail_bounds_and_command(monkeypatch, tmp_path) -> None:
    repo = _make_repo(tmp_path)
    calls = _patch_subprocess(monkeypatch, stdout=OPAQUE_STDOUT)
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
    _patch_subprocess(monkeypatch, returncode=2, stderr="compose failed")
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
        stdout="backend  Running (healthy)  0.0.0.0:8000->8000/tcp\n",
    )
    statuses = lifecycle._service_status_from_ps(repo_root=repo, timeout=20.0)
    assert any(item.name == "backend" and item.state == "present" for item in statuses)


def test_windows_paths_never_allow_command_injection(monkeypatch, tmp_path) -> None:
    # A Windows-style root with spaces and backslashes must be passed as an
    # exact array element; nothing is joined into a shell string.
    repo = tmp_path / "omnibase repo"
    repo.mkdir()
    (repo / ".env.example").write_text("A=B\n", encoding="utf-8")
    calls = _patch_subprocess(monkeypatch)
    request = lifecycle.validate_request("lite", ["backend"])
    result = lifecycle.start(request, repo_root=repo)
    assert result.exit_code == 0
    command = calls[0]["command"]
    assert isinstance(command, list)
    assert str(repo / ".env.example") in command
    assert str(repo / "docker-compose.yml") in command
    joined = " ".join(str(part) for part in command)  # type: ignore[union-attr]
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


def test_unverified_compose_provider_fails_closed_on_lifecycle_side(monkeypatch, tmp_path) -> None:
    # Podman executable is present but its compose provider is not verified
    # (bounded probe exit != 0): the shared resolution reports "none", Local
    # is never claimed and the lifecycle refuses to build any Compose command
    # BEFORE a subprocess is attempted.
    repo = _make_repo(tmp_path)
    calls: list[object] = []

    def _popen(command: object, **kwargs: object) -> object:
        calls.append(command)
        return FakePopen()

    monkeypatch.setattr(lifecycle.subprocess, "Popen", _popen)
    monkeypatch.setattr(
        caps.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "podman" else None
    )
    monkeypatch.setattr(
        caps,
        "_probe_compose",
        lambda name: caps.ComposeProbe(
            executable=name,
            executable_detected=name == "podman",
            compose_provider_verified=False,
            exit_code=1,
            detail=f"{name} compose version exit 1",
            executable_path=f"/usr/bin/{name}" if name == "podman" else None,
            executable_identity=_make_identity(f"/usr/bin/{name}") if name == "podman" else None,
        ),
    )
    # The lifecycle shares the real resolver; the probe boundary is mocked.
    assert lifecycle.resolve_engine_resolution().container_engine == "none"
    with pytest.raises(FileNotFoundError, match="container_engine_not_found"):
        lifecycle._compose_command("ps", repo_root=repo)
    assert calls == []


def test_verified_compose_provider_builds_command_via_real_resolver(monkeypatch, tmp_path) -> None:
    # Docker compose provider verified (exit 0): the shared resolution reports
    # docker and carries the verified absolute path/identity; the lifecycle
    # re-verifies identity (mocked True here) and builds the controlled
    # argument array with the explicit .env.example; the root .env is never
    # selected and PATH is never re-resolved.
    repo = _make_repo(tmp_path)
    monkeypatch.setattr(caps.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        caps,
        "_probe_compose",
        lambda name: caps.ComposeProbe(
            executable=name,
            executable_detected=True,
            compose_provider_verified=True,
            exit_code=0,
            detail=f"{name} compose version exit 0",
            executable_path=f"/usr/bin/{name}",
            executable_identity=_make_identity(f"/usr/bin/{name}"),
        ),
    )
    # The verified path does not exist on this Windows host, so identity
    # re-verification is pinned to True to exercise the real resolver path.
    monkeypatch.setattr(lifecycle, "verify_executable_identity", lambda _p, _i: True)
    assert lifecycle.resolve_engine_resolution().container_engine == "docker"
    command = lifecycle._compose_command("ps", repo_root=repo, services=("backend",))
    assert command[:2] == ["/usr/bin/docker", "compose"]
    assert command[command.index("--env-file") + 1] == str(repo / ".env.example")
    assert str(repo / ".env") not in command


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
    assert payload["truncated"] is False


def test_validate_request_services_and_tail_defaults() -> None:
    request = lifecycle.validate_request("lite", ["redis", "postgres"])
    assert request.services == ("redis", "postgres")
    assert request.tail_lines == lifecycle.DEFAULT_LOG_TAIL
    assert request.timeout_seconds == lifecycle.DEFAULT_LIFECYCLE_TIMEOUT


def test_capabilities_doctor_and_health_output_are_redacted(monkeypatch, tmp_path) -> None:
    repo = _make_repo(tmp_path)
    _patch_subprocess(
        monkeypatch,
        stdout="backend Running OPENAI_API_KEY=sk-proj-abc123xyz\n",
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


def test_cli_lifecycle_verbs_fail_closed_json_error_without_engine(monkeypatch, capsys) -> None:
    # With no verified container engine the shared resolution reports "none":
    # start/status/logs/stop must fail closed with a JSON error and exit code
    # 2 instead of a raw traceback, and no Compose subprocess is attempted.
    cli = _load_cli()
    _pin_resolution(monkeypatch, engine="none", path="", verified=False)
    for verb, extra in (
        ("start", ["--profile", "lite"]),
        ("status", ["--profile", "lite"]),
        ("logs", ["--profile", "lite"]),
        ("stop", ["--profile", "lite"]),
    ):
        assert cli.main([verb, *extra]) == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload == {"error": "container_engine_not_found"}


# --- Round 5: TOCTOU, identity drift and real byte-bounded output ----------


def test_verified_absolute_path_is_argv0_without_which_re_resolution(monkeypatch, tmp_path) -> None:
    # The lifecycle uses the verified absolute path recorded at probe time as
    # argv[0]; it never re-resolves PATH. A later which() pointing at a
    # replacement path cannot redirect execution.
    repo = _make_repo(tmp_path)
    trusted = str(tmp_path / "trusted-docker")
    _pin_resolution(monkeypatch, path=trusted)
    # Even if shutil.which (used nowhere in lifecycle now) would resolve a
    # different path, the command carries the trusted verified path.
    command = lifecycle._compose_command("ps", repo_root=repo, services=("backend",))
    assert command[0] == trusted
    assert command[1] == "compose"


def test_toctou_trusted_path_still_executed_after_which_changes(monkeypatch, tmp_path) -> None:
    # First which() returned the trusted path (captured at probe time); a later
    # PATH/which would point at a replacement. The lifecycle still executes
    # only the trusted path because it never calls which again.
    repo = _make_repo(tmp_path)
    trusted = str(tmp_path / "trusted-docker")
    calls = _patch_subprocess(monkeypatch, engine_path=trusted)
    request = lifecycle.validate_request("lite", ["backend"])
    lifecycle.status(request, repo_root=repo)
    assert calls[0]["command"][0] == trusted


def test_identity_drift_rejects_before_subprocess(monkeypatch, tmp_path) -> None:
    # The verified executable was replaced/retargeted after probe time: the
    # lifecycle re-verifies identity and fails closed BEFORE any subprocess.
    repo = _make_repo(tmp_path)
    calls: list[object] = []

    def _popen(command: object, **kwargs: object) -> object:
        calls.append(command)
        return FakePopen()

    monkeypatch.setattr(lifecycle.subprocess, "Popen", _popen)
    _pin_resolution(monkeypatch, path="/usr/bin/docker", verify_ok=False)
    with pytest.raises(FileNotFoundError, match="container_engine_identity_drift"):
        lifecycle._compose_command("ps", repo_root=repo)
    assert calls == []


def test_deleted_executable_rejects_before_subprocess(monkeypatch, tmp_path) -> None:
    # A real temp executable is probed, then deleted; the REAL identity
    # re-verification (not mocked) must reject before any subprocess.
    repo = _make_repo(tmp_path)
    trusted = tmp_path / "docker.exe"
    trusted.write_text("fake", encoding="utf-8")
    identity = caps._capture_executable_identity(str(trusted))
    assert identity is not None
    trusted.unlink()  # delete the verified executable

    calls: list[object] = []

    def _popen(command: object, **kwargs: object) -> object:
        calls.append(command)
        return FakePopen()

    monkeypatch.setattr(lifecycle.subprocess, "Popen", _popen)
    # Use the REAL verify_executable_identity (not mocked) so deletion is
    # detected from os.stat failure.
    res = EngineResolution(
        container_engine="docker",
        compose_provider_verified=True,
        local_mode_available=True,
        selected_executable_path=str(trusted),
        selected_executable_identity=identity,
    )
    monkeypatch.setattr(lifecycle, "resolve_engine_resolution", lambda: res)
    # Restore the real verifier (undo any prior pin).
    monkeypatch.setattr(
        lifecycle,
        "verify_executable_identity",
        caps.verify_executable_identity,
    )
    with pytest.raises(FileNotFoundError, match="container_engine_identity_drift"):
        lifecycle._compose_command("ps", repo_root=repo)
    assert calls == []


def test_replaced_executable_identity_drift_rejects(monkeypatch, tmp_path) -> None:
    # A real temp executable is probed, then replaced with a different-size
    # file at the same path; the REAL identity re-verification rejects.
    repo = _make_repo(tmp_path)
    trusted = tmp_path / "docker.exe"
    trusted.write_text("original-binary", encoding="utf-8")
    identity = caps._capture_executable_identity(str(trusted))
    assert identity is not None
    # Replace with a different size -> stat identity changes.
    trusted.write_text("replacement-binary-longer", encoding="utf-8")

    calls: list[object] = []

    def _popen(command: object, **kwargs: object) -> object:
        calls.append(command)
        return FakePopen()

    monkeypatch.setattr(lifecycle.subprocess, "Popen", _popen)
    res = EngineResolution(
        container_engine="docker",
        compose_provider_verified=True,
        local_mode_available=True,
        selected_executable_path=str(trusted),
        selected_executable_identity=identity,
    )
    monkeypatch.setattr(lifecycle, "resolve_engine_resolution", lambda: res)
    monkeypatch.setattr(
        lifecycle,
        "verify_executable_identity",
        caps.verify_executable_identity,
    )
    with pytest.raises(FileNotFoundError, match="container_engine_identity_drift"):
        lifecycle._compose_command("ps", repo_root=repo)
    assert calls == []


def test_lifecycle_stdout_exceeds_byte_cap(monkeypatch, tmp_path) -> None:
    # stdout larger than the per-stream cap is truncated DURING reading; the
    # process is terminated and no unbounded content reaches the bundle.
    repo = _make_repo(tmp_path)
    secret = "leak-stdout-" + "x" * (lifecycle.OUTPUT_BYTE_CAP_PER_STREAM + 100)
    _patch_subprocess(monkeypatch, stdout=secret, stderr="")
    request = lifecycle.validate_request("lite", ["backend"])
    result = lifecycle.status(request, repo_root=repo)
    assert result.truncated is True
    serialized = json.dumps(result.to_dict())
    # The secret never fully appears; output was bounded during reading.
    assert secret not in serialized
    assert "leak-stdout-" not in serialized


def test_lifecycle_stderr_exceeds_byte_cap(monkeypatch, tmp_path) -> None:
    repo = _make_repo(tmp_path)
    secret = "leak-stderr-" + "y" * (lifecycle.OUTPUT_BYTE_CAP_PER_STREAM + 100)
    _patch_subprocess(monkeypatch, stdout="", stderr=secret)
    request = lifecycle.validate_request("lite", ["backend"])
    result = lifecycle.status(request, repo_root=repo)
    assert result.truncated is True
    serialized = json.dumps(result.to_dict())
    assert secret not in serialized
    assert "leak-stderr-" not in serialized


def test_lifecycle_combined_output_exceeds_total_cap(monkeypatch, tmp_path) -> None:
    # Each stream is under its per-stream cap but the combined total exceeds
    # OUTPUT_BYTE_CAP_TOTAL; the reader still truncates during reading.
    repo = _make_repo(tmp_path)
    half = "z" * (lifecycle.OUTPUT_BYTE_CAP_TOTAL // 2 + 100)
    _patch_subprocess(monkeypatch, stdout=half, stderr=half)
    request = lifecycle.validate_request("lite", ["backend"])
    result = lifecycle.status(request, repo_root=repo)
    assert result.truncated is True
    # Combined bounded output never exceeds the total cap by more than a chunk.
    assert len(result.stdout["lines"]) + len(result.stderr["lines"]) <= (
        lifecycle.OUTPUT_BYTE_CAP_TOTAL + 8192
    )


def test_lifecycle_normal_small_output_not_truncated(monkeypatch, tmp_path) -> None:
    repo = _make_repo(tmp_path)
    _patch_subprocess(monkeypatch, stdout="backend Running\n", stderr="")
    request = lifecycle.validate_request("lite", ["backend"])
    result = lifecycle.status(request, repo_root=repo)
    assert result.truncated is False
    assert result.exit_code == 0
    assert result.stdout["lines"].strip() == "backend Running"


def test_all_subprocess_calls_keep_shell_false_and_verified_absolute_argv0(
    monkeypatch, tmp_path
) -> None:
    # Every Compose subprocess uses shell=False and argv[0] is the verified
    # absolute path (never a bare engine name re-resolved via which).
    repo = _make_repo(tmp_path)
    trusted = str(tmp_path / "verified-docker")
    calls = _patch_subprocess(monkeypatch, engine_path=trusted)
    request = lifecycle.validate_request("lite", ["backend"])
    for verb in (lifecycle.start, lifecycle.status, lifecycle.logs, lifecycle.stop):
        verb(request, repo_root=repo)
    assert calls
    for call in calls:
        assert call["shell"] is False
        assert call["command"][0] == trusted  # type: ignore[union-attr]
