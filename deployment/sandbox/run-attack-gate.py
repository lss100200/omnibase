#!/usr/bin/python3
"""Run the fixed P34.5 Linux Runner attack matrix against the live VM service."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

LAUNCHER = "/usr/libexec/omnibase/omnibase-isolation-launcher"
PROBE_CONFIG = Path("/etc/omnibase-runner/probe.json")
EVIDENCE_ROOT = Path("/var/lib/omnibase-runner/evidence")
ZERO_DIGEST = "0" * 64


def _invoke(
    mode: str, payload: dict[str, Any], *, timeout: int = 180
) -> tuple[int, dict[str, Any]]:
    process = subprocess.run(
        [LAUNCHER, mode],
        input=json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin"},
    )
    try:
        response = json.loads(process.stdout)
    except json.JSONDecodeError:
        response = {
            "error": "invalid_launcher_json",
            "stderr_digest": hashlib.sha256(process.stderr).hexdigest(),
        }
    return process.returncode, response


def _payload(
    script: str,
    *,
    timeout_seconds: int = 8,
    max_output_bytes: int = 32768,
    pids: int = 24,
    writable_bytes: int = 4 * 1024 * 1024,
    inodes: int = 256,
) -> dict[str, Any]:
    operation_id = str(uuid4())
    runtime_instance_id = str(uuid4())
    return {
        "binding_digest": hashlib.sha256(
            f"binding:{operation_id}".encode()
        ).hexdigest(),
        "cgroup_name": runtime_instance_id,
        "command": {
            "argv": ["sh", "-c", script],
            "cwd": "workspace",
            "max_output_bytes": max_output_bytes,
            "timeout_seconds": timeout_seconds,
        },
        "isolation_attestation": hashlib.sha256(b"p34.5-vm-attestation").hexdigest(),
        "operation_id": operation_id,
        "runner_id": str(uuid4()),
        "runtime_handle": str(uuid4()),
        "runtime_instance_id": runtime_instance_id,
        "runtime_spec": {
            "isolation": {
                "allow_devices": False,
                "allow_host_mounts": False,
                "allow_runtime_socket": False,
                "drop_all_capabilities": True,
                "no_new_privileges": True,
                "read_only_root": True,
                "run_as_gid": 10000,
                "run_as_uid": 10000,
            },
            "limits": {
                "cpu_millis": 500,
                "inodes": inodes,
                "memory_bytes": 192 * 1024 * 1024,
                "output_bytes": max_output_bytes,
                "pids": pids,
                "wall_time_seconds": timeout_seconds,
                "writable_bytes": writable_bytes,
            },
            "network": {
                "allowed_service_ids": [],
                "direct_overlay": False,
                "mode": "deny_all",
            },
            "policy_digest": hashlib.sha256(b"p34.5-attack-policy").hexdigest(),
            "template_digest": hashlib.sha256(b"busybox-static-root").hexdigest(),
        },
        "schema_version": 1,
    }


def _read_evidence(operation_id: str) -> dict[str, Any]:
    return json.loads((EVIDENCE_ROOT / f"{operation_id}.json").read_text())


def _normal_case(
    case_id: str, script: str, probe: dict[str, Any], **options: Any
) -> dict[str, Any]:
    payload = _payload(script, **options)
    started = time.monotonic()
    code, receipt = _invoke("execute", payload)
    duration_ms = round((time.monotonic() - started) * 1000)
    passed = (
        code == 0
        and receipt.get("reason_code") == "runner_execution_succeeded"
        and receipt.get("exit_code") == 0
        and receipt.get("cgroup_empty") is True
        and receipt.get("namespaces_isolated") is True
        and receipt.get("truncated") is False
    )
    evidence: dict[str, Any] = {}
    if code == 0 and (EVIDENCE_ROOT / f"{payload['operation_id']}.json").is_file():
        evidence = _read_evidence(payload["operation_id"])
        metadata = evidence.get("metadata", {})
        child_namespaces = metadata.get("namespaces", {})
        runner_namespaces = probe["evidence"]["runner_namespaces"]
        isolation_pass = (
            metadata.get("cap_eff") == "0000000000000000"
            and metadata.get("no_new_privileges") == "1"
            and metadata.get("seccomp_mode") == "2"
            and metadata.get("root_read_only") is True
            and metadata.get("host_uid_mapped_nonroot") is True
            and metadata.get("apparmor") == "omnibase-runner (enforce)"
            and all(
                child_namespaces.get(name) != runner_namespaces.get(name)
                for name in ("user", "pid", "mnt", "net")
            )
        )
        passed = passed and isolation_pass
    else:
        isolation_pass = False
    return {
        "case": case_id,
        "duration_ms": duration_ms,
        "exit_code": receipt.get("exit_code"),
        "isolation_evidence": isolation_pass,
        "passed": passed,
        "reason_code": receipt.get("reason_code", receipt.get("error")),
        "receipt_evidence_digest": receipt.get("evidence_digest"),
    }


def _bounded_failure_case(
    case_id: str,
    script: str,
    probe: dict[str, Any],
    expected_reason: str | tuple[str, ...],
    **options: Any,
) -> dict[str, Any]:
    payload = _payload(script, **options)
    started = time.monotonic()
    code, receipt = _invoke("execute", payload)
    duration_ms = round((time.monotonic() - started) * 1000)
    evidence = _read_evidence(payload["operation_id"]) if code == 0 else {}
    metadata = evidence.get("metadata", {})
    child_namespaces = metadata.get("namespaces", {})
    runner_namespaces = probe["evidence"]["runner_namespaces"]
    isolation_pass = (
        metadata.get("cap_eff") == "0000000000000000"
        and metadata.get("seccomp_mode") == "2"
        and metadata.get("root_read_only") is True
        and all(
            child_namespaces.get(name) != runner_namespaces.get(name)
            for name in ("user", "pid", "mnt", "net")
        )
    )
    expected_reasons = (
        (expected_reason,) if isinstance(expected_reason, str) else expected_reason
    )
    passed = (
        code == 0
        and receipt.get("reason_code") in expected_reasons
        and receipt.get("cgroup_empty") is True
        and receipt.get("namespaces_isolated") is True
        and isolation_pass
        and duration_ms < 30000
    )
    if "runner_output_limit_exceeded" in expected_reasons:
        passed = passed and receipt.get("truncated") is True
    if case_id == "PROC-01":
        pids_events = evidence.get("cgroup_pids_events", [])
        pids_limit_hit = any(
            line.startswith("max ") and int(line.partition(" ")[2]) > 0
            for line in pids_events
        )
        passed = passed and pids_limit_hit
    return {
        "case": case_id,
        "duration_ms": duration_ms,
        "exit_code": receipt.get("exit_code"),
        "isolation_evidence": isolation_pass,
        "passed": passed,
        "reason_code": receipt.get("reason_code", receipt.get("error")),
        "receipt_evidence_digest": receipt.get("evidence_digest"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() == 0:
        print("attack gate must run as the non-root Runner account", file=sys.stderr)
        return 2
    probe_code, probe = _invoke("probe", json.loads(PROBE_CONFIG.read_text()))
    if probe_code != 0 or probe.get("ready") is not True:
        report = {"gate": "failed", "probe": probe, "results": []}
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        return 2

    results = [
        _normal_case(
            "RUN-03",
            'test -z "$DATABASE_URL$REDIS_URL$MINIO_ENDPOINT$JWT_SECRET"; '
            "! nc -z -w 1 127.0.0.1 5432; ! nc -z -w 1 127.0.0.1 6379; ! nc -z -w 1 127.0.0.1 9000",
            probe,
        ),
    ]

    malformed = _payload("true")
    malformed["environment"] = {"JWT_SECRET": "synthetic-attack-marker"}
    malformed_code, malformed_response = _invoke("execute", malformed)
    results.append(
        {
            "case": "RUN-04",
            "duration_ms": 0,
            "exit_code": malformed_code,
            "isolation_evidence": True,
            "passed": malformed_code != 0 and malformed_response.get("ready") is False,
            "reason_code": "payload_with_environment_rejected",
            "receipt_evidence_digest": None,
        }
    )
    results.extend(
        [
            _normal_case(
                "FS-01",
                "! test -e /etc/hostname; ! test -e /var/lib/omnibase-runner; ! test -e /home/omnibase-runner",
                probe,
            ),
            _normal_case(
                "FS-02",
                "ln -s /etc/hostname escape; ! cat escape; ! ln /bin/busybox hard; ! mknod device c 1 3",
                probe,
            ),
            _normal_case(
                "FS-03",
                "i=0; while touch f$i 2>/dev/null; do i=$((i+1)); test $i -lt 1000 || exit 9; done; test $i -lt 1000",
                probe,
                inodes=128,
            ),
            _normal_case(
                "NET-01",
                "! nc -z -w 1 127.0.0.1 22; ! nc -z -w 1 10.0.0.1 443; ! nc -z -w 1 169.254.169.254 80",
                probe,
            ),
            _normal_case(
                "NET-02",
                "! test -e /etc/resolv.conf; ! nslookup example.com; ! nslookup metadata.google.internal",
                probe,
            ),
            _bounded_failure_case(
                "PROC-01",
                "while :; do sh -c 'sleep 30' & done",
                probe,
                ("runner_execution_failed", "runner_execution_timed_out"),
                timeout_seconds=2,
                pids=16,
            ),
            _bounded_failure_case(
                "PROC-02",
                "yes X",
                probe,
                "runner_output_limit_exceeded",
                timeout_seconds=5,
                max_output_bytes=4096,
            ),
            _normal_case(
                "HOST-01",
                "! test -e /.env; ! test -e /root/.env; ! test -e /home/omnibase-runner/.ssh; "
                "! test -e /run/docker.sock; ! test -e /run/podman/podman.sock; ! test -e /sys/fs/cgroup",
                probe,
            ),
            _normal_case(
                "CROSS-01",
                "! test -e /var/lib/omnibase-runner/runtimes; ! test -e /run/omnibase-runner/control.sock; "
                'test "$(find /workspace -mindepth 1 -maxdepth 1 | wc -l)" -eq 0',
                probe,
            ),
        ]
    )
    post_probe_code, post_probe = _invoke("probe", json.loads(PROBE_CONFIG.read_text()))
    service_healthy = post_probe_code == 0 and post_probe.get("ready") is True
    passed = all(item["passed"] for item in results) and service_healthy
    report = {
        "attack_matrix": [item["case"] for item in results],
        "gate": "passed" if passed else "failed",
        "host": {
            "apparmor": probe["evidence"]["apparmor"],
            "cgroup_controllers": probe["evidence"]["cgroup_controllers"],
            "kernel": os.uname().release,
            "runner_id": probe["evidence"]["runner_id"],
            "service_uid": probe["evidence"]["service_uid"],
        },
        "post_gate_service_healthy": service_healthy,
        "probe_evidence_digest": probe["evidence_digest"],
        "results": results,
        "schema_version": 1,
    }
    args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
