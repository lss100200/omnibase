"""Run the fixed P34.5A4 protocol/runtime attack-test slice."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_A4_TESTS = (
    "backend/tests/test_p34_5_sandbox_a4_runtime.py",
    "backend/tests/test_p34_5_sandbox_a4_transport.py",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--probe-config",
        type=Path,
        help="Optional absolute JSON config for the read-only Linux isolation probe.",
    )
    args = parser.parse_args()
    environment = {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(_REPO_ROOT / "backend" / "src"),
    }
    tests = subprocess.run(
        [sys.executable, "-m", "pytest", *_A4_TESTS, "-q"],
        cwd=_REPO_ROOT,
        env=environment,
        check=False,
        timeout=180,
    )
    if tests.returncode != 0:
        return tests.returncode
    if args.probe_config is None:
        return 0
    if not args.probe_config.is_absolute():
        parser.error("--probe-config must be absolute")
    probe = subprocess.run(
        [
            sys.executable,
            str(_REPO_ROOT / "scripts" / "sandbox" / "probe_linux_runtime.py"),
            "--config",
            str(args.probe_config),
        ],
        cwd=_REPO_ROOT,
        env=environment,
        check=False,
        timeout=60,
    )
    return probe.returncode


if __name__ == "__main__":
    raise SystemExit(main())
