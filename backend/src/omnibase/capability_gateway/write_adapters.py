"""Fail-closed P34.6 workspace-data adapter contracts.

Adapters receive only verified logical scope and strict DTOs.  They never
return physical locators, object-store credentials, SQL, or canonical storage
handles.  External-effect adapters must report ambiguous outcomes explicitly;
callers must persist ``unknown`` and must not replay them automatically.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from sqlalchemy.orm import Session

from omnibase.capabilities.service import VerifiedWorkspaceDataCapabilityFacts
from omnibase.capability_gateway.contracts import (
    ArtifactReadRequest,
    ArtifactReadResult,
    ArtifactWriteRequest,
    DerivedCreateRequest,
    DerivedDeleteRequest,
    PrivateRowsMutationRequest,
    ResourceDescriptor,
    VerifiedCapability,
    WorkspaceDataWriteResult,
)


class WorkspaceDataAdapterError(Exception):
    """Sanitized adapter failure with no raw infrastructure detail."""


class WorkspaceDataEffectUnknown(WorkspaceDataAdapterError):
    """The external boundary may have accepted the effect; replay is forbidden."""


class UnavailableWorkspaceDataAdapter:
    """Default adapter: no private write/read capability is installed."""

    supports_workspace_data_effects: Literal[False] = False

    def mutate_private_rows(self, *args, **kwargs):
        del args, kwargs
        raise WorkspaceDataAdapterError

    def read_artifact(self, *args, **kwargs):
        del args, kwargs
        raise WorkspaceDataAdapterError

    def write_artifact(self, *args, **kwargs):
        del args, kwargs
        raise WorkspaceDataAdapterError

    def create_derived(self, *args, **kwargs):
        del args, kwargs
        raise WorkspaceDataAdapterError

    def delete_derived(self, *args, **kwargs):
        del args, kwargs
        raise WorkspaceDataAdapterError

    def replay_workspace_data(self, *args, **kwargs):
        del args, kwargs
        raise WorkspaceDataAdapterError


@runtime_checkable
class WorkspaceDataAdapter(Protocol):
    """Trusted adapter marker plus effect and exact-replay contracts."""

    supports_workspace_data_effects: Literal[True]

    def replay_workspace_data(
        self,
        session: Session,
        *,
        capability: VerifiedCapability,
        reservation: VerifiedWorkspaceDataCapabilityFacts,
        resource: ResourceDescriptor,
    ) -> WorkspaceDataWriteResult: ...

    def mutate_private_rows(
        self,
        session: Session,
        *,
        capability: VerifiedCapability,
        reservation: VerifiedWorkspaceDataCapabilityFacts,
        resource: ResourceDescriptor,
        payload: PrivateRowsMutationRequest,
        request_id: str,
    ) -> WorkspaceDataWriteResult: ...

    def read_artifact(
        self,
        session: Session,
        *,
        capability: VerifiedCapability,
        resource: ResourceDescriptor,
        payload: ArtifactReadRequest,
    ) -> ArtifactReadResult: ...

    def write_artifact(
        self,
        session: Session,
        *,
        capability: VerifiedCapability,
        reservation: VerifiedWorkspaceDataCapabilityFacts,
        workspace: ResourceDescriptor,
        payload: ArtifactWriteRequest,
        request_id: str,
    ) -> WorkspaceDataWriteResult: ...

    def create_derived(
        self,
        session: Session,
        *,
        capability: VerifiedCapability,
        reservation: VerifiedWorkspaceDataCapabilityFacts,
        workspace: ResourceDescriptor,
        payload: DerivedCreateRequest,
        request_id: str,
    ) -> WorkspaceDataWriteResult: ...

    def delete_derived(
        self,
        session: Session,
        *,
        capability: VerifiedCapability,
        reservation: VerifiedWorkspaceDataCapabilityFacts,
        resource: ResourceDescriptor,
        payload: DerivedDeleteRequest,
        request_id: str,
    ) -> WorkspaceDataWriteResult: ...


__all__ = [
    "UnavailableWorkspaceDataAdapter",
    "WorkspaceDataAdapter",
    "WorkspaceDataAdapterError",
    "WorkspaceDataEffectUnknown",
]
