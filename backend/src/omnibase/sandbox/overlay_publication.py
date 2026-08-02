"""Composition seam from trusted Overlay publication to Broker service state.

Only logical metadata crosses this boundary.  Physical Overlay addresses,
routes, provider handles and credentials remain inside trusted Broker/Node
Daemon implementations and are never represented in Sandbox-facing DTOs.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from omnibase.sandbox.contracts import SandboxRejected, SandboxUnavailable
from omnibase.sandbox.network import LogicalNetworkService, NetworkProtocol
from omnibase.workspaces.overlay_adapters.contracts import (
    OverlayLogicalServicePublication,
    OverlayRejected,
)


class OverlayLogicalServiceMapper(Protocol):
    def map_publication(
        self,
        publication: OverlayLogicalServicePublication,
    ) -> LogicalNetworkService: ...


class RejectingOverlayLogicalServiceMapper:
    def map_publication(
        self,
        publication: OverlayLogicalServicePublication,
    ) -> LogicalNetworkService:
        del publication
        raise SandboxUnavailable("overlay_logical_service_mapper_unavailable")


class VerifiedOverlayLogicalServiceMapper:
    """Pure mapper for a publication already emitted by a trusted adapter."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._clock = clock

    def map_publication(
        self,
        publication: OverlayLogicalServicePublication,
    ) -> LogicalNetworkService:
        if not isinstance(publication, OverlayLogicalServicePublication):
            raise TypeError("publication must be OverlayLogicalServicePublication")
        try:
            publication.verify(now=self._clock())
            protocol = NetworkProtocol(publication.transport_protocol)
        except (OverlayRejected, ValueError) as exc:
            raise SandboxRejected("overlay_logical_service_publication_rejected") from exc
        return LogicalNetworkService(
            service_id=UUID(publication.service_id),
            tenant_id=UUID(publication.tenant_id),
            workspace_id=UUID(publication.workspace_id),
            publisher_node_id=UUID(publication.publisher_node_id),
            logical_name=publication.logical_name,
            protocol=protocol,
            logical_port=publication.logical_port,
            workspace_generation=publication.workspace_generation,
            publisher_node_fencing_token=(publication.publisher_node_fencing_token),
            network_fencing_token=publication.network_fencing_token,
            service_version=publication.service_version,
            expires_at=publication.expires_at,
        )


__all__ = [
    "OverlayLogicalServiceMapper",
    "RejectingOverlayLogicalServiceMapper",
    "VerifiedOverlayLogicalServiceMapper",
]
