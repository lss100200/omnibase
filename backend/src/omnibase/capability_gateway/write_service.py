"""Independent P34.6 workspace-data Gateway lifecycle.

This service is deliberately separate from the read service: write budget,
operation idempotency, effect state, and Audit are one lifecycle.  Promotion
and canonical mutation do not exist in this module.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel
from sqlalchemy.orm import Session

from omnibase.capabilities.service import VerifiedWorkspaceDataCapabilityFacts
from omnibase.capability_gateway.audit import GatewayAuditRecord, GatewayAuditSink
from omnibase.capability_gateway.contracts import (
    ArtifactReadRequest,
    ArtifactReadResponse,
    ArtifactWriteRequest,
    ArtifactWriteResponse,
    DerivedCreateRequest,
    DerivedCreateResponse,
    DerivedDeleteRequest,
    DerivedDeleteResponse,
    GatewayAction,
    PrivateRowsMutationRequest,
    PrivateRowsMutationResponse,
    ResourceDescriptor,
    VerifiedCapability,
    WorkloadCredential,
    WorkspaceDataWriteResult,
)
from omnibase.capability_gateway.policy import PolicyDenial, authorize_resource
from omnibase.capability_gateway.resolver import ResourceResolutionError, ResourceResolver
from omnibase.capability_gateway.security import (
    CapabilityBudgetError,
    CapabilityScopeError,
    CapabilityVerificationError,
    CapabilityVerifier,
    WorkspaceDataConflictError,
)
from omnibase.capability_gateway.service import GatewayFailure
from omnibase.capability_gateway.write_adapters import (
    UnavailableWorkspaceDataAdapter,
    WorkspaceDataAdapter,
    WorkspaceDataAdapterError,
    WorkspaceDataEffectUnknown,
)

MutationAction = Literal["data.rows.insert", "data.rows.update", "data.rows.delete"]

_MUTATION_ACTION: dict[str, MutationAction] = {
    "insert": "data.rows.insert",
    "update": "data.rows.update",
    "delete": "data.rows.delete",
}


def _base64_encoded_size(raw_size: int) -> int:
    """Return the exact ASCII byte length for canonical base64 output."""

    return 4 * ((raw_size + 2) // 3)


@dataclass(frozen=True)
class WorkspaceDataGatewayComponents:
    verifier: CapabilityVerifier
    resolver: ResourceResolver
    adapter: WorkspaceDataAdapter | UnavailableWorkspaceDataAdapter
    audit_sink: GatewayAuditSink
    audit_session_factory: Callable[[], Session]


class WorkspaceDataGatewayService:
    def __init__(self, components: WorkspaceDataGatewayComponents) -> None:
        self._components = components

    def _authenticate(
        self,
        session: Session,
        credential: WorkloadCredential,
        *,
        action: GatewayAction,
        resource_id: str,
    ) -> VerifiedCapability:
        try:
            return self._components.verifier.verify(
                session,
                credential,
                action=action,
                resource_id=resource_id,
            )
        except CapabilityScopeError as exc:
            raise GatewayFailure(403, "capability_scope_denied", "Capability scope denied") from exc
        except CapabilityVerificationError as exc:
            raise GatewayFailure(
                401,
                "invalid_capability",
                "Capability authentication failed",
            ) from exc

    def _resource(
        self,
        session: Session,
        *,
        capability: VerifiedCapability,
        resource_id: str,
        action: GatewayAction,
    ) -> ResourceDescriptor:
        try:
            resource = self._components.resolver.resolve(
                session,
                capability=capability,
                resource_id=resource_id,
            )
            authorize_resource(capability, resource, action)
            return resource
        except ResourceResolutionError as exc:
            raise GatewayFailure(404, "resource_not_found", "Resource not found") from exc
        except PolicyDenial as exc:
            status = 404 if exc.code == "resource_not_found" else 403
            code = "resource_not_found" if status == 404 else "capability_scope_denied"
            message = "Resource not found" if status == 404 else "Capability scope denied"
            raise GatewayFailure(status, code, message) from exc

    @staticmethod
    def _operation_id(capability: VerifiedCapability, action: str, idempotency_key: str) -> str:
        return str(
            uuid5(
                NAMESPACE_URL,
                "omnibase:workspace-data:"
                f"{capability.tenant_id}:{capability.workspace_id}:{capability.grant_id}:"
                f"{action}:{idempotency_key}",
            )
        )

    @staticmethod
    def _result_digest(result: WorkspaceDataWriteResult) -> str:
        payload = json.dumps(
            {
                "action": result.action,
                "affected_rows": result.affected_rows,
                "chunk_count": result.chunk_count,
                "content_sha256": result.content_sha256,
                "deleted": result.deleted,
                "media_type": result.media_type,
                "operation_id": result.operation_id,
                "resource_id": result.resource_id,
                "resource_version": result.resource_version,
                "size_bytes": result.size_bytes,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _reserve(
        self,
        session: Session,
        credential: WorkloadCredential,
        capability: VerifiedCapability,
        *,
        action: GatewayAction,
        resource: ResourceDescriptor,
        payload: BaseModel,
        idempotency_key: str,
        bytes_out_reserved: int = 0,
    ) -> VerifiedWorkspaceDataCapabilityFacts:
        serialized = payload.model_dump_json().encode("utf-8")
        operation_id = self._operation_id(capability, action, idempotency_key)
        try:
            facts = self._components.verifier.reserve_workspace_data(
                session,
                credential,
                capability,
                operation_id=operation_id,
                request_hash=hashlib.sha256(serialized).hexdigest(),
                action=action,
                resource_id=resource.id,
                resource_version=resource.version,
                bytes_in=len(serialized),
                bytes_out_reserved=bytes_out_reserved,
                cost_units=1,
            )
        except CapabilityBudgetError as exc:
            raise GatewayFailure(
                429,
                "capability_budget_exceeded",
                "Capability budget exceeded",
            ) from exc
        except WorkspaceDataConflictError as exc:
            code = (
                "workspace_data_reconciliation_required"
                if "reconciliation_required" in str(exc)
                else "workspace_data_binding_conflict"
            )
            raise GatewayFailure(
                409,
                code,
                "Workspace data operation requires reconciliation",
            ) from exc
        except CapabilityScopeError as exc:
            raise GatewayFailure(403, "capability_scope_denied", "Capability scope denied") from exc
        if not isinstance(facts, VerifiedWorkspaceDataCapabilityFacts):
            raise GatewayFailure(503, "write_verifier_unavailable", "Write service unavailable")
        return facts

    def _append_write_audit(
        self,
        session: Session,
        *,
        capability: VerifiedCapability,
        request_id: str,
        action: GatewayAction,
        resource_id: str,
        request_hash: str,
        operation_id: str,
        status_code: int,
        reason_code: str,
        bytes_in: int,
        result: WorkspaceDataWriteResult | None = None,
    ) -> None:
        self._components.audit_sink.append(
            session,
            capability=capability,
            record=GatewayAuditRecord(
                request_id=request_id,
                action=action,
                decision="allowed" if status_code < 400 else "error",
                status_code=status_code,
                input_hash=request_hash,
                resource_id=resource_id,
                reason_code=reason_code,
                duration_ms=0,
                bytes_in=bytes_in,
                bytes_out=result.size_bytes if result is not None else None,
                row_count=(result.affected_rows or result.chunk_count)
                if result is not None
                else None,
                risk_level="R1",
                operation_id=operation_id,
            ),
        )

    def _append_artifact_read_error_audit(
        self,
        *,
        capability: VerifiedCapability,
        request_id: str,
        resource_id: str,
        input_hash: str,
        bytes_in: int,
        status_code: int = 503,
        reason_code: str = "artifact_read_adapter_unavailable",
    ) -> None:
        audit_session = self._components.audit_session_factory()
        try:
            self._components.audit_sink.append(
                audit_session,
                capability=capability,
                record=GatewayAuditRecord(
                    request_id=request_id,
                    action="artifact.read",
                    decision="error",
                    status_code=status_code,
                    input_hash=input_hash,
                    resource_id=resource_id,
                    reason_code=reason_code,
                    duration_ms=0,
                    bytes_in=bytes_in,
                    risk_level="R0",
                ),
            )
            audit_session.commit()
        except Exception:
            audit_session.rollback()
        finally:
            audit_session.close()

    def _mark_external_effect_unknown_best_effort(
        self,
        *,
        capability: VerifiedCapability,
        request_id: str,
        action: GatewayAction,
        resource_id: str,
        request_hash: str,
        operation_id: str,
        bytes_in: int,
    ) -> None:
        """Persist no-replay evidence in a fresh transaction when possible.

        A provider boundary may have committed even when finalization, Audit,
        or the caller-owned transaction failed.  Failure to record ``unknown``
        leaves the original durable ``pending`` reservation in place; both
        states remain non-replayable and require reconciliation.
        """

        recovery_session = self._components.audit_session_factory()
        try:
            self._components.verifier.finalize_workspace_data(
                recovery_session,
                operation_id=operation_id,
                final_state="unknown",
                result_digest=None,
            )
            self._append_write_audit(
                recovery_session,
                capability=capability,
                request_id=request_id,
                action=action,
                resource_id=resource_id,
                request_hash=request_hash,
                operation_id=operation_id,
                status_code=503,
                reason_code="effect_unknown",
                bytes_in=bytes_in,
            )
            recovery_session.commit()
        except Exception:
            recovery_session.rollback()
        finally:
            recovery_session.close()

    def _revalidate_external_binding(
        self,
        session: Session,
        *,
        live_revalidator: Callable[[], WorkloadCredential] | None,
        action: GatewayAction,
        capability: VerifiedCapability,
        resource: ResourceDescriptor,
    ) -> None:
        if live_revalidator is None:
            raise WorkspaceDataEffectUnknown("live revalidation is unavailable")
        try:
            fresh_credential = live_revalidator()
            fresh_capability = self._authenticate(
                session,
                fresh_credential,
                action=action,
                resource_id=resource.id,
            )
            fresh_resource = self._resource(
                session,
                capability=fresh_capability,
                resource_id=resource.id,
                action=action,
            )
        except Exception as exc:
            raise WorkspaceDataEffectUnknown("live revalidation failed") from exc
        if (
            fresh_capability.tenant_id,
            fresh_capability.workspace_id,
            fresh_capability.runtime_instance_id,
            fresh_capability.grant_id,
            fresh_capability.actor_user_id,
            fresh_resource.id,
            fresh_resource.version,
            fresh_resource.policy_class,
        ) != (
            capability.tenant_id,
            capability.workspace_id,
            capability.runtime_instance_id,
            capability.grant_id,
            capability.actor_user_id,
            resource.id,
            resource.version,
            resource.policy_class,
        ):
            raise WorkspaceDataEffectUnknown("live binding changed")

    def _write(
        self,
        session: Session,
        credential: WorkloadCredential,
        *,
        action: GatewayAction,
        resource_id: str,
        payload: BaseModel,
        idempotency_key: str,
        request_id: str,
        external_effect: bool,
        live_revalidator: Callable[[], WorkloadCredential] | None,
        operation: Callable[
            [VerifiedCapability, VerifiedWorkspaceDataCapabilityFacts, ResourceDescriptor],
            WorkspaceDataWriteResult,
        ],
    ) -> WorkspaceDataWriteResult:
        adapter = self._components.adapter
        if adapter.supports_workspace_data_effects is not True:
            raise GatewayFailure(503, "write_adapter_unavailable", "Write service unavailable")
        capability = self._authenticate(
            session,
            credential,
            action=action,
            resource_id=resource_id,
        )
        resource = self._resource(
            session,
            capability=capability,
            resource_id=resource_id,
            action=action,
        )
        serialized = payload.model_dump_json().encode("utf-8")
        request_hash = hashlib.sha256(serialized).hexdigest()
        reservation = self._reserve(
            session,
            credential,
            capability,
            action=action,
            resource=resource,
            payload=payload,
            idempotency_key=idempotency_key,
        )
        if external_effect and not reservation.replayed:
            session.commit()
        provider_boundary_crossed = False
        try:
            if reservation.replayed:
                result = adapter.replay_workspace_data(
                    session,
                    capability=capability,
                    reservation=reservation,
                    resource=resource,
                )
            else:
                provider_boundary_crossed = external_effect
                result = operation(capability, reservation, resource)
            if external_effect and not reservation.replayed:
                self._revalidate_external_binding(
                    session,
                    live_revalidator=live_revalidator,
                    action=action,
                    capability=capability,
                    resource=resource,
                )
            if not reservation.replayed:
                self._components.verifier.finalize_workspace_data(
                    session,
                    operation_id=reservation.operation_id,
                    final_state="committed",
                    result_digest=self._result_digest(result),
                )
            self._append_write_audit(
                session,
                capability=capability,
                request_id=request_id,
                action=action,
                resource_id=result.resource_id,
                request_hash=request_hash,
                operation_id=reservation.operation_id,
                status_code=200,
                reason_code="allowed",
                bytes_in=len(serialized),
                result=result,
            )
            session.commit()
            return result
        except WorkspaceDataEffectUnknown as exc:
            session.rollback()
            if not reservation.replayed:
                self._mark_external_effect_unknown_best_effort(
                    capability=capability,
                    request_id=request_id,
                    action=action,
                    resource_id=resource.id,
                    request_hash=request_hash,
                    operation_id=reservation.operation_id,
                    bytes_in=len(serialized),
                )
            raise GatewayFailure(
                503,
                "workspace_data_effect_unknown",
                "Workspace data effect requires reconciliation",
            ) from exc
        except WorkspaceDataAdapterError as exc:
            session.rollback()
            if external_effect and not reservation.replayed:
                self._mark_external_effect_unknown_best_effort(
                    capability=capability,
                    request_id=request_id,
                    action=action,
                    resource_id=resource.id,
                    request_hash=request_hash,
                    operation_id=reservation.operation_id,
                    bytes_in=len(serialized),
                )
            raise GatewayFailure(
                503, "write_adapter_unavailable", "Write service unavailable"
            ) from exc
        except Exception as exc:
            session.rollback()
            if provider_boundary_crossed and not reservation.replayed:
                self._mark_external_effect_unknown_best_effort(
                    capability=capability,
                    request_id=request_id,
                    action=action,
                    resource_id=resource.id,
                    request_hash=request_hash,
                    operation_id=reservation.operation_id,
                    bytes_in=len(serialized),
                )
                raise GatewayFailure(
                    503,
                    "workspace_data_effect_unknown",
                    "Workspace data effect requires reconciliation",
                ) from exc
            raise GatewayFailure(
                503, "write_adapter_unavailable", "Write service unavailable"
            ) from exc

    def mutate_private_rows(
        self,
        session: Session,
        credential: WorkloadCredential,
        payload: PrivateRowsMutationRequest,
        request_id: str,
        *,
        live_revalidator: Callable[[], WorkloadCredential] | None = None,
    ) -> PrivateRowsMutationResponse:
        action = _MUTATION_ACTION[payload.mutation.kind]
        result = self._write(
            session,
            credential,
            action=action,
            resource_id=str(payload.mutation.resource_id),
            payload=payload,
            idempotency_key=payload.mutation.idempotency_key,
            request_id=request_id,
            external_effect=False,
            live_revalidator=live_revalidator,
            operation=lambda capability,
            reservation,
            resource: self._components.adapter.mutate_private_rows(
                session,
                capability=capability,
                reservation=reservation,
                resource=resource,
                payload=payload,
                request_id=request_id,
            ),
        )
        return PrivateRowsMutationResponse(
            operation_id=UUID(result.operation_id),
            resource_id=UUID(result.resource_id),
            resource_version=result.resource_version,
            action=cast("MutationAction", result.action),
            affected_rows=result.affected_rows,
            replayed=result.replayed,
            request_id=request_id,
        )

    def read_artifact(
        self,
        session: Session,
        credential: WorkloadCredential,
        payload: ArtifactReadRequest,
        request_id: str,
    ) -> ArtifactReadResponse:
        capability = self._authenticate(
            session,
            credential,
            action="artifact.read",
            resource_id=str(payload.resource_id),
        )
        resource = self._resource(
            session,
            capability=capability,
            resource_id=str(payload.resource_id),
            action="artifact.read",
        )
        if resource.version != payload.resource_version:
            raise GatewayFailure(409, "resource_version_conflict", "Resource version changed")
        serialized = payload.model_dump_json().encode("utf-8")
        input_hash = hashlib.sha256(serialized).hexdigest()
        try:
            self._components.verifier.consume_budget(
                session,
                capability,
                calls=1,
                bytes_in=len(serialized),
                bytes_out_reserved=_base64_encoded_size(payload.max_bytes),
            )
            session.commit()
            result = self._components.adapter.read_artifact(
                session,
                capability=capability,
                resource=resource,
                payload=payload,
            )
        except CapabilityBudgetError as exc:
            session.rollback()
            raise GatewayFailure(
                429, "capability_budget_exceeded", "Capability budget exceeded"
            ) from exc
        except WorkspaceDataAdapterError as exc:
            session.rollback()
            self._append_artifact_read_error_audit(
                capability=capability,
                request_id=request_id,
                resource_id=resource.id,
                input_hash=input_hash,
                bytes_in=len(serialized),
            )
            raise GatewayFailure(
                503, "write_adapter_unavailable", "Read adapter unavailable"
            ) from exc
        post_read_failure: tuple[int, str, str] | None = None
        if result.resource_version != resource.version:
            post_read_failure = (
                409,
                "resource_version_conflict",
                "Resource version changed",
            )
        elif len(result.content) > payload.max_bytes:
            post_read_failure = (413, "result_too_large", "Result exceeds response budget")
        elif hashlib.sha256(result.content).hexdigest() != result.content_sha256:
            post_read_failure = (
                503,
                "artifact_integrity_failed",
                "Artifact integrity verification failed",
            )
        if post_read_failure is not None:
            status_code, code, message = post_read_failure
            session.rollback()
            self._append_artifact_read_error_audit(
                capability=capability,
                request_id=request_id,
                resource_id=resource.id,
                input_hash=input_hash,
                bytes_in=len(serialized),
                status_code=status_code,
                reason_code=code,
            )
            raise GatewayFailure(status_code, code, message)
        encoded = base64.b64encode(result.content).decode("ascii")
        encoded_size = len(encoded)
        try:
            self._components.audit_sink.append(
                session,
                capability=capability,
                record=GatewayAuditRecord(
                    request_id=request_id,
                    action="artifact.read",
                    decision="allowed",
                    status_code=200,
                    input_hash=input_hash,
                    resource_id=resource.id,
                    reason_code="allowed",
                    duration_ms=0,
                    bytes_in=len(serialized),
                    bytes_out=encoded_size,
                    risk_level="R0",
                ),
            )
            session.commit()
        except Exception as exc:
            session.rollback()
            self._append_artifact_read_error_audit(
                capability=capability,
                request_id=request_id,
                resource_id=resource.id,
                input_hash=input_hash,
                bytes_in=len(serialized),
                reason_code="artifact_read_audit_failed",
            )
            raise GatewayFailure(
                503, "artifact_read_audit_failed", "Artifact read audit failed"
            ) from exc
        return ArtifactReadResponse(
            resource_id=payload.resource_id,
            resource_version=result.resource_version,
            media_type=result.media_type,
            size_bytes=len(result.content),
            content_sha256=result.content_sha256,
            content_base64=encoded,
            bytes_out=encoded_size,
        )

    def write_artifact(
        self,
        session: Session,
        credential: WorkloadCredential,
        payload: ArtifactWriteRequest,
        request_id: str,
        *,
        live_revalidator: Callable[[], WorkloadCredential] | None = None,
    ) -> ArtifactWriteResponse:
        workspace_id = credential.trusted_context.workspace_id
        result = self._write(
            session,
            credential,
            action="artifact.write",
            resource_id=workspace_id,
            payload=payload,
            idempotency_key=payload.idempotency_key,
            request_id=request_id,
            external_effect=True,
            live_revalidator=live_revalidator,
            operation=lambda capability,
            reservation,
            resource: self._components.adapter.write_artifact(
                session,
                capability=capability,
                reservation=reservation,
                workspace=resource,
                payload=payload,
                request_id=request_id,
            ),
        )
        return ArtifactWriteResponse(
            operation_id=UUID(result.operation_id),
            resource_id=UUID(result.resource_id),
            resource_version=result.resource_version,
            media_type=result.media_type or payload.media_type,
            size_bytes=result.size_bytes if result.size_bytes is not None else payload.size_bytes,
            content_sha256=result.content_sha256 or payload.content_sha256,
            replayed=result.replayed,
            request_id=request_id,
        )

    def create_derived(
        self,
        session: Session,
        credential: WorkloadCredential,
        payload: DerivedCreateRequest,
        request_id: str,
        *,
        live_revalidator: Callable[[], WorkloadCredential] | None = None,
    ) -> DerivedCreateResponse:
        workspace_id = credential.trusted_context.workspace_id
        result = self._write(
            session,
            credential,
            action="rag.derived.create",
            resource_id=workspace_id,
            payload=payload,
            idempotency_key=payload.idempotency_key,
            request_id=request_id,
            external_effect=False,
            live_revalidator=live_revalidator,
            operation=lambda capability,
            reservation,
            resource: self._components.adapter.create_derived(
                session,
                capability=capability,
                reservation=reservation,
                workspace=resource,
                payload=payload,
                request_id=request_id,
            ),
        )
        return DerivedCreateResponse(
            operation_id=UUID(result.operation_id),
            resource_id=UUID(result.resource_id),
            resource_version=result.resource_version,
            chunk_count=result.chunk_count,
            replayed=result.replayed,
            request_id=request_id,
        )

    def delete_derived(
        self,
        session: Session,
        credential: WorkloadCredential,
        payload: DerivedDeleteRequest,
        request_id: str,
        *,
        live_revalidator: Callable[[], WorkloadCredential] | None = None,
    ) -> DerivedDeleteResponse:
        result = self._write(
            session,
            credential,
            action="rag.derived.delete",
            resource_id=str(payload.resource_id),
            payload=payload,
            idempotency_key=payload.idempotency_key,
            request_id=request_id,
            external_effect=False,
            live_revalidator=live_revalidator,
            operation=lambda capability,
            reservation,
            resource: self._components.adapter.delete_derived(
                session,
                capability=capability,
                reservation=reservation,
                resource=resource,
                payload=payload,
                request_id=request_id,
            ),
        )
        return DerivedDeleteResponse(
            operation_id=UUID(result.operation_id),
            resource_id=UUID(result.resource_id),
            resource_version=result.resource_version,
            deleted=result.deleted,
            replayed=result.replayed,
            request_id=request_id,
        )


__all__ = ["WorkspaceDataGatewayComponents", "WorkspaceDataGatewayService"]
