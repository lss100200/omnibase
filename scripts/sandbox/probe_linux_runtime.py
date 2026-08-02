"""Probe one explicitly configured P34.5A4 Linux Runner deployment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND_SRC = _REPO_ROOT / "backend" / "src"
sys.path.insert(0, str(_BACKEND_SRC))

from omnibase.sandbox.runner import RunnerIsolationProfile, RunnerPlatform  # noqa: E402
from omnibase.sandbox.runtime_probe import SystemLinuxRuntimeProbe  # noqa: E402

_CONFIG_KEYS = {
    "cgroup_root",
    "expected_launcher_digest",
    "host_namespace_root",
    "launcher_path",
    "lsm_profile_digest",
    "lsm_profile_name",
    "lsm_profile_path",
    "runner_id",
    "runner_root",
    "seccomp_profile_digest",
    "seccomp_profile_path",
}


def _load_config(path: Path) -> dict[str, str]:
    if not path.is_absolute():
        raise ValueError("probe config path must be absolute")
    payload = path.read_bytes()
    if len(payload) > 64 * 1024:
        raise ValueError("probe config exceeds its safe size limit")
    value = json.loads(payload)
    if not isinstance(value, dict) or set(value) != _CONFIG_KEYS:
        raise ValueError("probe config shape is invalid")
    if any(not isinstance(item, str) for item in value.values()):
        raise ValueError("probe config values must be strings")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        config = _load_config(args.config)
        profile = RunnerIsolationProfile(
            platform=RunnerPlatform.LINUX,
            cgroup_v2=True,
            user_namespace=True,
            pid_namespace=True,
            mount_namespace=True,
            network_namespace=True,
            seccomp_profile_digest=config["seccomp_profile_digest"],
            lsm_profile_digest=config["lsm_profile_digest"],
            bounded_kill_seconds=10,
        )
        report = SystemLinuxRuntimeProbe(
            runner_id=UUID(config["runner_id"]),
            launcher_path=Path(config["launcher_path"]),
            expected_launcher_digest=config["expected_launcher_digest"],
            runner_root=Path(config["runner_root"]),
            cgroup_root=Path(config["cgroup_root"]),
            host_namespace_root=Path(config["host_namespace_root"]),
            seccomp_profile_path=Path(config["seccomp_profile_path"]),
            lsm_profile_path=Path(config["lsm_profile_path"]),
            lsm_profile_name=config["lsm_profile_name"],
        ).probe(profile)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ready": False, "error": type(exc).__name__}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "evidence_digest": report.evidence_digest,
                "expires_at": report.expires_at.isoformat(),
                "isolation_profile_digest": report.isolation_profile_digest,
                "launcher_digest": report.launcher_digest,
                "missing_controls": list(report.missing_controls),
                "ready": report.ready_for_untrusted_execution,
                "runner_id": str(report.runner_id),
                "runner_root_digest": report.runner_root_digest,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if report.ready_for_untrusted_execution else 2


if __name__ == "__main__":
    raise SystemExit(main())
