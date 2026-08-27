"""Personal-edition Owner admission built on the existing security lifecycle.

This module does not launch a Runtime and it does not weaken the enterprise
P34.7 joint Gate.  It verifies that one exact AI-space action is ready for an
authenticated human Owner to activate through the existing Operation ->
ApprovalRequest -> CapabilityGrant -> RunLease lifecycle.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from omnibase.capabilities.models import (
    CapabilityGrant,
    CapabilityRevocation,
    CapabilityUsage,
)
from omnibase.capabilities.service import (
    READ_ACTIONS,
    SANDBOX_ACTIONS,
    WORKSPACE_DATA_ACTIONS,
)
from omnibase.control_plane.models import ApprovalRequest, OperationRecord, ResourceRecord
from omnibase.db.tenant import User
from omnibase.workspaces.models import WorkspaceMembership
from omnibase.workspaces.service import LeaseRejected, verify_run_lease_for_sandbox

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_LOGICAL_DESTINATION = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_FORBIDDEN_DESTINATION_MARKERS = (
    ".env",
    "database",
    "docker.sock",
    "host.docker.internal",
    "localhost",
    "minio",
    "postgres",
    "redis",
    "socket",
)
_ALL_ACTIONS = READ_ACTIONS | SANDBOX_ACTIONS | WORKSPACE_DATA_ACTIONS
_READ_ONLY_ACTIONS = READ_ACTIONS | frozenset({"sandbox.logs", "sandbox.stats"})
_APPROVAL_METADATA_KEYS = frozenset(
    {
        "approval_policy",
        "external_side_effects",
        "network_policy_sha256",
        "plan_sha256",
        "profile",
        "sandbox_mode",
        "tool_schema_sha256",
    }
)


class PersonalGateConfigurationError(ValueError):
    """The personal admission contract is unsafe or ambiguous."""


class PersonalGateState(StrEnum):
    INVALID = "invalid/veto"
    BLOCKED = "personal/owner_approval_required"
    READY = "personal/ready_for_activation"


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _require_exact_keys(mapping: Mapping[str, object], expected: frozenset[str], name: str) -> None:
    actual = frozenset(mapping)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise PersonalGateConfigurationError(
            f"{name} fields must be an exact closed set; missing={missing}, unknown={unknown}"
        )


def _require_string(mapping: Mapping[str, object], name: str) -> str:
    value = mapping.get(name)
    if type(value) is not str or not value:
        raise PersonalGateConfigurationError(f"{name} must be a non-empty string")
    return value


def _require_bool(mapping: Mapping[str, object], name: str) -> bool:
    value = mapping.get(name)
    if type(value) is not bool:
        raise PersonalGateConfigurationError(f"{name} must be a JSON boolean")
    return value


def _require_int(mapping: Mapping[str, object], name: str, *, minimum: int = 1) -> int:
    value = mapping.get(name)
    if type(value) is not int or value < minimum:
        raise PersonalGateConfigurationError(
            f"{name} must be an integer greater than or equal to {minimum}"
        )
    return value


def _require_uuid(mapping: Mapping[str, object], name: str) -> str:
    value = _require_string(mapping, name)
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise PersonalGateConfigurationError(f"{name} must be a canonical UUID") from exc


def _require_digest(mapping: Mapping[str, object], name: str) -> str:
    value = _require_string(mapping, name)
    if not _DIGEST.fullmatch(value):
        raise PersonalGateConfigurationError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _is_forbidden_destination(value: str) -> bool:
    lowered = value.lower()
    if (
        value != lowered
        or not _LOGICAL_DESTINATION.fullmatch(value)
        or "*" in value
        or "://" in value
        or "/" in value
        or "\\" in value
        or any(marker in lowered for marker in _FORBIDDEN_DESTINATION_MARKERS)
    ):
        return True
    try:
        ipaddress.ip_address(value.strip("[]"))
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class PersonalNetworkPolicy:
    default_deny: bool
    destinations: tuple[str, ...]

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> PersonalNetworkPolicy:
        _require_exact_keys(mapping, frozenset({"default_deny", "destinations"}), "network")
        if _require_bool(mapping, "default_deny") is not True:
            raise PersonalGateConfigurationError("personal network policy must default deny")
        raw = mapping.get("destinations")
        if type(raw) is not list or any(type(item) is not str for item in raw):
            raise PersonalGateConfigurationError("network destinations must be a string array")
        destinations = tuple(raw)
        if len(set(destinations)) != len(destinations):
            raise PersonalGateConfigurationError("network destinations must not repeat")
        if destinations != tuple(sorted(destinations)):
            raise PersonalGateConfigurationError("network destinations must be sorted")
        for destination in destinations:
            if _is_forbidden_destination(destination):
                raise PersonalGateConfigurationError(
                    "network destinations must be logical service identifiers only"
                )
        return cls(default_deny=True, destinations=destinations)

    def canonical_digest(self) -> str:
        return _canonical_digest(
            {"default_deny": self.default_deny, "destinations": list(self.destinations)}
        )


@dataclass(frozen=True)
class PersonalOwnerPolicy:
    profile: str
    sandbox_mode: str
    approval_policy: str
    network: PersonalNetworkPolicy
    external_side_effects: bool

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> PersonalOwnerPolicy:
        _require_exact_keys(
            mapping,
            frozenset(
                {
                    "approval_policy",
                    "external_side_effects",
                    "network",
                    "profile",
                    "sandbox_mode",
                }
            ),
            "policy",
        )
        profile = _require_string(mapping, "profile")
        if profile != "personal_single_owner":
            raise PersonalGateConfigurationError(
                "only the personal_single_owner profile may use the personal Gate"
            )
        sandbox_mode = _require_string(mapping, "sandbox_mode")
        if sandbox_mode not in {
            "observe",
            "workspace_auto",
            "workspace_network_scoped",
            "owner_full_control",
        }:
            raise PersonalGateConfigurationError("sandbox_mode is outside the closed set")
        approval_policy = _require_string(mapping, "approval_policy")
        if approval_policy not in {
            "ask_on_boundary",
            "ask_on_network_or_side_effect",
            "owner_preapproved_exact_scope",
        }:
            raise PersonalGateConfigurationError("approval_policy is outside the closed set")
        raw_network = mapping.get("network")
        if not isinstance(raw_network, Mapping):
            raise PersonalGateConfigurationError("network must be an object")
        network = PersonalNetworkPolicy.from_mapping(raw_network)
        external_side_effects = _require_bool(mapping, "external_side_effects")
        if sandbox_mode in {"observe", "workspace_auto"} and network.destinations:
            raise PersonalGateConfigurationError(
                f"{sandbox_mode} cannot carry network destinations"
            )
        if sandbox_mode == "observe" and external_side_effects:
            raise PersonalGateConfigurationError("observe cannot authorize external side effects")
        return cls(
            profile=profile,
            sandbox_mode=sandbox_mode,
            approval_policy=approval_policy,
            network=network,
            external_side_effects=external_side_effects,
        )


@dataclass(frozen=True)
class PersonalEngineeringEvidence:
    path: str
    sha256: str
    assertions: tuple[tuple[str, object], ...]

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> PersonalEngineeringEvidence:
        _require_exact_keys(mapping, frozenset({"assertions", "path", "sha256"}), "evidence")
        path = _require_string(mapping, "path").replace("\\", "/")
        if path == ".env" or path.startswith("../") or "/../" in path:
            raise PersonalGateConfigurationError("evidence path is outside the repository")
        sha256 = _require_digest(mapping, "sha256")
        assertions = mapping.get("assertions")
        if not isinstance(assertions, Mapping) or not assertions:
            raise PersonalGateConfigurationError("evidence assertions must be a non-empty object")
        return cls(path=path, sha256=sha256, assertions=tuple(sorted(assertions.items())))


@dataclass(frozen=True)
class PersonalOwnerGateConfig:
    schema_version: int
    policy: PersonalOwnerPolicy
    evidence: PersonalEngineeringEvidence
    migration_head: str
    migration_0013_created: bool
    agent_runtime_enabled: bool
    agent_planner_enabled: bool
    multi_agent_enabled: bool
    enterprise_approved_digest_present: bool

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> PersonalOwnerGateConfig:
        _require_exact_keys(
            mapping,
            frozenset(
                {
                    "agent_planner_enabled",
                    "agent_runtime_enabled",
                    "engineering_evidence",
                    "enterprise_approved_digest_present",
                    "migration_0013_created",
                    "migration_head",
                    "multi_agent_enabled",
                    "policy",
                    "schema_version",
                }
            ),
            "personal Gate config",
        )
        if _require_int(mapping, "schema_version") != 1:
            raise PersonalGateConfigurationError("unsupported personal Gate schema_version")
        raw_policy = mapping.get("policy")
        raw_evidence = mapping.get("engineering_evidence")
        if not isinstance(raw_policy, Mapping) or not isinstance(raw_evidence, Mapping):
            raise PersonalGateConfigurationError("policy and engineering_evidence must be objects")
        migration_head = _require_string(mapping, "migration_head")
        if migration_head != "0016":
            raise PersonalGateConfigurationError("personal Gate requires migration head 0016")
        migration_0013_created = _require_bool(mapping, "migration_0013_created")
        if not migration_0013_created:
            raise PersonalGateConfigurationError(
                "personal Gate requires the current migration 0013 to exist"
            )
        safety_flags = {
            name: _require_bool(mapping, name)
            for name in (
                "agent_runtime_enabled",
                "agent_planner_enabled",
                "multi_agent_enabled",
                "enterprise_approved_digest_present",
            )
        }
        if any(safety_flags.values()):
            raise PersonalGateConfigurationError(
                "personal readiness must be proven before activation or enterprise approval"
            )
        return cls(
            schema_version=1,
            policy=PersonalOwnerPolicy.from_mapping(raw_policy),
            evidence=PersonalEngineeringEvidence.from_mapping(raw_evidence),
            migration_head=migration_head,
            migration_0013_created=migration_0013_created,
            **safety_flags,
        )


@dataclass(frozen=True)
class PersonalOwnerGateRequest:
    tenant_id: str
    workspace_id: str
    run_id: str
    runtime_instance_id: str
    lease_id: str
    node_id: str
    generation: int
    run_fencing_token: int
    workload_identity_digest: str
    approval_id: str
    approval_expected_version: int
    operation_id: str
    requester_type: str
    requester_id: str | None
    action: str
    resource_id: str
    resource_version: int
    request_digest: str
    plan_digest: str
    tool_schema_digest: str
    grant_id: str
    requested_calls: int
    requested_bytes: int
    requested_cost_units: int

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> PersonalOwnerGateRequest:
        expected = frozenset(
            {
                "action",
                "approval_expected_version",
                "approval_id",
                "generation",
                "grant_id",
                "lease_id",
                "node_id",
                "operation_id",
                "plan_digest",
                "request_digest",
                "requested_bytes",
                "requested_calls",
                "requested_cost_units",
                "requester_id",
                "requester_type",
                "resource_id",
                "resource_version",
                "run_fencing_token",
                "run_id",
                "runtime_instance_id",
                "tenant_id",
                "tool_schema_digest",
                "workload_identity_digest",
                "workspace_id",
            }
        )
        _require_exact_keys(mapping, expected, "personal Gate request")
        requester_type = _require_string(mapping, "requester_type")
        if requester_type not in {"agent", "run", "system"}:
            raise PersonalGateConfigurationError(
                "personal requester must be an agent, run, or system"
            )
        raw_requester = mapping.get("requester_id")
        if requester_type == "system":
            if raw_requester is not None:
                raise PersonalGateConfigurationError("system requester_id must be null")
            requester_id = None
        else:
            requester_id = _require_uuid(mapping, "requester_id")
        action = _require_string(mapping, "action")
        if action not in _ALL_ACTIONS:
            raise PersonalGateConfigurationError("action is outside Capability closed sets")
        requested_bytes = _require_int(mapping, "requested_bytes", minimum=0)
        return cls(
            tenant_id=_require_uuid(mapping, "tenant_id"),
            workspace_id=_require_uuid(mapping, "workspace_id"),
            run_id=_require_uuid(mapping, "run_id"),
            runtime_instance_id=_require_uuid(mapping, "runtime_instance_id"),
            lease_id=_require_uuid(mapping, "lease_id"),
            node_id=_require_uuid(mapping, "node_id"),
            generation=_require_int(mapping, "generation"),
            run_fencing_token=_require_int(mapping, "run_fencing_token"),
            workload_identity_digest=_require_digest(mapping, "workload_identity_digest"),
            approval_id=_require_uuid(mapping, "approval_id"),
            approval_expected_version=_require_int(mapping, "approval_expected_version"),
            operation_id=_require_uuid(mapping, "operation_id"),
            requester_type=requester_type,
            requester_id=requester_id,
            action=action,
            resource_id=_require_uuid(mapping, "resource_id"),
            resource_version=_require_int(mapping, "resource_version"),
            request_digest=_require_digest(mapping, "request_digest"),
            plan_digest=_require_digest(mapping, "plan_digest"),
            tool_schema_digest=_require_digest(mapping, "tool_schema_digest"),
            grant_id=_require_uuid(mapping, "grant_id"),
            requested_calls=_require_int(mapping, "requested_calls"),
            requested_bytes=requested_bytes,
            requested_cost_units=_require_int(mapping, "requested_cost_units"),
        )


@dataclass(frozen=True)
class PersonalOwnerGateReport:
    state: PersonalGateState
    personal_activation_ready: bool
    runtime_activated: bool
    owner_user_id: str | None
    policy_sha256: str
    evidence_sha256: str
    approval_id: str | None
    grant_id: str | None
    run_lease_verification_sha256: str | None
    blockers: tuple[str, ...]
    vetoes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "profile": "personal_single_owner",
            "personal_activation_ready": self.personal_activation_ready,
            "runtime_activated": self.runtime_activated,
            "enterprise_track_frozen": True,
            "owner_user_id": self.owner_user_id,
            "policy_sha256": self.policy_sha256,
            "evidence_sha256": self.evidence_sha256,
            "approval_id": self.approval_id,
            "grant_id": self.grant_id,
            "run_lease_verification_sha256": self.run_lease_verification_sha256,
            "blockers": list(self.blockers),
            "vetoes": list(self.vetoes),
            "activation_allowed": False,
            "root_env_accessed": False,
            "business_database_accessed": False,
            "business_database_migrated": False,
            "migration_head": "0016",
            "migration_0013_created": True,
            "agent_runtime_enabled": False,
            "agent_planner_enabled": False,
            "multi_agent_enabled": False,
            "enterprise_approved_digest_present": False,
        }


class PersonalOwnerGate:
    """Verify sealed engineering evidence and live single-Owner authority facts."""

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root.resolve()

    def verify(
        self,
        session: Session,
        *,
        config: PersonalOwnerGateConfig,
        request: PersonalOwnerGateRequest,
    ) -> PersonalOwnerGateReport:
        policy_sha256 = _canonical_digest(
            {
                "approval_policy": config.policy.approval_policy,
                "external_side_effects": config.policy.external_side_effects,
                "network": {
                    "default_deny": config.policy.network.default_deny,
                    "destinations": list(config.policy.network.destinations),
                },
                "profile": config.policy.profile,
                "sandbox_mode": config.policy.sandbox_mode,
            }
        )
        vetoes: list[str] = []
        blockers: list[str] = []
        self._verify_static_contract(
            config=config,
            request=request,
            blockers=blockers,
            vetoes=vetoes,
        )
        owner_user_id, owner = self._resolve_live_owner(
            session,
            request=request,
            vetoes=vetoes,
        )
        approval, operation, grant, resource = self._load_bound_records(session, request)
        now = session.scalar(select(func.clock_timestamp()))
        if not isinstance(now, datetime):
            vetoes.append("database clock is unavailable")
            now = datetime.now(UTC)

        if owner_user_id is not None and (
            owner is None or not owner.is_active or not owner.is_tenant_admin
        ):
            vetoes.append("personal Owner must be a live tenant administrator")
        self._verify_live_authority(
            session,
            config=config,
            request=request,
            owner_user_id=owner_user_id,
            approval=approval,
            operation=operation,
            grant=grant,
            resource=resource,
            now=_aware(now),
            blockers=blockers,
            vetoes=vetoes,
        )

        lease_digest = self._verify_live_lease(session, request=request, vetoes=vetoes)
        if vetoes:
            state = PersonalGateState.INVALID
        elif blockers:
            state = PersonalGateState.BLOCKED
        else:
            state = PersonalGateState.READY
        return PersonalOwnerGateReport(
            state=state,
            personal_activation_ready=state is PersonalGateState.READY,
            runtime_activated=False,
            owner_user_id=owner_user_id,
            policy_sha256=policy_sha256,
            evidence_sha256=config.evidence.sha256,
            approval_id=approval.id if approval is not None else None,
            grant_id=grant.id if grant is not None else None,
            run_lease_verification_sha256=lease_digest,
            blockers=tuple(blockers),
            vetoes=tuple(vetoes),
        )

    def _verify_static_contract(
        self,
        *,
        config: PersonalOwnerGateConfig,
        request: PersonalOwnerGateRequest,
        blockers: list[str],
        vetoes: list[str],
    ) -> None:
        try:
            self._verify_evidence(config.evidence)
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            PersonalGateConfigurationError,
        ) as exc:
            vetoes.append(f"engineering evidence: {exc}")

        if config.policy.sandbox_mode == "observe" and request.action not in _READ_ONLY_ACTIONS:
            vetoes.append("observe profile cannot authorize a mutating action")
        if (
            config.policy.network.destinations or config.policy.external_side_effects
        ) and config.policy.approval_policy == "ask_on_boundary":
            blockers.append("Owner must approve the network or side-effect boundary")

    def _resolve_live_owner(
        self,
        session: Session,
        *,
        request: PersonalOwnerGateRequest,
        vetoes: list[str],
    ) -> tuple[str | None, User | None]:
        memberships = list(
            session.scalars(
                select(WorkspaceMembership)
                .where(
                    WorkspaceMembership.tenant_id == request.tenant_id,
                    WorkspaceMembership.workspace_id == request.workspace_id,
                    WorkspaceMembership.state == "active",
                )
                .with_for_update()
            )
        )
        owner_user_id: str | None = None
        if len(memberships) != 1 or memberships[0].role != "owner":
            vetoes.append(
                "personal AI space must have exactly one active Owner and no other member"
            )
            return None, None
        owner_user_id = memberships[0].user_id
        owner = session.execute(
            select(User).where(User.id == owner_user_id).with_for_update()
        ).scalar_one_or_none()
        return owner_user_id, owner

    def _load_bound_records(
        self, session: Session, request: PersonalOwnerGateRequest
    ) -> tuple[
        ApprovalRequest | None,
        OperationRecord | None,
        CapabilityGrant | None,
        ResourceRecord | None,
    ]:
        approval = session.execute(
            select(ApprovalRequest)
            .where(
                ApprovalRequest.id == request.approval_id,
                ApprovalRequest.tenant_id == request.tenant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        operation = session.execute(
            select(OperationRecord)
            .where(
                OperationRecord.id == request.operation_id,
                OperationRecord.tenant_id == request.tenant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        grant = session.execute(
            select(CapabilityGrant)
            .where(
                CapabilityGrant.id == request.grant_id,
                CapabilityGrant.tenant_id == request.tenant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        resource = session.execute(
            select(ResourceRecord)
            .where(
                ResourceRecord.id == request.resource_id,
                ResourceRecord.tenant_id == request.tenant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        return approval, operation, grant, resource

    def _verify_live_authority(
        self,
        session: Session,
        *,
        config: PersonalOwnerGateConfig,
        request: PersonalOwnerGateRequest,
        owner_user_id: str | None,
        approval: ApprovalRequest | None,
        operation: OperationRecord | None,
        grant: CapabilityGrant | None,
        resource: ResourceRecord | None,
        now: datetime,
        blockers: list[str],
        vetoes: list[str],
    ) -> None:
        if approval is None:
            blockers.append("Owner approval is unavailable")
        else:
            self._verify_approval(
                approval,
                operation=operation,
                owner_user_id=owner_user_id,
                request=request,
                config=config,
                now=now,
                blockers=blockers,
                vetoes=vetoes,
            )
        if operation is None:
            vetoes.append("bound Operation is unavailable")
        if grant is None:
            vetoes.append("bound CapabilityGrant is unavailable")
        else:
            usage = session.execute(
                select(CapabilityUsage)
                .where(
                    CapabilityUsage.tenant_id == request.tenant_id,
                    CapabilityUsage.grant_id == request.grant_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            grant_revoked = session.execute(
                select(CapabilityRevocation.id).where(
                    CapabilityRevocation.tenant_id == request.tenant_id,
                    CapabilityRevocation.grant_id == request.grant_id,
                    CapabilityRevocation.token_jti.is_(None),
                )
            ).scalar_one_or_none()
            self._verify_grant(
                grant,
                usage=usage,
                grant_revoked=grant_revoked is not None,
                owner_user_id=owner_user_id,
                request=request,
                now=now,
                vetoes=vetoes,
            )
        if resource is None:
            vetoes.append("logical Resource is unavailable")
        elif (
            resource.version != request.resource_version
            or resource.state not in {"active", "running", "paused", "stopped"}
            or resource.policy_class == "system_internal"
        ):
            vetoes.append("logical Resource version or policy binding drifted")

    def _verify_live_lease(
        self,
        session: Session,
        *,
        request: PersonalOwnerGateRequest,
        vetoes: list[str],
    ) -> str | None:
        try:
            lease = verify_run_lease_for_sandbox(
                session,
                tenant_id=request.tenant_id,
                run_id=request.run_id,
                runtime_instance_id=request.runtime_instance_id,
                lease_id=request.lease_id,
                node_id=request.node_id,
                generation=request.generation,
                fencing_token=request.run_fencing_token,
                workload_identity_digest=request.workload_identity_digest,
            )
        except LeaseRejected as exc:
            vetoes.append(f"live RunLease verification failed: {exc}")
            return None
        if lease.workspace_id != request.workspace_id:
            vetoes.append("RunLease workspace binding drifted")
            return None
        return lease.verification_digest

    def _verify_approval(
        self,
        approval: ApprovalRequest,
        *,
        operation: OperationRecord | None,
        owner_user_id: str | None,
        request: PersonalOwnerGateRequest,
        config: PersonalOwnerGateConfig,
        now: datetime,
        blockers: list[str],
        vetoes: list[str],
    ) -> None:
        if approval.state != "approved" or approval.consumed_at is not None:
            blockers.append("Owner approval is not approved and unconsumed")
        if _aware(approval.expires_at) <= now:
            blockers.append("Owner approval expired")
        expected = (
            approval.version == request.approval_expected_version
            and approval.requester_type == request.requester_type
            and approval.requester_id == request.requester_id
            and approval.workspace_id == request.workspace_id
            and approval.run_id == request.run_id
            and approval.resource_id == request.resource_id
            and approval.resource_version == request.resource_version
            and approval.operation_id == request.operation_id
            and approval.grant_id == request.grant_id
            and approval.action == request.action
            and approval.request_hash == request.request_digest
            and approval.required_approver_role == "tenant_admin"
            and approval.risk_level != "R4"
            and approval.decided_by_actor_type == "user"
            and approval.decided_by_actor_id == owner_user_id
            and approval.decided_at is not None
            and approval.decided_at >= approval.created_at
            and approval.decided_at < approval.expires_at
        )
        if not expected:
            vetoes.append("Owner approval exact binding drifted")
        if request.requester_type == "run" and request.requester_id != request.run_id:
            vetoes.append("run requester must be the exact bound Run")
        if owner_user_id is not None and request.requester_id == owner_user_id:
            vetoes.append("AI requester cannot use the Owner identity")
        expected_metadata: dict[str, object] = {
            "approval_policy": config.policy.approval_policy,
            "external_side_effects": config.policy.external_side_effects,
            "network_policy_sha256": config.policy.network.canonical_digest(),
            "plan_sha256": request.plan_digest,
            "profile": config.policy.profile,
            "sandbox_mode": config.policy.sandbox_mode,
            "tool_schema_sha256": request.tool_schema_digest,
        }
        if (
            frozenset(approval.approval_metadata) != _APPROVAL_METADATA_KEYS
            or approval.approval_metadata != expected_metadata
        ):
            vetoes.append("Owner approval policy metadata drifted")
        if operation is not None and any(
            (
                operation.state != "pending_approval",
                operation.actor_type != request.requester_type,
                operation.actor_id != request.requester_id,
                operation.workspace_id != request.workspace_id,
                operation.run_id != request.run_id,
                operation.resource_id != request.resource_id,
                operation.resource_version != request.resource_version,
                operation.request_hash != request.request_digest,
                operation.kind != request.action,
                operation.risk_level != approval.risk_level,
                operation.approval_id not in {None, approval.id},
            )
        ):
            vetoes.append("Operation and Owner approval binding drifted")

    def _verify_grant(
        self,
        grant: CapabilityGrant,
        *,
        usage: CapabilityUsage | None,
        grant_revoked: bool,
        owner_user_id: str | None,
        request: PersonalOwnerGateRequest,
        now: datetime,
        vetoes: list[str],
    ) -> None:
        if any(
            (
                grant.state != "active",
                _aware(grant.not_before) > now,
                _aware(grant.expires_at) <= now,
                grant.revoked_at is not None,
                grant_revoked,
                grant.workspace_id != request.workspace_id,
                grant.runtime_instance_id != request.runtime_instance_id,
                grant.workload_identity_digest != request.workload_identity_digest,
                grant.actor_user_id != owner_user_id,
                grant.delegation_depth != 0,
                grant.delegation_depth_limit != 0,
                grant.parent_grant_id is not None,
                grant.approval_id is not None,
                request.action not in grant.actions,
                request.resource_id not in grant.resource_ids,
            )
        ):
            vetoes.append("CapabilityGrant exact binding is inactive or drifted")
        if usage is None:
            vetoes.append("Capability budget ledger is unavailable")
            return
        if (
            usage.calls + request.requested_calls > grant.max_calls
            or usage.bytes_in + usage.bytes_out + request.requested_bytes > grant.max_bytes
            or usage.cost_units + request.requested_cost_units > grant.max_cost_units
        ):
            vetoes.append("Capability budget is insufficient")

    def _verify_evidence(self, evidence: PersonalEngineeringEvidence) -> None:
        verify_personal_engineering_evidence(self._repo_root, evidence)


def verify_personal_engineering_evidence(
    repo_root: Path,
    evidence: PersonalEngineeringEvidence,
) -> None:
    """Verify the sealed personal-readiness evidence without activating anything.

    The helper is intentionally filesystem-only so the later personal Runtime
    activation controller can reuse the exact byte/path checks while keeping
    live Owner, Approval, Capability and RunLease validation in their existing
    database-backed services.
    """
    root = repo_root.resolve(strict=True)
    path = (root / evidence.path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise PersonalGateConfigurationError("evidence escaped the repository") from exc
    metadata = os.lstat(path)
    is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
    if stat.S_ISLNK(metadata.st_mode) or is_reparse or not stat.S_ISREG(metadata.st_mode):
        raise PersonalGateConfigurationError("evidence must be a regular non-link file")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != evidence.sha256:
        raise PersonalGateConfigurationError("sealed evidence SHA-256 drifted")
    payload = json.loads(content.decode("utf-8"))
    for name, expected in evidence.assertions:
        if payload.get(name) != expected:
            raise PersonalGateConfigurationError(f"evidence assertion failed: {name}")


def load_personal_owner_gate_config(path: Path) -> PersonalOwnerGateConfig:
    metadata = os.lstat(path)
    is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
    if stat.S_ISLNK(metadata.st_mode) or is_reparse or not stat.S_ISREG(metadata.st_mode):
        raise PersonalGateConfigurationError("personal Gate config must be a regular file")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise PersonalGateConfigurationError("personal Gate config must be an object")
    return PersonalOwnerGateConfig.from_mapping(payload)


__all__ = [
    "PersonalGateConfigurationError",
    "PersonalGateState",
    "PersonalNetworkPolicy",
    "PersonalOwnerGate",
    "PersonalOwnerGateConfig",
    "PersonalOwnerGateReport",
    "PersonalOwnerGateRequest",
    "PersonalOwnerPolicy",
    "load_personal_owner_gate_config",
]
