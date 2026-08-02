"""Sanitized, read-only P34.5 Runner host capability probe.

This operator tool reads only ``docker info`` metadata.  It does not inspect
containers, images, environment variables, credentials or the repository
``.env``.  A failing result is a deployment Gate, not an invitation to weaken
the target isolation profile.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping


def _docker_info() -> Mapping[str, object]:
    completed = subprocess.run(
        ["docker", "info", "--format", "{{json .}}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("docker info did not return an object")
    return value


def main() -> int:
    try:
        info = _docker_info()
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, RuntimeError) as exc:
        print(json.dumps({"ready": False, "error": type(exc).__name__}, sort_keys=True))
        return 2

    security_options = tuple(
        str(item).lower() for item in info.get("SecurityOptions", []) if isinstance(item, str)
    )
    checks = {
        "linux": str(info.get("OSType", "")).lower() == "linux",
        "cgroup_v2": str(info.get("CgroupVersion", "")) == "2",
        "seccomp": any("seccomp" in item for item in security_options),
        "rootless_or_userns": any(
            marker in item
            for item in security_options
            for marker in ("rootless", "userns")
        ),
        "lsm": any(
            marker in item
            for item in security_options
            for marker in ("apparmor", "selinux")
        ),
    }
    missing = sorted(name for name, passed in checks.items() if not passed)
    report = {
        "architecture": str(info.get("Architecture", "unknown")),
        "checks": checks,
        "cgroup_driver": str(info.get("CgroupDriver", "unknown")),
        "driver": str(info.get("Driver", "unknown")),
        "missing_controls": missing,
        "operating_system": str(info.get("OperatingSystem", "unknown")),
        "ready": not missing,
        "security_options": sorted(security_options),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    sys.exit(main())
