"""SQLAlchemy-backed P34.5 durable operation store.

Every method owns one short transaction, locks the current operation pointer,
appends immutable transition evidence and writes a redacted control-plane Audit
event before commit.  It never contacts a Runner or runtime provider.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from omnibase.control_plane.service import ControlPlaneError, append_audit_event
from omnibase.sandbox.contracts import SandboxConflict, SandboxError, SandboxUnavailable
from omnibase.sandbox.models import SandboxOperation, SandboxOperationTransitionModel
from omnibase.sandbox.operations import (
    SandboxOperationIntent,
    SandboxOperationRecord,
    SandboxOperationState,
    SandboxOperationTransition,
    transition_allowed,
)

_TERMINAL = frozenset(
    {
        SandboxOperationState.SUCCEEDED,
        SandboxOperationState.FAILED,
        SandboxOperationState.RECONCILED_SUCCEEDED,
        SandboxOperationState.RECONCILED_FAILED,
    }
)
_ALLOWED_AUDIT_STATES = frozenset(
    {
        SandboxOperationState.ACCEPTED,
        SandboxOperationState.AUTHORIZED,
        SandboxOperationState.DISPATCHING,
        SandboxOperationState.SUCCEEDED,
        SandboxOperationState.RECONCILED_SUCCEEDED,
    }
)


class SqlAlchemySandboxOperationStore:
    """Production durable store with append-only transition and Audit evidence."""

    def __init__(self, *, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    @contextmanager
    def _transaction(self) -> Iterator[Session]:
        try:
            with self._session_factory() as session, session.begin():
                yield session
        except SandboxError:
            raise
        except ControlPlaneError as exc:
            raise SandboxConflict("sandbox_operation_audit_binding_rejected") from exc
        except SQLAlchemyError as exc:
            raise SandboxUnavailable("sandbox_operation_store_failed") from exc

    def begin(self, intent: SandboxOperationIntent) -> SandboxOperationRecord:
        with self._transaction() as session:
            inserted = session.execute(
                pg_insert(SandboxOperation)
                .values(
                    operation_id=str(intent.operation_id),
                    tenant_id=str(intent.tenant_id),
                    workspace_id=str(intent.workspace_id),
                    run_id=str(intent.run_id),
                    runtime_instance_id=str(intent.runtime_instance_id),
                    capability_grant_id=(
                        str(intent.capability_grant_id)
                        if intent.capability_grant_id is not None
                        else None
                    ),
                    action=intent.action,
                    request_digest=intent.request_digest,
                    spec_digest=intent.spec_digest,
                    workspace_generation=intent.workspace_generation,
                    run_fencing_token=intent.run_fencing_token,
                    node_fencing_token=intent.node_fencing_token,
                    state=SandboxOperationState.ACCEPTED.value,
                    version=1,
                )
                .on_conflict_do_nothing(index_elements=[SandboxOperation.operation_id])
                .returning(SandboxOperation.operation_id)
            ).scalar_one_or_none()
            operation = self._get_locked(session, intent.operation_id)
            if self._intent(operation) != intent:
                raise SandboxConflict("sandbox_operation_payload_drift")
            if inserted is not None:
                transition = SandboxOperationTransitionModel(
                    operation_id=operation.operation_id,
                    tenant_id=operation.tenant_id,
                    sequence=1,
                    state=SandboxOperationState.ACCEPTED.value,
                    reason_code="operation_accepted",
                    evidence_digest=None,
                )
                session.add(transition)
                self._audit_transition(
                    session,
                    operation=operation,
                    previous_state=None,
                    target=SandboxOperationState.ACCEPTED,
                    reason_code="operation_accepted",
                    before_version=None,
                    after_version=1,
                )
                session.flush()
            return self._record(session, operation)

    def authorize(
        self,
        operation_id: UUID,
        *,
        evidence_digest: str,
    ) -> SandboxOperationRecord:
        return self._append(
            operation_id,
            SandboxOperationState.AUTHORIZED,
            reason_code="operation_authorized",
            evidence_digest=evidence_digest,
            idempotent_same_evidence=True,
        )

    def claim_dispatch(self, operation_id: UUID) -> SandboxOperationRecord:
        return self._append(
            operation_id,
            SandboxOperationState.DISPATCHING,
            reason_code="provider_dispatch_started",
        )

    def succeed(
        self,
        operation_id: UUID,
        *,
        evidence_digest: str,
    ) -> SandboxOperationRecord:
        return self._append(
            operation_id,
            SandboxOperationState.SUCCEEDED,
            reason_code="provider_succeeded",
            evidence_digest=evidence_digest,
        )

    def fail(self, operation_id: UUID, *, reason_code: str) -> SandboxOperationRecord:
        return self._append(
            operation_id,
            SandboxOperationState.FAILED,
            reason_code=reason_code,
        )

    def mark_ambiguous(self, operation_id: UUID) -> SandboxOperationRecord:
        return self._append(
            operation_id,
            SandboxOperationState.AMBIGUOUS,
            reason_code="provider_outcome_ambiguous",
        )

    def require_reconciliation(self, operation_id: UUID) -> SandboxOperationRecord:
        return self._append(
            operation_id,
            SandboxOperationState.RECONCILIATION_REQUIRED,
            reason_code="provider_reconciliation_required",
        )

    def reconcile(
        self,
        operation_id: UUID,
        *,
        succeeded: bool,
        evidence_digest: str,
    ) -> SandboxOperationRecord:
        return self._append(
            operation_id,
            (
                SandboxOperationState.RECONCILED_SUCCEEDED
                if succeeded
                else SandboxOperationState.RECONCILED_FAILED
            ),
            reason_code=(
                "provider_reconciled_succeeded" if succeeded else "provider_reconciled_failed"
            ),
            evidence_digest=evidence_digest,
        )

    def get(self, operation_id: UUID) -> SandboxOperationRecord:
        with self._transaction() as session:
            operation = session.execute(
                select(SandboxOperation).where(SandboxOperation.operation_id == str(operation_id))
            ).scalar_one_or_none()
            if operation is None:
                raise SandboxConflict("sandbox_operation_not_found")
            return self._record(session, operation)

    def _append(
        self,
        operation_id: UUID,
        target: SandboxOperationState,
        *,
        reason_code: str,
        evidence_digest: str | None = None,
        idempotent_same_evidence: bool = False,
    ) -> SandboxOperationRecord:
        with self._transaction() as session:
            operation = self._get_locked(session, operation_id)
            current = SandboxOperationState(operation.state)
            if idempotent_same_evidence and current is target:
                record = self._record(session, operation)
                latest = record.transitions[-1]
                if latest.evidence_digest == evidence_digest:
                    return record
                raise SandboxConflict("sandbox_operation_authorization_drift")
            if current in _TERMINAL:
                raise SandboxConflict("sandbox_operation_terminal")
            if not transition_allowed(current, target):
                raise SandboxConflict("sandbox_operation_transition_rejected")

            before_version = operation.version
            operation.state = target.value
            operation.version += 1
            transition = SandboxOperationTransitionModel(
                operation_id=operation.operation_id,
                tenant_id=operation.tenant_id,
                sequence=operation.version,
                state=target.value,
                reason_code=reason_code,
                evidence_digest=evidence_digest,
            )
            session.add(transition)
            self._audit_transition(
                session,
                operation=operation,
                previous_state=current,
                target=target,
                reason_code=reason_code,
                before_version=before_version,
                after_version=operation.version,
            )
            session.flush()
            return self._record(session, operation)

    @staticmethod
    def _get_locked(session: Session, operation_id: UUID) -> SandboxOperation:
        operation = session.execute(
            select(SandboxOperation)
            .where(SandboxOperation.operation_id == str(operation_id))
            .with_for_update()
        ).scalar_one_or_none()
        if operation is None:
            raise SandboxConflict("sandbox_operation_not_found")
        return operation

    @staticmethod
    def _intent(operation: SandboxOperation) -> SandboxOperationIntent:
        return SandboxOperationIntent(
            operation_id=UUID(operation.operation_id),
            tenant_id=UUID(operation.tenant_id),
            workspace_id=UUID(operation.workspace_id),
            run_id=UUID(operation.run_id),
            runtime_instance_id=UUID(operation.runtime_instance_id),
            capability_grant_id=(
                UUID(operation.capability_grant_id)
                if operation.capability_grant_id is not None
                else None
            ),
            workspace_generation=operation.workspace_generation,
            run_fencing_token=operation.run_fencing_token,
            node_fencing_token=operation.node_fencing_token,
            action=operation.action,
            request_digest=operation.request_digest,
            spec_digest=operation.spec_digest,
        )

    @classmethod
    def _record(
        cls,
        session: Session,
        operation: SandboxOperation,
    ) -> SandboxOperationRecord:
        rows = list(
            session.scalars(
                select(SandboxOperationTransitionModel)
                .where(
                    SandboxOperationTransitionModel.operation_id == operation.operation_id,
                    SandboxOperationTransitionModel.tenant_id == operation.tenant_id,
                )
                .order_by(SandboxOperationTransitionModel.sequence)
            )
        )
        return SandboxOperationRecord(
            intent=cls._intent(operation),
            transitions=tuple(
                SandboxOperationTransition(
                    sequence=row.sequence,
                    state=SandboxOperationState(row.state),
                    recorded_at=row.recorded_at,
                    reason_code=row.reason_code,
                    evidence_digest=row.evidence_digest,
                )
                for row in rows
            ),
        )

    @staticmethod
    def _audit_transition(
        session: Session,
        *,
        operation: SandboxOperation,
        previous_state: SandboxOperationState | None,
        target: SandboxOperationState,
        reason_code: str,
        before_version: int | None,
        after_version: int,
    ) -> None:
        append_audit_event(
            session,
            tenant_id=operation.tenant_id,
            request_id=operation.operation_id,
            actor_type="system",
            actor_id=None,
            workspace_id=operation.workspace_id,
            run_id=operation.run_id,
            grant_id=operation.capability_grant_id,
            resource_id=operation.workspace_id,
            action=f"{operation.action}.{target.value}",
            decision="allowed" if target in _ALLOWED_AUDIT_STATES else "error",
            risk_level="R1",
            input_hash=operation.request_digest,
            before_version=before_version,
            after_version=after_version,
            details={
                "from_state": previous_state.value if previous_state is not None else "none",
                "to_state": target.value,
                "reason_code": reason_code,
            },
        )


__all__ = ["SqlAlchemySandboxOperationStore"]
