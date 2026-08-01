"""Local Linux isolation probe for the independent P34.5A4 Runner.

The probe reads only explicitly configured runtime-control paths plus the
Linux proc/cgroup facts required by the isolation contract.  It never reads
environment variables, repository configuration, workload files or secrets.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

from omnibase.sandbox.contracts import SandboxRejected, utc_now
from omnibase.sandbox.runner import RunnerIsolationProfile, RunnerPlatform

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_NAMESPACE_ID_RE = re.compile(r"^[0-9]+:[0-9]+$")
_REQUIRED_CONTROLLERS = frozenset({"cpu", "memory", "pids"})


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path, *, maximum_bytes: int = 32 * 1024 * 1024) -> str:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > maximum_bytes:
        raise ValueError("attested file is not a bounded regular file")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_bounded(path: Path, *, maximum_bytes: int = 64 * 1024) -> str:
    with path.open("rb") as stream:
        payload = stream.read(maximum_bytes + 1)
    if len(payload) > maximum_bytes:
        raise ValueError("probe source exceeds its safe size limit")
    return payload.decode("utf-8", errors="strict")


def _proc_status() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in _read_bounded(Path("/proc/self/status")).splitlines():
        key, separator, value = line.partition(":")
        if separator:
            values[key] = value.strip()
    return values


def _namespace_identity(path: Path) -> str:
    info = path.stat()
    return f"{info.st_dev}:{info.st_ino}"


def _host_namespace_identity(path: Path) -> str:
    """Read a trusted PID1 namespace handle or root-owned identity snapshot."""
    if path.parent == Path("/proc/1/ns") and path.is_symlink():
        return _namespace_identity(path)
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or path.is_symlink()
        or info.st_uid != 0
        or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise ValueError("host namespace reference is not trusted")
    identity = _read_bounded(path, maximum_bytes=128).strip()
    if _NAMESPACE_ID_RE.fullmatch(identity) is None:
        raise ValueError("host namespace reference identity is invalid")
    return identity


def _secure_file(path: Path, *, owner_uid: int) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(info.st_mode)
        and not path.is_symlink()
        and info.st_uid == owner_uid
        and info.st_mode & (stat.S_IWGRP | stat.S_IWOTH) == 0
    )


def _private_directory(path: Path, *, owner_uid: int) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(info.st_mode)
        and not path.is_symlink()
        and info.st_uid == owner_uid
        and info.st_mode & (stat.S_IRWXG | stat.S_IRWXO) == 0
    )


def _trusted_directory(path: Path, *, owner_uid: int) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(info.st_mode)
        and not path.is_symlink()
        and info.st_uid == owner_uid
        and info.st_mode & (stat.S_IWGRP | stat.S_IWOTH) == 0
    )


@dataclass(frozen=True, slots=True)
class LinuxRuntimeAttestation:
    runner_id: UUID
    isolation_profile_digest: str
    launcher_digest: str
    runner_root_digest: str
    verified_at: datetime
    expires_at: datetime
    evidence_digest: str
    ready_for_untrusted_execution: bool
    missing_controls: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.runner_id, UUID):
            raise TypeError("runner_id must be UUID")
        for name, value in (
            ("isolation_profile_digest", self.isolation_profile_digest),
            ("launcher_digest", self.launcher_digest),
            ("runner_root_digest", self.runner_root_digest),
            ("evidence_digest", self.evidence_digest),
        ):
            if _SHA256_RE.fullmatch(value) is None:
                raise ValueError(f"{name} must be sha256")
        if self.verified_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("runtime attestation timestamps must be timezone-aware")
        if self.expires_at <= self.verified_at:
            raise ValueError("runtime attestation is already expired")
        if not isinstance(self.missing_controls, tuple):
            raise TypeError("missing_controls must be an immutable tuple")
        if self.ready_for_untrusted_execution == bool(self.missing_controls):
            raise ValueError("runtime readiness and missing controls disagree")

    def verify(
        self,
        *,
        runner_id: UUID,
        isolation_profile: RunnerIsolationProfile,
        now: datetime,
    ) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("runtime attestation clock must be timezone-aware")
        if (
            not self.ready_for_untrusted_execution
            or self.runner_id != runner_id
            or self.isolation_profile_digest != isolation_profile.digest()
            or self.verified_at > now
            or self.expires_at <= now
        ):
            raise SandboxRejected("sandbox_linux_isolation_attestation_rejected")


class SystemLinuxRuntimeProbe:
    """Evaluate one explicitly configured local Linux Runner deployment."""

    def __init__(
        self,
        *,
        runner_id: UUID,
        launcher_path: Path,
        expected_launcher_digest: str,
        runner_root: Path,
        cgroup_root: Path,
        host_namespace_root: Path,
        seccomp_profile_path: Path,
        lsm_profile_path: Path,
        lsm_profile_name: str,
        require_root_owned_launcher: bool = True,
        ttl_seconds: int = 30,
        clock=utc_now,
    ) -> None:
        paths = (
            launcher_path,
            runner_root,
            cgroup_root,
            host_namespace_root,
            seccomp_profile_path,
            lsm_profile_path,
        )
        if any(not path.is_absolute() for path in paths):
            raise ValueError("Linux runtime probe paths must be absolute")
        if runner_root == Path("/") or cgroup_root == Path("/"):
            raise ValueError("Linux runtime roots cannot be filesystem root")
        if host_namespace_root == Path("/"):
            raise ValueError("host namespace reference root cannot be filesystem root")
        if _SHA256_RE.fullmatch(expected_launcher_digest) is None:
            raise ValueError("expected_launcher_digest must be sha256")
        if not lsm_profile_name or len(lsm_profile_name) > 128:
            raise ValueError("lsm_profile_name is invalid")
        if isinstance(ttl_seconds, bool) or ttl_seconds < 5 or ttl_seconds > 300:
            raise ValueError("runtime probe TTL is outside the safe range")
        self._runner_id = runner_id
        self._launcher_path = launcher_path
        self._expected_launcher_digest = expected_launcher_digest
        self._runner_root = runner_root
        self._cgroup_root = cgroup_root
        self._host_namespace_root = host_namespace_root
        self._seccomp_profile_path = seccomp_profile_path
        self._lsm_profile_path = lsm_profile_path
        self._lsm_profile_name = lsm_profile_name
        self._require_root_owned_launcher = require_root_owned_launcher
        self._ttl_seconds = ttl_seconds
        self._clock = clock

    def probe(self, isolation_profile: RunnerIsolationProfile) -> LinuxRuntimeAttestation:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("runtime probe clock must be timezone-aware")
        missing: set[str] = set()
        launcher_digest = "0" * 64
        runner_root_digest = "0" * 64

        if platform.system() != "Linux" or isolation_profile.platform is not RunnerPlatform.LINUX:
            missing.add("linux")
        else:
            effective_uid = os.geteuid()
            launcher_owner = 0 if self._require_root_owned_launcher else effective_uid
            if not _secure_file(self._launcher_path, owner_uid=launcher_owner):
                missing.add("trusted_launcher")
            else:
                launcher_digest = _sha256_file(self._launcher_path)
                if launcher_digest != self._expected_launcher_digest:
                    missing.add("launcher_digest")
            if effective_uid == 0:
                missing.add("non_root_runner")
            if not _private_directory(self._runner_root, owner_uid=effective_uid):
                missing.add("private_runner_root")
            else:
                root_info = self._runner_root.lstat()
                runner_root_digest = _digest(
                    {
                        "device": root_info.st_dev,
                        "inode": root_info.st_ino,
                        "mode": stat.S_IMODE(root_info.st_mode),
                        "owner": root_info.st_uid,
                    }
                )
            if not _trusted_directory(self._host_namespace_root, owner_uid=0):
                missing.add("trusted_host_namespace_reference")
            namespace_evidence = self._probe_namespaces(missing)
            cgroup_evidence = self._probe_cgroup(missing)
            self._probe_seccomp_and_lsm(missing, isolation_profile)
        if platform.system() != "Linux":
            namespace_evidence = {}
            cgroup_evidence = {}

        checks = {
            "isolation_profile": isolation_profile.digest(),
            "cgroup_evidence": cgroup_evidence,
            "launcher_digest": launcher_digest,
            "missing_controls": sorted(missing),
            "namespace_evidence": namespace_evidence,
            "runner_id": str(self._runner_id),
            "runner_root_digest": runner_root_digest,
        }
        return LinuxRuntimeAttestation(
            runner_id=self._runner_id,
            isolation_profile_digest=isolation_profile.digest(),
            launcher_digest=launcher_digest,
            runner_root_digest=runner_root_digest,
            verified_at=now,
            expires_at=now + timedelta(seconds=self._ttl_seconds),
            evidence_digest=_digest(checks),
            ready_for_untrusted_execution=not missing,
            missing_controls=tuple(sorted(missing)),
        )

    def _probe_namespaces(self, missing: set[str]) -> dict[str, dict[str, str]]:
        namespace_paths = {
            "user_namespace": "user",
            "pid_namespace": "pid",
            "mount_namespace": "mnt",
            "network_namespace": "net",
        }
        evidence: dict[str, dict[str, str]] = {}
        for name, namespace in namespace_paths.items():
            runner_path = Path(f"/proc/self/ns/{namespace}")
            init_path = self._host_namespace_root / namespace
            try:
                runner_identity = _namespace_identity(runner_path)
                init_identity = _host_namespace_identity(init_path)
            except (OSError, UnicodeError, ValueError):
                missing.add(name)
                continue
            evidence[name] = {
                "init": init_identity,
                "runner": runner_identity,
            }
            if runner_identity == init_identity:
                missing.add(f"isolated_{name}")
        try:
            max_user_namespaces = int(
                _read_bounded(Path("/proc/sys/user/max_user_namespaces")).strip()
            )
        except (OSError, UnicodeError, ValueError):
            max_user_namespaces = 0
        if max_user_namespaces < 1:
            missing.add("user_namespace")
        return evidence

    def _probe_cgroup(self, missing: set[str]) -> dict[str, int]:
        try:
            cgroup_root = self._cgroup_root.resolve(strict=True)
        except OSError:
            cgroup_root = self._cgroup_root
        if not cgroup_root.is_relative_to(Path("/sys/fs/cgroup")) or self._cgroup_root.is_symlink():
            missing.add("delegated_cgroup")
        controllers_path = Path("/sys/fs/cgroup/cgroup.controllers")
        try:
            controllers = frozenset(_read_bounded(controllers_path).split())
        except (OSError, UnicodeError, ValueError):
            controllers = frozenset()
        if not _REQUIRED_CONTROLLERS.issubset(controllers):
            missing.add("cgroup_v2_controllers")
        if not (self._cgroup_root / "cgroup.kill").is_file():
            missing.add("cgroup_kill")
        if not os.access(self._cgroup_root, os.W_OK | os.X_OK):
            missing.add("delegated_cgroup")
        try:
            info = cgroup_root.lstat()
        except OSError:
            return {}
        return {
            "device": info.st_dev,
            "inode": info.st_ino,
            "mode": stat.S_IMODE(info.st_mode),
            "owner": info.st_uid,
        }

    def _probe_seccomp_and_lsm(
        self,
        missing: set[str],
        isolation_profile: RunnerIsolationProfile,
    ) -> None:
        try:
            status = _proc_status()
        except (OSError, UnicodeError, ValueError):
            status = {}
        if status.get("NoNewPrivs") != "1":
            missing.add("no_new_privileges")
        if status.get("Seccomp") != "2":
            missing.add("seccomp_enforcing")
        if not _secure_file(self._seccomp_profile_path, owner_uid=0):
            seccomp_digest = ""
            missing.add("trusted_seccomp_profile")
        else:
            try:
                seccomp_digest = _sha256_file(self._seccomp_profile_path)
            except (OSError, ValueError):
                seccomp_digest = ""
        if seccomp_digest != isolation_profile.seccomp_profile_digest:
            missing.add("seccomp_profile_digest")
        if not _secure_file(self._lsm_profile_path, owner_uid=0):
            lsm_digest = ""
            missing.add("trusted_lsm_profile")
        else:
            try:
                lsm_digest = _sha256_file(self._lsm_profile_path)
            except (OSError, ValueError):
                lsm_digest = ""
        try:
            current_lsm = _read_bounded(Path("/proc/self/attr/current")).strip()
        except (OSError, UnicodeError, ValueError):
            current_lsm = ""
        if lsm_digest != isolation_profile.lsm_profile_digest:
            missing.add("lsm_profile_digest")
        if self._lsm_profile_name not in current_lsm or "unconfined" in current_lsm:
            missing.add("lsm_enforcing")


__all__ = [
    "LinuxRuntimeAttestation",
    "SystemLinuxRuntimeProbe",
]
