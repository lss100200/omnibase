#!/usr/bin/env python3
"""Adversarial P34.7 joint-evidence bundle forger (review negative-proof tool).

This tool deliberately fabricates a *complete* P34.7 evidence bundle from
scratch: every file, every sidecar manifest, every SHA-256 and every
cross-binding is produced here, with no real execution behind it.  It exists so
the review can prove that the joint gate can never return ``passed`` from
operator-authored bytes, even when every hash matches.

Modes:

* default: a complete **unsigned** bundle (all hashes match, no detached
  signatures) -- the verifier must report ``blocked/not_proven``.
* ``--keyfile``: sign every canonical evidence file with the supplied
  per-role Ed25519 keys -- still ``blocked/not_proven`` because the trust
  policy is not an approved anchor and the chain is self-authored.
* ``--forged-signatures``: write random signature bytes so verification must
  fail closed instead of trusting the sidecar.

The tool never reads the root ``.env``, never touches a database, never opens
a network connection and never executes hostile code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))

from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa: E402

SCHEMA = "omnibase.p34-7.hardened-joint-evidence.v2"
COMPONENT_SCHEMA = "omnibase.p34-7.component-evidence.v1"
RECEIPT_SCHEMA = "omnibase.p34-7.command-receipt.v1"
POSTURE_SCHEMA = "omnibase.p34-7.posture-measurement.v1"
ATTACK_SCHEMA = "omnibase.p34-7.attack-matrix.v1"
CLEANUP_SCHEMA = "omnibase.p34-7.cleanup-inventory.v1"
SEAL_SCHEMA = "omnibase.p34-7.evidence-seal.v1"

REQUIRED_COMMANDS = (
    "core_runner",
    "runner_broker",
    "runner_gateway",
    "broker_gateway",
    "overlay_data_plane",
    "recovery_sla",
)
COMMAND_PRODUCER = {
    "core_runner": "core",
    "runner_broker": "runner",
    "runner_gateway": "runner",
    "broker_gateway": "broker",
    "overlay_data_plane": "overlay",
    "recovery_sla": "recovery_sla",
}
REQUIRED_COMPONENTS = (
    "core",
    "runner",
    "broker",
    "gateway",
    "overlay",
    "recovery_sla",
)
REQUIRED_PEERS: dict[str, tuple[str, ...]] = {
    "core": ("runner",),
    "runner": ("core", "broker", "gateway"),
    "broker": ("runner", "gateway"),
    "gateway": ("runner", "broker"),
    "overlay": ("runner",),
    "recovery_sla": ("core",),
}
REQUIRED_ATTACKS = (
    "node_compromise",
    "credential_theft",
    "revocation_replay",
    "derp_failover",
    "cross_component_replay",
)
REQUIRED_CLEANUP_KEYS = (
    "containers",
    "networks",
    "processes",
    "volumes",
    "databases",
    "test_identities",
)
ROLES = (*REQUIRED_COMPONENTS, "sealer")


def _canonical(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _write_canonical(run: Path, path: Path, value: object) -> dict[str, object]:
    raw = _canonical(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {
        "path": path.relative_to(run).as_posix(),
        "size": len(raw),
        "sha256": _digest(raw),
    }


def _write_raw(run: Path, path: Path, raw: bytes) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {
        "path": path.relative_to(run).as_posix(),
        "size": len(raw),
        "sha256": _digest(raw),
    }


def generate_keypair() -> tuple[str, str]:
    private = ed25519.Ed25519PrivateKey.generate()
    private_hex = private.private_bytes_raw().hex()
    public_hex = private.public_key().public_bytes_raw().hex()
    return private_hex, public_hex


def generate_keyfile() -> dict[str, dict[str, str]]:
    return {
        role: dict(zip(("private", "public"), generate_keypair())) for role in ROLES
    }


def write_keyfile(path: Path, keys: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keys, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_keyfile(path: Path) -> dict[str, dict[str, str]]:
    keys = json.loads(path.read_text(encoding="utf-8"))
    missing = [role for role in ROLES if role not in keys]
    if missing:
        raise SystemExit(f"keyfile is missing roles: {', '.join(missing)}")
    return keys


def _signature_ref(
    run: Path,
    evidence_path: Path,
    private_hex: str,
    forged: bool,
) -> dict[str, object]:
    raw = evidence_path.read_bytes()
    if forged:
        signature = os.urandom(64)
    else:
        private = ed25519.Ed25519PrivateKey.from_private_bytes(
            bytes.fromhex(private_hex)
        )
        signature = private.sign(raw)
    relative = f"signatures/{evidence_path.relative_to(run).as_posix()}"
    return _write_raw(run, run / relative, signature)


def forge_bundle(
    output_dir: Path,
    *,
    run_id: str = "forge-run-0001",
    source_commit: str | None = None,
    source_tree: str | None = None,
    repository: str = "https://github.com/lss100200/omnibase.git",
    keys: dict[str, dict[str, str]] | None = None,
    forged_signatures: bool = False,
    executable_content: dict[str, bytes] | None = None,
    gateway_certificate: dict[str, object] | None = None,
) -> dict[str, object]:
    """Fabricate a complete P34.7 bundle; returns the evidence payload."""
    run = output_dir.resolve()
    run.mkdir(parents=True, exist_ok=True)
    commit = source_commit or ("a" * 64)
    tree = source_tree or ("b" * 64)

    def now_iso() -> str:
        return (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )

    started_at = "2026-08-07T00:00:00Z"

    def manifest(name: str, content: bytes) -> dict[str, object]:
        entry = _write_raw(run, run / f"{name}.txt", content)
        files = [entry]
        raw = _digest(_canonical(files))
        return {"raw_sha256": raw, "files": files}

    source_manifest = manifest("source", b"forged-source-bytes")
    artifact_manifest = manifest("artifact", b"forged-artifact-bytes")

    receipts: dict[str, dict[str, object]] = {}
    receipt_digests: dict[str, str] = {}
    receipt_executables: dict[str, tuple[str, str]] = {}
    for index, name in enumerate(REQUIRED_COMMANDS):
        content = (executable_content or {}).get(name, name.encode())
        exe_ref = _write_raw(run, run / f"bin/{name}", content)
        stdout_ref = _write_raw(run, run / f"out/{name}.out", f"stdout-{name}".encode())
        stderr_ref = _write_raw(run, run / f"out/{name}.err", b"")
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "command": name,
            "order": index,
            "run_id": run_id,
            "producer": COMMAND_PRODUCER[name],
            "executable": {"path": exe_ref["path"], "sha256": exe_ref["sha256"]},
            "argv": [f"/run/omnibase/bin/{name}", "--probe"],
            "working_directory": "/run/omnibase",
            "env_names": ["PATH", "OMNIBASE_RUN_ID"],
            "started_at": f"2026-08-07T00:0{index}:00Z",
            "ended_at": f"2026-08-07T00:0{index}:30Z",
            "timeout_seconds": 60,
            "exit_code": 0,
            "stdout": stdout_ref,
            "stderr": stderr_ref,
        }
        receipt_ref = _write_canonical(run, run / f"receipts/{name}.json", receipt)
        signature_ref = None
        if keys is not None:
            signature_ref = _signature_ref(
                run,
                run / f"receipts/{name}.json",
                keys[COMMAND_PRODUCER[name]]["private"],
                forged_signatures,
            )
        receipts[name] = {
            "order": index,
            "receipt": receipt_ref,
            "signature": signature_ref,
        }
        receipt_digests[name] = str(receipt_ref["sha256"])
        receipt_executables[name] = (str(exe_ref["path"]), str(exe_ref["sha256"]))

    posture = {
        "schema": POSTURE_SCHEMA,
        "producer": "core",
        "run_id": run_id,
        "measured": True,
        "measured_at": started_at,
        "measurement_source": "process_config",
        "production_runtime_activated": False,
        "hostile_code_executed": False,
        "root_env_accessed": False,
        "business_database_accessed": False,
        "business_database_migrated": False,
        "host": {"os": "ubuntu", "kernel": "6.8.0", "arch": "x86_64"},
    }
    posture_ref = _write_canonical(run, run / "measurements/posture.json", posture)
    posture_sig = None
    if keys is not None:
        posture_sig = _signature_ref(
            run,
            run / "measurements/posture.json",
            keys["core"]["private"],
            forged_signatures,
        )
    posture_entry = {"evidence": posture_ref, "signature": posture_sig}
    posture_digest = str(posture_ref["sha256"])

    attack = {
        "schema": ATTACK_SCHEMA,
        "producer": "runner",
        "run_id": run_id,
        "executed_at": started_at,
        "results": {attack_name: "rejected" for attack_name in REQUIRED_ATTACKS},
        "inventory": [
            {
                "attack_id": attack_name,
                "outcome": "rejected",
                "attempted_at": started_at,
                "evidence_digest": _digest(attack_name.encode()),
            }
            for attack_name in REQUIRED_ATTACKS
        ],
    }
    attack_ref = _write_canonical(run, run / "attack/attack-matrix.json", attack)
    attack_sig = None
    if keys is not None:
        attack_sig = _signature_ref(
            run,
            run / "attack/attack-matrix.json",
            keys["runner"]["private"],
            forged_signatures,
        )
    attack_entry = {"evidence": attack_ref, "signature": attack_sig}
    attack_digest = str(attack_ref["sha256"])

    cleanup = {
        "schema": CLEANUP_SCHEMA,
        "producer": "sealer",
        "run_id": run_id,
        "completed_at": "2026-08-07T00:06:00Z",
        "counts": {key: 0 for key in REQUIRED_CLEANUP_KEYS},
        "inventory": [],
    }
    cleanup_ref = _write_canonical(run, run / "cleanup/cleanup-inventory.json", cleanup)
    cleanup_sig = None
    if keys is not None:
        cleanup_sig = _signature_ref(
            run,
            run / "cleanup/cleanup-inventory.json",
            keys["sealer"]["private"],
            forged_signatures,
        )
    cleanup_entry = {"evidence": cleanup_ref, "signature": cleanup_sig}
    cleanup_digest = str(cleanup_ref["sha256"])

    components: dict[str, dict[str, object]] = {}
    component_digests: dict[str, str] = {}
    for name in REQUIRED_COMPONENTS:
        owned = [
            command for command, owner in COMMAND_PRODUCER.items() if owner == name
        ]
        gateway_field: dict[str, object] = {}
        if name == "gateway":
            certificate = gateway_certificate or {
                "public_fingerprint": _digest(b"cert"),
                "issuer": _digest(b"issuer"),
                "san": "workload.gateway.omnibase",
                "valid_from": "2020-01-01T00:00:00Z",
                "valid_until": "2099-01-01T00:00:00Z",
                "revoked": False,
            }
            gateway_field["gateway"] = {
                "certificate": certificate,
                "replay": {"replayed": False, "sequence": 1},
            }
        evidence = {
            "schema": COMPONENT_SCHEMA,
            "producer": name,
            "run_id": run_id,
            "source_commit": commit,
            "source_tree": tree,
            "source_manifest_sha256": source_manifest["raw_sha256"],
            "artifact_manifest_sha256": artifact_manifest["raw_sha256"],
            "component_identity": {"kind": "sha256", "value": _digest(name.encode())},
            "peer_identities": {
                peer: _digest(peer.encode()) for peer in REQUIRED_PEERS[name]
            },
            "receipts": {command: receipt_digests[command] for command in owned},
            "executables": [
                {
                    "path": receipt_executables[command][0],
                    "sha256": receipt_executables[command][1],
                }
                for command in owned
            ],
            "measurements": {"posture_sha256": posture_digest},
            "results": {
                "attack_matrix_sha256": attack_digest,
                "cleanup_sha256": cleanup_digest,
            },
            "host": {"os": "ubuntu", "kernel": "6.8.0", "arch": "x86_64"},
            **gateway_field,
        }
        evidence_ref = _write_canonical(run, run / f"components/{name}.json", evidence)
        evidence_sig = None
        if keys is not None:
            evidence_sig = _signature_ref(
                run,
                run / f"components/{name}.json",
                keys[name]["private"],
                forged_signatures,
            )
        components[name] = {"evidence": evidence_ref, "signature": evidence_sig}
        component_digests[name] = str(evidence_ref["sha256"])

    gates = {
        "agent_runtime_enabled": False,
        "agent_planner_enabled": False,
        "multi_agent_enabled": False,
    }
    binding = {
        "schema": SEAL_SCHEMA,
        "producer": "sealer",
        "run_id": run_id,
        "source_commit": commit,
        "source_tree": tree,
        "source_manifest_sha256": source_manifest["raw_sha256"],
        "artifact_manifest_sha256": artifact_manifest["raw_sha256"],
        "commands": dict(sorted(receipt_digests.items())),
        "components": dict(sorted(component_digests.items())),
        "posture_measurement": posture_digest,
        "attack_matrix": attack_digest,
        "cleanup": cleanup_digest,
        "migration_head": "0012",
        "feature_gates": dict(sorted(gates.items())),
    }
    binding_bytes = _canonical(binding)
    seal_sig = None
    if keys is not None:
        if forged_signatures:
            seal_sig = _write_raw(run, run / "signatures/seal.sig", os.urandom(64))
        else:
            private = ed25519.Ed25519PrivateKey.from_private_bytes(
                bytes.fromhex(keys["sealer"]["private"])
            )
            seal_sig = _write_raw(
                run, run / "signatures/seal.sig", private.sign(binding_bytes)
            )

    payload: dict[str, object] = {
        "schema": SCHEMA,
        "schema_version": "2",
        "run_id": run_id,
        "environment": "production",
        "disposable": False,
        "provenance": {
            "source_commit": commit,
            "source_tree": tree,
            "dirty": False,
            "repository": repository,
        },
        "source_manifest": source_manifest,
        "artifact_manifest": artifact_manifest,
        "commands": receipts,
        "components": components,
        "measurements": {"posture": posture_entry},
        "migration_head": "0012",
        "feature_gates": gates,
        "attack_matrix": attack_entry,
        "cleanup": cleanup_entry,
        "evidence_seal": {
            "producer": "sealer",
            "binding_sha256": _digest(binding_bytes),
            "signature": seal_sig,
        },
    }
    _write_canonical(run, run / "evidence.json", payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="bundle directory")
    parser.add_argument(
        "--keyfile", type=Path, help="per-role Ed25519 keyfile (signs the bundle)"
    )
    parser.add_argument(
        "--forged-signatures",
        action="store_true",
        help="write random signature bytes instead of real signatures",
    )
    parser.add_argument("--run-id", default="forge-run-0001")
    args = parser.parse_args()
    keys = load_keyfile(args.keyfile) if args.keyfile is not None else None
    payload = forge_bundle(
        args.output,
        run_id=args.run_id,
        keys=keys,
        forged_signatures=args.forged_signatures,
    )
    print(
        json.dumps(
            {"forged": True, "unsigned": keys is None, "payload": payload}, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
