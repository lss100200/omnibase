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
from typing import Any, Protocol

from sqlalchemy import func, select

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
from omnibase.agent_registry.models import AgentVersionModel, WorkspaceAgentBindingModel
from omnibase.capability_gateway.contracts import WorkloadCredential
from omnibase.task_ledger.models import AgentRunModel, AgentTaskModel
from omnibase.workspaces.models import RunLease, Workspace, WorkspaceRun
from omnibase.workspaces.service import (
    LeaseRejected,
    WorkspaceNotFound,
    get_active_attested_node,
)

ENGINEERING_FLAG = "P5_4B_ENGINEERING_ENABLED"
EXPECTED_MIGRATION_HEAD = "0014"
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

    def validate(self, *, context: ExecutorInvocationContext, credential: Any) -> None:  # noqa: C901
        if not isinstance(credential, WorkloadCredential):
            raise EngineeringCompositionError("workload_credential_type_invalid")
        trusted = credential.trusted_context
        runtime_instance_id = trusted.runtime_instance_id
        workload_digest = trusted.workload_identity_digest
        if not runtime_instance_id or trusted.tenant_id != context.tenant_id:
            raise EngineeringCompositionError("runtime_identity_invalid")
        if trusted.workspace_id != context.workspace_id:
            raise EngineeringCompositionError("runtime_workspace_mismatch")
        if not workload_digest:
            raise EngineeringCompositionError("workload_identity_digest_missing")
        session = self._session_factory()
        try:
            workspace = session.execute(
                select(Workspace)
                .where(
                    Workspace.id == context.workspace_id,
                    Workspace.tenant_id == context.tenant_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
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
            if workspace is None or task is None or run is None:
                raise EngineeringCompositionError("runtime_binding_not_found")
            version = session.execute(
                select(AgentVersionModel)
                .where(
                    AgentVersionModel.id == context.agent_version_id,
                    AgentVersionModel.tenant_id == context.tenant_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            binding = session.execute(
                select(WorkspaceAgentBindingModel)
                .where(
                    WorkspaceAgentBindingModel.id == task.workspace_agent_binding_id,
                    WorkspaceAgentBindingModel.tenant_id == context.tenant_id,
                    WorkspaceAgentBindingModel.workspace_id == context.workspace_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if version is None or binding is None:
                raise EngineeringCompositionError("agent_version_authority_stale")
            if (
                version.version_state != "sealed"
                or version.id != task.agent_version_id
                or version.manifest_digest != task.agent_version_digest
                or version.definition_id != task.agent_definition_id
                or binding.binding_state != "installed"
                or binding.id != task.workspace_agent_binding_id
                or binding.workspace_generation != context.workspace_generation
                or binding.agent_definition_id != task.agent_definition_id
                or binding.agent_version_id != version.id
                or binding.agent_version_digest != version.manifest_digest
            ):
                raise EngineeringCompositionError("agent_version_authority_stale")
            now = session.execute(select(func.clock_timestamp())).scalar_one()
            if (
                workspace.generation != context.workspace_generation
                or workspace.desired_state != "running"
                or workspace.observed_state != "running"
            ):
                raise EngineeringCompositionError("workspace_authority_stale")
            if (
                task.state not in {"scheduled", "running"}
                or task.deadline <= now
                or task.workspace_id != context.workspace_id
                or task.workspace_generation != context.workspace_generation
                or task.actor_user_id != context.actor_user_id
                or task.task_generation != context.task_generation
                or task.agent_version_id != context.agent_version_id
                or task.agent_version_digest != context.agent_version_digest
                or task.plan_digest != context.proposal_digest
                or task.plan_version != context.proposal_version
                or task.resource_scope_digest != context.resource_scope_digest
                or task.budget_policy_digest != context.budget_policy_digest
            ):
                raise EngineeringCompositionError("task_authority_stale")
            if (
                run.state not in {"leased", "running"}
                or run.task_id != task.id
                or run.workspace_id != context.workspace_id
                or run.workspace_generation != context.workspace_generation
                or run.runtime_instance_id != runtime_instance_id
                or run.workload_identity_digest != workload_digest
                or run.run_fencing_token != context.run_fencing_token
                or run.node_id is None
                or run.run_lease_id is None
                or run.node_fencing_token is None
                or run.workspace_run_id == run.id
            ):
                raise EngineeringCompositionError("runtime_fencing_stale")
            workspace_run = session.execute(
                select(WorkspaceRun)
                .where(
                    WorkspaceRun.id == run.workspace_run_id,
                    WorkspaceRun.tenant_id == context.tenant_id,
                    WorkspaceRun.workspace_id == context.workspace_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if (
                workspace_run is None
                or workspace_run.desired_state != "running"
                or workspace_run.observed_state != "running"
                or workspace_run.generation != context.workspace_generation
                or workspace_run.next_fencing_token - 1 != context.run_fencing_token
                or workspace_run.runtime_instance_id != runtime_instance_id
                or workspace_run.workload_identity_digest != workload_digest
            ):
                raise EngineeringCompositionError("workspace_run_authority_stale")
            lease = session.execute(
                select(RunLease)
                .where(
                    RunLease.id == run.run_lease_id,
                    RunLease.tenant_id == context.tenant_id,
                    RunLease.workspace_id == context.workspace_id,
                    RunLease.run_id == workspace_run.id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if (
                lease is None
                or lease.state != "active"
                or lease.expires_at <= now
                or lease.generation != context.workspace_generation
                or lease.fencing_token != context.run_fencing_token
                or lease.node_id != run.node_id
                or lease.node_fencing_token != run.node_fencing_token
                or run.run_lease_id != lease.id
            ):
                raise EngineeringCompositionError("run_lease_stale")
            try:
                node = get_active_attested_node(
                    session,
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    node_id=run.node_id,
                    lock=True,
                )
            except (WorkspaceNotFound, LeaseRejected) as exc:
                raise EngineeringCompositionError("node_attestation_stale") from exc
            except Exception as exc:
                raise EngineeringCompositionError("authority_unavailable") from exc
            if node.fencing_token != lease.node_fencing_token:
                raise EngineeringCompositionError("node_fencing_stale")
            session.commit()
        except Exception:
            session.rollback()
            raise
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
    if feature_gates is None or set(feature_gates) != set(_PHASE5_GATE_NAMES):
        return False
    return all(
        type(feature_gates[name]) is bool and feature_gates[name] is False
        for name in _PHASE5_GATE_NAMES
    )


def build_engineering_single_agent_executor(
    *,
    enabled: bool | None = None,
    feature_gates: Mapping[str, bool] | None = None,
    gateway: GatewayRagService | None = None,
    session_factory: SessionFactory | None = None,
    workload_credential_seam: ServerWorkloadCredentialSeam | None = None,
    migration_head: str | None = None,
) -> EngineeringSingleAgentExecutor | UnavailableEngineeringSingleAgentExecutor:
    """Assemble P5.4B only when every engineering dependency is explicit.

    ``migration_head`` is an explicit admission fact; omission is unavailable.
    The formal builder always installs the live database-backed authority validator.
    """
    if enabled is None:
        enabled = _exact_flag(os.environ.get(ENGINEERING_FLAG), name="p5_4b_engineering")
    if not enabled or migration_head != EXPECTED_MIGRATION_HEAD or not _gates_false(feature_gates):
        return UnavailableEngineeringSingleAgentExecutor()
    if gateway is None or session_factory is None or workload_credential_seam is None:
        return UnavailableEngineeringSingleAgentExecutor()
    validator = LiveRuntimeAuthorityValidator(session_factory=session_factory)
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
