"""Engineering-only composition for the P5.4B single-Agent executor.

This module is deliberately an internal composition seam.  It does not add a
Browser/API route, a migration, a provider client, or a second tool.  The
only executable capability is the existing ``knowledge_search`` port backed by
``GatewayService.rag_search``.  All workload credentials are issued by an
injected server-owned provider and live Run/lease/node facts are checked before
an invocation reaches the Gateway.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select

from omnibase.agent_executor.contracts import (
    ExecutorInvocationContext,
    ExecutorNodeResult,
    KnowledgeSearchPort,
    KnowledgeSearchRequest,
)
from omnibase.agent_executor.gateway_adapter import (
    CapabilityGatewayKnowledgeSearchPort,
    GatewayRagService,
    RuntimeAuthorityValidator,
    SessionFactory,
)
from omnibase.agent_executor.service import (
    TypedExecutorUnavailable,
    TypedSingleAgentExecutor,
)
from omnibase.capability_gateway.contracts import WorkloadCredential
from omnibase.task_ledger.models import AgentRunModel, AgentTaskModel
from omnibase.workspaces.models import RunLease, WorkspaceNode

ENGINEERING_FLAG = "P5_4B_ENGINEERING_ENABLED"
EXPECTED_MIGRATION_HEAD = "0012"
_PHASE5_GATE_NAMES = (
    "agent_runtime_enabled",
    "agent_planner_enabled",
    "multi_agent_enabled",
)
_PHASE5_GATE_ENV_NAMES = (
    "AGENT_RUNTIME_ENABLED",
    "AGENT_PLANNER_ENABLED",
    "MULTI_AGENT_ENABLED",
)


class EngineeringCompositionError(RuntimeError):
    """A malformed engineering-only composition configuration."""


class EngineeringCompositionUnavailable(TypedExecutorUnavailable):
    """The explicit engineering composition is not admissible."""


class ServerWorkloadCredentialSeam(Protocol):
    """Server-owned short-lived workload credential issuer."""

    def issue(self, *, context: ExecutorInvocationContext) -> WorkloadCredential: ...


class LiveRuntimeAuthorityValidator(RuntimeAuthorityValidator):
    """Revalidate the live task/run/lease/node chain before each Gateway call.

    The validator intentionally obtains all authority from the database and
    the server-issued credential.  It never accepts a client-supplied runtime,
    lease, node, schema, or credential value as an authorization shortcut.
    """

    def __init__(self, *, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def validate(self, *, context: ExecutorInvocationContext, credential: Any) -> None:
        if not isinstance(credential, WorkloadCredential):
            raise EngineeringCompositionError("workload_credential_type_invalid")
        trusted = credential.trusted_context
        runtime_instance_id = trusted.runtime_instance_id
        if not runtime_instance_id or trusted.tenant_id != context.tenant_id:
            raise EngineeringCompositionError("runtime_identity_invalid")
        if trusted.workspace_id != context.workspace_id:
            raise EngineeringCompositionError("runtime_workspace_mismatch")
        session = self._session_factory()
        try:
            task = session.execute(
                select(AgentTaskModel)
                .where(
                    AgentTaskModel.id == context.task_id,
                    AgentTaskModel.tenant_id == context.tenant_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            run = session.execute(
                select(AgentRunModel)
                .where(
                    AgentRunModel.id == context.run_id,
                    AgentRunModel.task_id == context.task_id,
                    AgentRunModel.tenant_id == context.tenant_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if task is None or run is None:
                raise EngineeringCompositionError("runtime_binding_not_found")
            if (
                task.workspace_id != context.workspace_id
                or task.workspace_generation != context.workspace_generation
                or task.task_generation != context.task_generation
            ):
                raise EngineeringCompositionError("runtime_generation_stale")
            if run.state not in {"leased", "running", "paused"}:
                raise EngineeringCompositionError("runtime_not_active")
            if (
                run.runtime_instance_id != runtime_instance_id
                or run.run_fencing_token != context.run_fencing_token
                or run.run_lease_id is None
                or run.node_id is None
                or run.node_fencing_token is None
            ):
                raise EngineeringCompositionError("runtime_fencing_stale")
            lease = session.execute(
                select(RunLease)
                .where(
                    RunLease.id == run.run_lease_id,
                    RunLease.tenant_id == context.tenant_id,
                    RunLease.workspace_id == context.workspace_id,
                    RunLease.run_id == context.run_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if (
                lease is None
                or lease.state != "active"
                or lease.expires_at <= datetime.now(UTC)
                or lease.fencing_token != context.run_fencing_token
                or lease.node_id != run.node_id
                or lease.node_fencing_token != run.node_fencing_token
            ):
                raise EngineeringCompositionError("run_lease_stale")
            node = session.execute(
                select(WorkspaceNode).where(
                    WorkspaceNode.id == lease.node_id,
                    WorkspaceNode.tenant_id == context.tenant_id,
                    WorkspaceNode.workspace_id == context.workspace_id,
                )
            ).scalar_one_or_none()
            if (
                node is None
                or node.state != "active"
                or node.attestation_state != "verified"
                or node.fencing_token != lease.node_fencing_token
            ):
                raise EngineeringCompositionError("node_attestation_stale")
        finally:
            session.close()


class _SeamProvider:
    def __init__(self, seam: ServerWorkloadCredentialSeam) -> None:
        self._seam = seam

    def __call__(self, *, context: ExecutorInvocationContext) -> WorkloadCredential:
        credential = self._seam.issue(context=context)
        if not isinstance(credential, WorkloadCredential):
            raise EngineeringCompositionError("workload_credential_type_invalid")
        return credential


class EngineeringSingleAgentExecutor:
    """Composed P5.4B executor with one Gateway-backed read capability."""

    def __init__(self, *, typed_executor: TypedSingleAgentExecutor) -> None:
        self._typed_executor = typed_executor

    def execute(
        self,
        *,
        context: ExecutorInvocationContext,
        plan: Any,
        request: KnowledgeSearchRequest,
    ) -> ExecutorNodeResult:
        return self._typed_executor.execute(context=context, plan=plan, request=request)


class UnavailableEngineeringSingleAgentExecutor:
    """Fail-closed default; no credential, session, or Gateway is touched."""

    def execute(self, **_: object) -> ExecutorNodeResult:
        raise EngineeringCompositionUnavailable("engineering_composition_unavailable")


def _exact_flag(raw: str | None, *, name: str) -> bool:
    if raw is None or raw == "false" or raw == "":
        return False
    if raw == "true":
        return True
    raise EngineeringCompositionError(f"{name}_invalid")


def _gates_false(feature_gates: Mapping[str, bool] | None) -> bool:
    if feature_gates is not None:
        return all(
            feature_gates.get(name, feature_gates.get(env_name)) is False
            for name, env_name in zip(_PHASE5_GATE_NAMES, _PHASE5_GATE_ENV_NAMES, strict=True)
        )
    for name in _PHASE5_GATE_ENV_NAMES:
        raw = os.environ.get(name)
        if raw in (None, "", "false"):
            continue
        if raw != "true":
            raise EngineeringCompositionError(f"{name}_invalid")
        return False
    return True


def build_engineering_single_agent_executor(
    *,
    enabled: bool | None = None,
    feature_gates: Mapping[str, bool] | None = None,
    gateway: GatewayRagService | None = None,
    session_factory: SessionFactory | None = None,
    workload_credential_seam: ServerWorkloadCredentialSeam | None = None,
    authority_validator: RuntimeAuthorityValidator | None = None,
    migration_head: str = EXPECTED_MIGRATION_HEAD,
) -> EngineeringSingleAgentExecutor | UnavailableEngineeringSingleAgentExecutor:
    """Assemble P5.4B only when every engineering dependency is explicit.

    ``migration_head`` is supplied by the caller's already-running migration
    check; this function never migrates or connects merely to inspect it.
    """
    if enabled is None:
        enabled = _exact_flag(os.environ.get(ENGINEERING_FLAG), name="p5_4b_engineering")
    if not enabled or migration_head != EXPECTED_MIGRATION_HEAD or not _gates_false(feature_gates):
        return UnavailableEngineeringSingleAgentExecutor()
    if gateway is None or session_factory is None or workload_credential_seam is None:
        return UnavailableEngineeringSingleAgentExecutor()
    validator = authority_validator or LiveRuntimeAuthorityValidator(
        session_factory=session_factory
    )
    port: KnowledgeSearchPort = CapabilityGatewayKnowledgeSearchPort(
        gateway=gateway,
        session_factory=session_factory,
        credential_provider=_SeamProvider(workload_credential_seam),
        authority_validator=validator,
    )
    return EngineeringSingleAgentExecutor(
        typed_executor=TypedSingleAgentExecutor(knowledge_search=port)
    )


# Short aliases keep the seam discoverable to internal callers without adding
# another public/API surface.
build_engineering_executor = build_engineering_single_agent_executor


__all__ = [
    "ENGINEERING_FLAG",
    "EXPECTED_MIGRATION_HEAD",
    "EngineeringCompositionError",
    "EngineeringCompositionUnavailable",
    "EngineeringSingleAgentExecutor",
    "LiveRuntimeAuthorityValidator",
    "ServerWorkloadCredentialSeam",
    "UnavailableEngineeringSingleAgentExecutor",
    "build_engineering_executor",
    "build_engineering_single_agent_executor",
]
