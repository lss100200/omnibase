"""Closed P34.2 read policy evaluated after capability verification."""

from __future__ import annotations

from dataclasses import dataclass

from omnibase.capability_gateway.contracts import (
    GatewayAction,
    ResourceDescriptor,
    VerifiedCapability,
)


@dataclass(frozen=True)
class PolicyDenial(Exception):
    code: str


_ACTION_KINDS: dict[str, frozenset[str]] = {
    "data.schema.read": frozenset({"data_table", "data_view"}),
    "data.rows.read": frozenset({"data_table", "data_view"}),
    "rag.search": frozenset({"corpus", "derived_index"}),
    "rag.citation.read": frozenset({"corpus", "document", "derived_index"}),
}
_ACTION_POLICIES: dict[str, frozenset[str]] = {
    "data.schema.read": frozenset(
        {
            "canonical_readonly",
            "tenant_managed",
            "controlled_shared",
            "workspace_private",
            "workspace_derived",
        }
    ),
    "data.rows.read": frozenset(
        {
            "canonical_readonly",
            "tenant_managed",
            "controlled_shared",
            "workspace_private",
            "workspace_derived",
        }
    ),
    "rag.search": frozenset({"canonical_readonly", "controlled_shared", "workspace_derived"}),
    "rag.citation.read": frozenset(
        {"canonical_readonly", "controlled_shared", "workspace_derived"}
    ),
}


def authorize_resource(
    capability: VerifiedCapability,
    resource: ResourceDescriptor,
    action: GatewayAction,
) -> None:
    """Apply exact action/resource scope and canonical/private invariants."""
    if action not in capability.actions:
        raise PolicyDenial("action_not_granted")
    if resource.id not in capability.resource_ids:
        # Hide whether a forged logical identifier exists.
        raise PolicyDenial("resource_not_found")
    if resource.tenant_id != capability.tenant_id:
        raise PolicyDenial("resource_not_found")
    if resource.state != "active":
        raise PolicyDenial("resource_not_found")
    if resource.kind not in _ACTION_KINDS[action]:
        raise PolicyDenial("action_not_allowed_for_resource")
    if resource.policy_class not in _ACTION_POLICIES[action]:
        raise PolicyDenial("policy_class_denied")
    if resource.policy_class in {"workspace_private", "workspace_derived"} and (
        resource.owner_type != "workspace" or resource.owner_id != capability.workspace_id
    ):
        raise PolicyDenial("resource_not_found")


__all__ = ["PolicyDenial", "authorize_resource"]
