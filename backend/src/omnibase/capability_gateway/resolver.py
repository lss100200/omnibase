"""Tenant-first logical resource resolver and adapter-only locator store."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.orm import Session

from omnibase.capability_gateway.contracts import ResourceDescriptor, VerifiedCapability
from omnibase.control_plane.models import ResourceRecord


class ResourceResolutionError(Exception):
    """Absent and cross-scope identifiers deliberately share this error."""


@runtime_checkable
class ResourceResolver(Protocol):
    def resolve(
        self,
        session: Session,
        *,
        capability: VerifiedCapability,
        resource_id: str,
    ) -> ResourceDescriptor: ...


@runtime_checkable
class PhysicalLocatorStore(Protocol):
    """This protocol is consumed only by domain adapters."""

    def get_locator(
        self,
        session: Session,
        *,
        capability: VerifiedCapability,
        resource: ResourceDescriptor,
    ) -> dict[str, object]: ...


@dataclass(frozen=True)
class RegistryResourceResolver:
    """Resolve logical records without returning their physical locator."""

    def _load(
        self,
        session: Session,
        *,
        capability: VerifiedCapability,
        resource_id: str,
    ) -> ResourceRecord:
        record = session.scalar(
            select(ResourceRecord).where(
                ResourceRecord.tenant_id == capability.tenant_id,
                ResourceRecord.id == resource_id,
            )
        )
        if record is None:
            raise ResourceResolutionError
        return record

    def resolve(
        self,
        session: Session,
        *,
        capability: VerifiedCapability,
        resource_id: str,
    ) -> ResourceDescriptor:
        record = self._load(
            session,
            capability=capability,
            resource_id=resource_id,
        )
        return ResourceDescriptor(
            id=str(record.id),
            tenant_id=str(record.tenant_id),
            kind=record.kind,
            owner_type=record.owner_type,
            owner_id=str(record.owner_id) if record.owner_id is not None else None,
            parent_id=str(record.parent_id) if record.parent_id is not None else None,
            state=record.state,
            version=record.version,
            policy_class=record.policy_class,
        )

    def get_locator(
        self,
        session: Session,
        *,
        capability: VerifiedCapability,
        resource: ResourceDescriptor,
    ) -> dict[str, object]:
        record = self._load(
            session,
            capability=capability,
            resource_id=resource.id,
        )
        if record.version != resource.version or not isinstance(record.physical_locator, dict):
            raise ResourceResolutionError
        return record.physical_locator


__all__ = [
    "PhysicalLocatorStore",
    "RegistryResourceResolver",
    "ResourceResolutionError",
    "ResourceResolver",
]
