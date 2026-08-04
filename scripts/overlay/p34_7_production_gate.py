"""Admit independently collected P34.7 production Overlay evidence.

This harness never mutates a provider or connects to a member node. Operators run
the documented probes on the real Linux members and submit the resulting evidence
bundle here. Missing external infrastructure therefore remains blocked/not_proven
instead of being silently replaced by Docker, WSL, a test double, or fake evidence.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from p34_7_overlay_common import (
    ProductionGateError,
    canonical_bytes,
    require,
    require_exact_keys,
    require_git_commit,
    require_sha256,
    safe_json_file,
    safe_regular_file,
    sha256_bytes,
    sha256_file,
    validate_credential_reference,
    validate_https_endpoint,
)

CONFIG_SCHEMA = "omnibase.p34-7.overlay-production-config.v1"
EVIDENCE_SCHEMA = "omnibase.p34-7.overlay-production-evidence.v1"
REPORT_SCHEMA = "omnibase.p34-7.overlay-production-report.v1"
SLA_REPORT_SCHEMA = "omnibase.p34-7.overlay-sla-report.v1"
REQUIRED_FAULT_SCENARIOS = {
    "broker_restart_pending_no_replay",
    "forced_derp_relay",
    "gateway_timeout_unknown_no_replay",
    "member_offline_reconnect",
    "network_partition_fail_closed",
    "node_credential_theft_after_revoke",
    "node_daemon_restart",
    "node_revoke_propagation",
    "runner_forced_kill_cleanup",
}
ALLOWED_NODE_ROLES = {"broker", "member", "node_daemon", "runner"}
SOURCE_EXACT_PATHS = {
    ".gitattributes",
    "backend/pyproject.toml",
    "backend/uv.lock",
}
SOURCE_GLOBS = (
    "backend/src/omnibase/sandbox/**/*.py",
    "backend/src/omnibase/workspaces/overlay_adapters/**/*.py",
    "deployment/network-broker/**/*",
    "deployment/overlay/**/*",
    "deployment/sandbox/**/*",
    "scripts/network-broker/**/*",
    "scripts/overlay/**/*",
    "scripts/sandbox/**/*",
)


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise ProductionGateError("Git provenance is unavailable")
    return completed.stdout.strip()


def build_source_scope(root: Path) -> dict[str, Any]:
    """Fingerprint the production Overlay/Runner/Broker Gate source bytes."""

    paths = set(SOURCE_EXACT_PATHS)
    for pattern in SOURCE_GLOBS:
        paths.update(
            path.relative_to(root).as_posix()
            for path in root.glob(pattern)
            if path.is_file() and "__pycache__" not in path.parts
        )
    missing = sorted(path for path in SOURCE_EXACT_PATHS if not (root / path).is_file())
    require(not missing, f"production Gate source paths are missing: {missing}")
    files = []
    for relative in sorted(paths):
        path = root / relative
        require(not path.is_symlink(), "production Gate source symlinks are forbidden")
        files.append(
            {
                "path": relative,
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
        )
    return {
        "file_count": len(files),
        "files": files,
        "source_scope_sha256": sha256_bytes(canonical_bytes(files)),
    }


def _validate_member(member: dict[str, Any], *, index: int) -> dict[str, Any]:
    context = f"member_nodes[{index}]"
    require_exact_keys(
        member,
        allowed={
            "credential_reference",
            "attestation_public_key_file",
            "attestation_public_key_sha256",
            "daemon_identity_digest",
            "host_id",
            "machine_identity_digest",
            "node_daemon_endpoint",
            "node_id",
            "placeholder",
            "roles",
            "sandbox_overlay_peer",
            "target_linux",
        },
        context=context,
    )
    for field in ("host_id", "node_id"):
        require(
            isinstance(member.get(field), str) and bool(member[field].strip()),
            f"{context}.{field} is required",
        )
    require_sha256(member.get("machine_identity_digest"), context=f"{context} machine digest")
    require_sha256(member.get("daemon_identity_digest"), context=f"{context} daemon digest")
    require_sha256(
        member.get("attestation_public_key_sha256"),
        context=f"{context} attestation public key digest",
    )
    require(
        isinstance(member.get("attestation_public_key_file"), str)
        and bool(member["attestation_public_key_file"]),
        f"{context} attestation public key path is required",
    )
    validate_https_endpoint(member.get("node_daemon_endpoint"), context=f"{context} endpoint")
    validate_credential_reference(
        member.get("credential_reference"), context=f"{context} credential reference"
    )
    roles = member.get("roles")
    require(
        isinstance(roles, list)
        and roles
        and set(roles) <= ALLOWED_NODE_ROLES
        and {"member", "node_daemon"} <= set(roles),
        f"{context} roles are invalid",
    )
    require(member.get("target_linux") is True, f"{context} must be a target Linux member")
    require(
        member.get("sandbox_overlay_peer") is False,
        f"{context} must not make a Sandbox an Overlay peer",
    )
    require(
        isinstance(member.get("placeholder"), bool),
        f"{context} placeholder flag missing",
    )
    return member


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate production topology without claiming that it exists."""

    require_exact_keys(
        config,
        allowed={
            "control_plane",
            "environment",
            "member_nodes",
            "required_fault_scenarios",
            "schema",
        },
        context="production Overlay config",
    )
    require(
        config.get("schema") == CONFIG_SCHEMA,
        "production Overlay config schema unsupported",
    )
    require(config.get("environment") == "production", "Overlay config is not production")
    members = config.get("member_nodes")
    require(
        isinstance(members, list) and len(members) >= 2,
        "at least two members are required",
    )
    validated_members = [
        _validate_member(value, index=index) for index, value in enumerate(members)
    ]
    require(
        len({str(member["node_id"]) for member in validated_members}) == len(validated_members),
        "member node identities must be unique",
    )
    require(
        len({str(member["host_id"]) for member in validated_members}) == len(validated_members),
        "member nodes must use independent hosts",
    )
    require(
        len({str(member["machine_identity_digest"]) for member in validated_members})
        == len(validated_members),
        "member nodes must use independent machine identities",
    )
    require(
        len({str(member["daemon_identity_digest"]) for member in validated_members})
        == len(validated_members),
        "member nodes must use independent Node Daemon identities",
    )

    control_plane = config.get("control_plane")
    require(isinstance(control_plane, dict), "control_plane is required")
    require_exact_keys(
        control_plane,
        allowed={"derp_hosts", "headscale_endpoint", "provider"},
        context="control_plane",
    )
    require(
        control_plane.get("provider") == "headscale",
        "only sealed Headscale wiring is admitted",
    )
    validate_https_endpoint(control_plane.get("headscale_endpoint"), context="Headscale endpoint")
    derp_hosts = control_plane.get("derp_hosts")
    require(
        isinstance(derp_hosts, list) and derp_hosts,
        "at least one DERP host is required",
    )
    member_hosts = {str(member["host_id"]) for member in validated_members}
    require(
        all(isinstance(host, str) and host and host not in member_hosts for host in derp_hosts),
        "DERP must be independently hosted from member nodes",
    )
    require(len(set(derp_hosts)) == len(derp_hosts), "DERP host identities must be unique")

    scenarios = config.get("required_fault_scenarios")
    require(
        isinstance(scenarios, list) and set(scenarios) == REQUIRED_FAULT_SCENARIOS,
        "required fault scenario set was weakened or expanded without a schema revision",
    )
    return config


def _require_gate_result(
    value: object,
    *,
    expected_total: int,
    expected_commit: str,
    expected_source_scope_sha256: str,
    context: str,
) -> dict[str, Any]:
    require(isinstance(value, dict), f"{context} evidence missing")
    require_exact_keys(
        value,
        allowed={
            "evidence_sha256",
            "passed",
            "source_git_commit",
            "source_scope_sha256",
            "status",
            "total",
        },
        context=context,
    )
    require(value.get("status") == "passed", f"{context} did not pass")
    require(value.get("passed") == expected_total, f"{context} passed count drifted")
    require(value.get("total") == expected_total, f"{context} total was weakened")
    require(
        value.get("source_git_commit") == expected_commit,
        f"{context} source commit drifted",
    )
    require(
        value.get("source_scope_sha256") == expected_source_scope_sha256,
        f"{context} source scope drifted",
    )
    require_sha256(value.get("evidence_sha256"), context=f"{context} evidence hash invalid")
    return value


def _validate_evidence_nodes(
    *, config: dict[str, Any], evidence_nodes: object
) -> list[dict[str, Any]]:
    require(isinstance(evidence_nodes, list), "production member evidence is missing")
    configured = {
        str(member["node_id"]): member
        for member in config["member_nodes"]
        if not member["placeholder"]
    }
    require(
        len(configured) >= 2,
        "placeholder member topology cannot produce production PASS",
    )
    require(
        len(evidence_nodes) == len(configured),
        "member evidence count does not match topology",
    )
    decoded: list[dict[str, Any]] = []
    for index, item in enumerate(evidence_nodes):
        require(isinstance(item, dict), "member evidence item invalid")
        require_exact_keys(
            item,
            allowed={
                "daemon_identity_digest",
                "host_id",
                "machine_identity_digest",
                "node_daemon_independent",
                "node_daemon_test_double",
                "node_id",
                "real_member",
                "target_linux",
            },
            context=f"evidence member {index}",
        )
        node_id = str(item.get("node_id"))
        require(node_id in configured, "evidence contains an unconfigured member node")
        expected = configured[node_id]
        for field in ("host_id", "machine_identity_digest", "daemon_identity_digest"):
            require(item.get(field) == expected[field], f"member evidence {field} drifted")
        require(item.get("target_linux") is True, "member evidence is not target Linux")
        require(
            item.get("real_member") is True,
            "member evidence is not a real Overlay member",
        )
        require(
            item.get("node_daemon_independent") is True,
            "member Node Daemon is not independently attested",
        )
        require(
            item.get("node_daemon_test_double") is False,
            "test-double Node Daemon is forbidden",
        )
        decoded.append(item)
    return decoded


def verify_member_signatures(*, config: dict[str, Any], evidence: dict[str, Any]) -> None:
    """Verify both member attestations over the exact canonical evidence claims."""

    signatures = evidence.get("signatures")
    require(isinstance(signatures, list), "member evidence signatures are missing")
    configured = {
        str(member["node_id"]): member
        for member in config["member_nodes"]
        if not member["placeholder"]
    }
    require(len(signatures) == len(configured), "each real member must sign the evidence")
    unsigned = dict(evidence)
    unsigned.pop("signatures", None)
    payload = canonical_bytes(unsigned)
    payload_sha256 = sha256_bytes(payload)
    seen: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="omnibase-p347-attestation-") as temporary:
        payload_path = Path(temporary) / "evidence-payload.json"
        payload_path.write_bytes(payload)
        for index, signature in enumerate(signatures):
            require(isinstance(signature, dict), "member evidence signature is invalid")
            require_exact_keys(
                signature,
                allowed={
                    "algorithm",
                    "node_id",
                    "signature_file",
                    "signature_file_sha256",
                    "signed_payload_sha256",
                },
                context=f"member signature {index}",
            )
            node_id = str(signature.get("node_id"))
            require(
                node_id in configured and node_id not in seen,
                "member signature identity invalid",
            )
            seen.add(node_id)
            require(
                signature.get("algorithm") == "ed25519",
                "only Ed25519 evidence is accepted",
            )
            require(
                signature.get("signed_payload_sha256") == payload_sha256,
                "member signature payload binding drifted",
            )
            public_key = safe_regular_file(
                Path(str(configured[node_id]["attestation_public_key_file"])),
                context="attestation public key",
            )
            require(
                sha256_file(public_key) == configured[node_id]["attestation_public_key_sha256"],
                "attestation public key digest drifted",
            )
            signature_file = safe_regular_file(
                Path(str(signature.get("signature_file"))), context="member signature"
            )
            require(
                sha256_file(signature_file)
                == require_sha256(
                    signature.get("signature_file_sha256"),
                    context="member signature digest invalid",
                ),
                "member signature file digest drifted",
            )
            completed = subprocess.run(
                [
                    "openssl",
                    "pkeyutl",
                    "-verify",
                    "-pubin",
                    "-inkey",
                    str(public_key),
                    "-rawin",
                    "-in",
                    str(payload_path),
                    "-sigfile",
                    str(signature_file),
                ],
                check=False,
                capture_output=True,
            )
            require(
                completed.returncode == 0,
                "member evidence signature verification failed",
            )
    require(seen == set(configured), "member evidence signature set is incomplete")


def _append_overlay_vetoes(gates: dict[str, Any], vetoes: list[str]) -> None:
    member = gates.get("member_data_plane")
    compromise = gates.get("node_compromise")
    cleanup = gates.get("cleanup")
    secret_scan = gates.get("secret_scan")
    require(isinstance(member, dict), "member data-plane evidence missing")
    require(isinstance(compromise, dict), "node compromise evidence missing")
    require(isinstance(cleanup, dict), "cleanup evidence missing")
    require(isinstance(secret_scan, dict), "secret scan evidence missing")
    if member.get("sandbox_is_overlay_member") is not False:
        vetoes.append("sandbox_is_overlay_member")
    if member.get("direct_infrastructure_routes") != []:
        vetoes.append("direct_infrastructure_route_present")
    if member.get("logical_service_only") is not True:
        vetoes.append("physical_overlay_endpoint_exposed")
    if compromise.get("revoked_node_rejected") is not True:
        vetoes.append("revoked_node_still_accepted")
    if compromise.get("stolen_credential_rejected") is not True:
        vetoes.append("stolen_credential_still_accepted")
    if compromise.get("stale_lease_rejected") is not True:
        vetoes.append("stale_lease_still_accepted")
    if compromise.get("stale_fencing_rejected") is not True:
        vetoes.append("stale_fencing_still_accepted")
    if compromise.get("ambiguous_operation_replayed") is not False:
        vetoes.append("ambiguous_operation_replayed")
    if compromise.get("rejoin_uses_new_identity") is not True:
        vetoes.append("revoked_identity_reused_on_rejoin")
    if any(cleanup.get(name) != 0 for name in ("containers", "networks", "processes", "volumes")):
        vetoes.append("production_gate_cleanup_residue")
    if secret_scan.get("findings") != []:
        vetoes.append("secret_or_credential_exposure")


def evaluate_evidence(
    *,
    config: dict[str, Any],
    evidence: dict[str, Any],
    sla_report: dict[str, Any],
    current_commit: str,
    current_source_scope_sha256: str,
) -> dict[str, Any]:
    """Evaluate independent production evidence and preserve all unsafe vetoes."""

    require_exact_keys(
        evidence,
        allowed={
            "disposable",
            "environment",
            "gates",
            "member_nodes",
            "run_id",
            "schema",
            "signatures",
            "source_git_commit",
            "source_git_dirty",
            "source_scope_sha256",
            "topology_sha256",
        },
        context="production Overlay evidence",
    )
    require(
        evidence.get("schema") == EVIDENCE_SCHEMA,
        "production evidence schema unsupported",
    )
    require(evidence.get("environment") == "production", "evidence is not production")
    require(
        evidence.get("disposable") is False,
        "disposable evidence cannot prove production",
    )
    evidence_commit = require_git_commit(
        evidence.get("source_git_commit"), context="evidence source commit is invalid"
    )
    require(evidence.get("source_git_dirty") is False, "scored evidence used dirty source")
    require(
        evidence.get("source_scope_sha256") == current_source_scope_sha256,
        "evidence production source bytes are stale",
    )
    require(
        evidence.get("topology_sha256") == sha256_bytes(canonical_bytes(config)),
        "evidence topology binding drifted",
    )
    require(
        isinstance(evidence.get("run_id"), str) and evidence["run_id"],
        "run identity missing",
    )
    _validate_evidence_nodes(config=config, evidence_nodes=evidence.get("member_nodes"))
    gates = evidence.get("gates")
    require(isinstance(gates, dict), "production Gate evidence is missing")
    require_exact_keys(
        gates,
        allowed={
            "broker_rounds",
            "cleanup",
            "derp",
            "fault_scenarios",
            "member_data_plane",
            "node_compromise",
            "runner_a4_current_source",
            "secret_scan",
        },
        context="production Gate evidence",
    )

    _require_gate_result(
        gates.get("runner_a4_current_source"),
        expected_total=12,
        expected_commit=evidence_commit,
        expected_source_scope_sha256=current_source_scope_sha256,
        context="current-source A4 Gate",
    )
    broker_rounds = gates.get("broker_rounds")
    require(
        isinstance(broker_rounds, list) and len(broker_rounds) >= 2,
        "two Broker rounds required",
    )
    for index, round_result in enumerate(broker_rounds[:2], start=1):
        _require_gate_result(
            round_result,
            expected_total=26,
            expected_commit=evidence_commit,
            expected_source_scope_sha256=current_source_scope_sha256,
            context=f"Network Broker round {index}",
        )

    member = gates.get("member_data_plane")
    derp = gates.get("derp")
    compromise = gates.get("node_compromise")
    fault_scenarios = gates.get("fault_scenarios")
    require(
        isinstance(member, dict) and member.get("status") == "passed",
        "member path not proven",
    )
    require(member.get("real_two_member_path") is True, "two-member data plane not proven")
    require(isinstance(derp, dict) and derp.get("status") == "passed", "DERP not proven")
    require(derp.get("forced_relay") is True, "DERP evidence did not force relay")
    require(
        derp.get("direct_path_disabled") is True,
        "direct path remained available in DERP Gate",
    )
    require(
        isinstance(compromise, dict) and compromise.get("status") == "passed",
        "node-compromise Gate not proven",
    )
    require(
        isinstance(fault_scenarios, list) and set(fault_scenarios) == REQUIRED_FAULT_SCENARIOS,
        "fault-injection scenario coverage is incomplete",
    )

    vetoes: list[str] = []
    _append_overlay_vetoes(gates, vetoes)
    if sla_report.get("schema") != SLA_REPORT_SCHEMA:
        raise ProductionGateError("SLA report schema unsupported")
    if sla_report.get("vetoes"):
        vetoes.extend(f"sla:{value}" for value in sla_report["vetoes"])
    blockers = []
    if sla_report.get("production_sla_passed") is not True:
        blockers.append("production_capacity_sla_not_proven")
    status = "veto" if vetoes else ("blocked/not_proven" if blockers else "passed")
    return {
        "schema": REPORT_SCHEMA,
        "run_id": evidence["run_id"],
        "status": status,
        "production_overlay_gate_passed": status == "passed",
        "blockers": blockers,
        "vetoes": sorted(set(vetoes)),
        "source_git_commit": current_commit,
        "evidence_source_git_commit": evidence_commit,
        "source_scope_sha256": current_source_scope_sha256,
        "topology_sha256": evidence["topology_sha256"],
        "evidence_sha256": sha256_bytes(canonical_bytes(evidence)),
        "sla_report_sha256": sha256_bytes(canonical_bytes(sla_report)),
        "real_member_node_count": len(evidence["member_nodes"]),
        "derp_forced_relay_proven": derp.get("forced_relay") is True,
        "node_compromise_proven": compromise.get("status") == "passed",
        "current_source_a4_result": "12/12",
        "network_broker_results": ["26/26", "26/26"],
        "business_database_accessed": False,
        "root_env_accessed_by_script": False,
    }


def validation_only_report(
    *,
    config: dict[str, Any],
    current_commit: str,
    source_git_dirty: bool,
    source_scope: dict[str, Any],
) -> dict[str, Any]:
    placeholders = sum(bool(member["placeholder"]) for member in config["member_nodes"])
    return {
        "schema": REPORT_SCHEMA,
        "status": "blocked/not_proven",
        "production_overlay_gate_passed": False,
        "configuration_valid": True,
        "source_git_commit": current_commit,
        "source_git_dirty": source_git_dirty,
        "source_scope_file_count": source_scope["file_count"],
        "source_scope_sha256": source_scope["source_scope_sha256"],
        "topology_sha256": sha256_bytes(canonical_bytes(config)),
        "configured_member_node_count": len(config["member_nodes"]),
        "placeholder_member_node_count": placeholders,
        "blockers": [
            "current-source target Linux A4 12/12 evidence required",
            "two independent Network Broker 26/26 rounds required",
            "two real independent member nodes and production Node Daemons required",
            "forced DERP relay evidence required",
            "node revoke and stolen-credential rejection evidence required",
            "capacity SLA and failure-injection observations required",
        ],
        "vetoes": [],
        "business_database_accessed": False,
        "root_env_accessed_by_script": False,
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--report-out", required=True)
    parser.add_argument("--evidence")
    parser.add_argument("--sla-report")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if args.validate_only and (args.evidence or args.sla_report):
        raise SystemExit("--validate-only cannot consume scored evidence")
    if not args.validate_only and not (args.evidence and args.sla_report):
        raise SystemExit("scored mode requires --evidence and --sla-report")
    try:
        root = Path(args.repo_root).resolve(strict=True)
        config_path = Path(args.config)
        config = validate_config(safe_json_file(config_path))
        current_commit = require_git_commit(
            _git(root, "rev-parse", "HEAD"), context="current Git commit invalid"
        )
        source_git_dirty = bool(_git(root, "status", "--porcelain"))
        source_scope = build_source_scope(root)
        if args.validate_only:
            report = validation_only_report(
                config=config,
                current_commit=current_commit,
                source_git_dirty=source_git_dirty,
                source_scope=source_scope,
            )
        else:
            require(not source_git_dirty, "scored production Gate requires a clean checkout")
            evidence = safe_json_file(Path(args.evidence))
            sla_report = safe_json_file(Path(args.sla_report))
            verify_member_signatures(config=config, evidence=evidence)
            report = evaluate_evidence(
                config=config,
                evidence=evidence,
                sla_report=sla_report,
                current_commit=current_commit,
                current_source_scope_sha256=source_scope["source_scope_sha256"],
            )
        write_report(Path(args.report_out).resolve(), report)
    except (ProductionGateError, OSError, TypeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(
        f"P34.7 production Overlay Gate: {report['status']} "
        f"({sha256_file(Path(args.report_out).resolve())})"
    )
    return 0 if args.validate_only or report["production_overlay_gate_passed"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
