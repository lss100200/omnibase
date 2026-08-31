"""Source-owned P7.3 component manifests.

The catalog deliberately contains data only.  Entrypoints are identifiers for
closed host adapters; they are never paths, command lines, URLs, or renderer
code.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

_COMPONENT_ID = re.compile(r"^[a-z][a-z0-9.-]{2,127}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_HOST_ADAPTER_OUTPUT_BYTES_PER_CALL = 4_194_304
_OPERATION = re.compile(r"^[a-z][a-z0-9_.]{2,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

_MANIFEST_KEYS = {
    "budgets",
    "compatibility",
    "component_id",
    "configuration_schema",
    "conflicts",
    "dependencies",
    "entrypoint",
    "family",
    "health",
    "manifest_schema_version",
    "network",
    "operations",
    "permissions",
    "publisher",
    "quiesce_timeout_ms",
    "recovery",
    "slots",
    "state_migration",
    "state_schema",
    "uninstall",
    "version",
}
_ENTRYPOINTS = {
    "declarative_ui": {"adapter_id": "builtin-ui.v1", "kind": "host_view_v1"},
    "instruction_skill": {
        "adapter_id": "instruction-skill.v1",
        "kind": "instruction_v1",
    },
    "mcp_connector": {"adapter_id": "readonly-mcp.v1", "kind": "mcp_closed_v1"},
    "sandbox_workload": {
        "adapter_id": "p34-sandbox.v1",
        "kind": "sandbox_workflow_v1",
    },
    "trusted_local_adapter": {
        "adapter_id": "trusted-local-app.v1",
        "kind": "native_catalog_v1",
    },
}


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_closed_manifest(value: object) -> dict[str, object]:  # noqa: C901
    """Validate the source-owned manifest grammar without accepting executable locators."""

    if not isinstance(value, dict) or set(value) != _MANIFEST_KEYS:
        raise ValueError("component_manifest_fields_invalid")
    component_id = value.get("component_id")
    version = value.get("version")
    family = value.get("family")
    if not isinstance(component_id, str) or _COMPONENT_ID.fullmatch(component_id) is None:
        raise ValueError("component_manifest_id_invalid")
    if not isinstance(version, str) or _VERSION.fullmatch(version) is None:
        raise ValueError("component_manifest_version_invalid")
    if not isinstance(family, str) or family not in _ENTRYPOINTS:
        raise ValueError("component_manifest_family_invalid")
    if value.get("entrypoint") != _ENTRYPOINTS[family]:
        raise ValueError("component_manifest_entrypoint_invalid")
    publisher = value.get("publisher")
    if (
        not isinstance(publisher, dict)
        or set(publisher) != {"classification", "id"}
        or publisher.get("classification") not in {"source_owned", "owner_reviewed"}
        or not isinstance(publisher.get("id"), str)
        or not 3 <= len(str(publisher["id"])) <= 64
        or (publisher["classification"] == "source_owned" and publisher["id"] != "omnibase")
        or (publisher["classification"] == "owner_reviewed" and family != "declarative_ui")
    ):
        raise ValueError("component_manifest_publisher_invalid")
    operations = value.get("operations")
    if (
        not isinstance(operations, list)
        or not operations
        or len(operations) != len(set(operations))
        or any(
            not isinstance(item, str) or _OPERATION.fullmatch(item) is None for item in operations
        )
    ):
        raise ValueError("component_manifest_operations_invalid")
    budgets = value.get("budgets")
    budget_keys = {
        "max_bytes_in",
        "max_bytes_out",
        "max_calls",
        "max_concurrency",
        "max_cost_units",
        "max_retries",
        "max_tokens",
        "max_wall_time_ms",
    }
    if (
        not isinstance(budgets, dict)
        or set(budgets) != budget_keys
        or any(type(item) is not int or item < 0 for item in budgets.values())
        or budgets["max_calls"] < 1
        or budgets["max_concurrency"] < 1
        or budgets["max_wall_time_ms"] < 1
        or budgets["max_cost_units"] < 1
        or budgets["max_concurrency"] > 64
        or budgets["max_wall_time_ms"] > 86_400_000
        or budgets["max_bytes_in"] > 1_073_741_824
        or budgets["max_bytes_out"] > 1_073_741_824
        or budgets["max_tokens"] > 100_000_000
    ):
        raise ValueError("component_manifest_budgets_invalid")
    compatibility = value.get("compatibility")
    if compatibility != {"desktop_schema_min": 11, "host_api": "p7.3.v1"}:
        raise ValueError("component_manifest_compatibility_invalid")
    configuration = value.get("configuration_schema")
    if (
        not isinstance(configuration, dict)
        or set(configuration)
        != {"additional_properties", "kind", "properties", "required", "version"}
        or configuration["additional_properties"] is not False
        or configuration["kind"] != "closed_object"
        or type(configuration["version"]) is not int
        or configuration["version"] < 1
        or not isinstance(configuration["properties"], dict)
        or not isinstance(configuration["required"], list)
        or any(not isinstance(item, str) for item in configuration["required"])
        or not set(configuration["required"]).issubset(configuration["properties"])
    ):
        raise ValueError("component_manifest_configuration_schema_invalid")
    for name, specification in configuration["properties"].items():
        if (
            not isinstance(name, str)
            or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) is None
            or not isinstance(specification, dict)
            or not {"type"}.issubset(specification)
            or not set(specification).issubset(
                {"type", "default", "enum", "minimum", "maximum", "max_length"}
            )
            or specification["type"] not in {"boolean", "integer", "number", "string"}
            or (
                "enum" in specification
                and (
                    not isinstance(specification["enum"], list)
                    or not 1 <= len(specification["enum"]) <= 64
                )
            )
            or (
                "max_length" in specification
                and (
                    type(specification["max_length"]) is not int
                    or not 0 <= specification["max_length"] <= 4096
                )
            )
        ):
            raise ValueError("component_manifest_configuration_property_invalid")
    dependencies = value.get("dependencies")
    if not isinstance(dependencies, list) or len(dependencies) > 64:
        raise ValueError("component_manifest_dependencies_invalid")
    dependency_ids: set[str] = set()
    for dependency in dependencies:
        if (
            not isinstance(dependency, dict)
            or set(dependency)
            != {
                "component_id",
                "manifest_sha256",
                "package_sha256",
                "policy_manifest_sha256",
                "version",
            }
            or not isinstance(dependency["component_id"], str)
            or _COMPONENT_ID.fullmatch(dependency["component_id"]) is None
            or not isinstance(dependency["version"], str)
            or _VERSION.fullmatch(dependency["version"]) is None
            or any(
                not isinstance(dependency[key], str) or _SHA256.fullmatch(dependency[key]) is None
                for key in ("policy_manifest_sha256", "manifest_sha256", "package_sha256")
            )
        ):
            raise ValueError("component_manifest_dependency_invalid")
        dependency_id = str(dependency["component_id"])
        if dependency_id == component_id or dependency_id in dependency_ids:
            raise ValueError("component_manifest_dependencies_invalid")
        dependency_ids.add(dependency_id)
    conflicts = value.get("conflicts")
    if (
        not isinstance(conflicts, list)
        or len(conflicts) > 64
        or len(conflicts) != len(set(conflicts))
        or any(
            not isinstance(item, str) or _COMPONENT_ID.fullmatch(item) is None for item in conflicts
        )
        or component_id in conflicts
        or bool(dependency_ids.intersection(conflicts))
    ):
        raise ValueError("component_manifest_conflicts_invalid")
    slots = value.get("slots")
    if not isinstance(slots, list) or len(slots) > 64:
        raise ValueError("component_manifest_slots_invalid")
    slot_ids: set[str] = set()
    for slot in slots:
        if (
            not isinstance(slot, dict)
            or set(slot) != {"cardinality", "maximum_order", "minimum_order", "slot_id"}
            or slot["cardinality"] not in {"one", "many"}
            or not isinstance(slot["slot_id"], str)
            or re.fullmatch(r"[a-z][a-z0-9._-]{2,63}", slot["slot_id"]) is None
            or type(slot["minimum_order"]) is not int
            or type(slot["maximum_order"]) is not int
            or not 0 <= slot["minimum_order"] <= slot["maximum_order"] <= 10_000
            or slot["slot_id"] in slot_ids
        ):
            raise ValueError("component_manifest_slot_invalid")
        slot_ids.add(slot["slot_id"])
    permissions = value.get("permissions")
    if not isinstance(permissions, list) or len(permissions) != len(operations):
        raise ValueError("component_manifest_permissions_invalid")
    permission_actions: set[str] = set()
    for permission in permissions:
        if (
            not isinstance(permission, dict)
            or set(permission)
            != {"action", "data_scope", "logical_resource_classes", "secret_reference_classes"}
            or permission["action"] not in operations
            or permission["action"] in permission_actions
            or permission["data_scope"] not in {"none", "workspace_logical"}
            or not isinstance(permission["logical_resource_classes"], list)
            or not isinstance(permission["secret_reference_classes"], list)
            or any(not isinstance(item, str) for item in permission["logical_resource_classes"])
            or any(not isinstance(item, str) for item in permission["secret_reference_classes"])
        ):
            raise ValueError("component_manifest_permission_invalid")
        permission_actions.add(permission["action"])
    network = value.get("network")
    if (
        not isinstance(network, dict)
        or set(network) != {"required", "service_classes"}
        or type(network["required"]) is not bool
        or not isinstance(network["service_classes"], list)
        or any(item != "reviewed_https" for item in network["service_classes"])
        or bool(network["service_classes"]) != network["required"]
    ):
        raise ValueError("component_manifest_network_invalid")
    if value.get("health") != {
        "kind": "native_receipt_v1",
        "required_state": "healthy",
        "timeout_ms": 5_000,
    }:
        raise ValueError("component_manifest_health_invalid")
    if value.get("recovery") != {
        "auto_replay_unknown": False,
        "retention": "retain_workspace_data",
        "safe_mode": "disable_component",
    }:
        raise ValueError("component_manifest_recovery_invalid")
    state_schema = value.get("state_schema")
    if (
        not isinstance(state_schema, dict)
        or set(state_schema) != {"kind", "version"}
        or state_schema["kind"] != "canonical_json"
        or type(state_schema["version"]) is not int
        or state_schema["version"] < 1
    ):
        raise ValueError("component_manifest_state_schema_invalid")
    if value.get("state_migration") != {
        "kind": "host_canonical_v1",
        "requires_owner_review_on_schema_change": True,
    }:
        raise ValueError("component_manifest_state_migration_invalid")
    if value.get("uninstall") != {
        "retention": "retain_workspace_data",
        "unbound_delete_forbidden": True,
    }:
        raise ValueError("component_manifest_uninstall_invalid")
    if (
        type(value.get("quiesce_timeout_ms")) is not int
        or not 1 <= value["quiesce_timeout_ms"] <= 60_000
    ):
        raise ValueError("component_manifest_quiesce_invalid")
    if value.get("manifest_schema_version") != 1:
        raise ValueError("component_manifest_schema_version_invalid")
    encoded = canonical_json(value)
    forbidden = ("://", "javascript", "iframe", "<script", "argv", "command", "physical_path")
    if any(token in encoded.lower() for token in forbidden):
        raise ValueError("component_manifest_ambient_authority_forbidden")
    return value


@dataclass(frozen=True, slots=True)
class SeededComponentVersion:
    component_id: str
    version: str
    family: str
    display_name: str
    adapter_id: str
    entrypoint_kind: str
    operations: tuple[str, ...]
    requires_network: bool
    slots: tuple[str, ...]
    max_calls: int = 64
    max_bytes_in: int = 1_048_576
    max_bytes_out: int = 4_194_304
    max_tokens: int = 131_072
    max_wall_time_ms: int = 600_000
    max_cost_units: int = 1_000
    max_retries: int = 2
    max_concurrency: int = 2
    state_schema_version: int = 1
    configuration_schema_version: int = 1

    def __post_init__(self) -> None:
        if _COMPONENT_ID.fullmatch(self.component_id) is None:
            raise ValueError("seeded_component_id_invalid")
        if _VERSION.fullmatch(self.version) is None:
            raise ValueError("seeded_component_version_invalid")
        if not self.operations or any(
            _OPERATION.fullmatch(item) is None for item in self.operations
        ):
            raise ValueError("seeded_component_operations_invalid")
        if self.family not in {
            "declarative_ui",
            "instruction_skill",
            "mcp_connector",
            "sandbox_workload",
            "trusted_local_adapter",
        }:
            raise ValueError("seeded_component_family_invalid")
        entrypoints = {
            "declarative_ui": ("host_view_v1", "builtin-ui.v1"),
            "instruction_skill": ("instruction_v1", "instruction-skill.v1"),
            "mcp_connector": ("mcp_closed_v1", "readonly-mcp.v1"),
            "sandbox_workload": ("sandbox_workflow_v1", "p34-sandbox.v1"),
            "trusted_local_adapter": ("native_catalog_v1", "trusted-local-app.v1"),
        }
        if (self.entrypoint_kind, self.adapter_id) != entrypoints[self.family]:
            raise ValueError("seeded_component_entrypoint_invalid")
        if (
            type(self.state_schema_version) is not int
            or self.state_schema_version < 1
            or type(self.configuration_schema_version) is not int
            or self.configuration_schema_version < 1
        ):
            raise ValueError("seeded_component_schema_version_invalid")

    @property
    def manifest(self) -> dict[str, object]:
        return {
            "budgets": {
                "max_bytes_in": self.max_bytes_in,
                "max_bytes_out": self.max_bytes_out,
                "max_calls": self.max_calls,
                "max_concurrency": self.max_concurrency,
                "max_cost_units": self.max_cost_units,
                "max_retries": self.max_retries,
                "max_tokens": self.max_tokens,
                "max_wall_time_ms": self.max_wall_time_ms,
            },
            "component_id": self.component_id,
            "compatibility": {"desktop_schema_min": 11, "host_api": "p7.3.v1"},
            "configuration_schema": {
                "additional_properties": False,
                "kind": "closed_object",
                "properties": {},
                "required": [],
                "version": self.configuration_schema_version,
            },
            "conflicts": [],
            "dependencies": [],
            "family": self.family,
            "entrypoint": {
                "adapter_id": self.adapter_id,
                "kind": self.entrypoint_kind,
            },
            "health": {
                "kind": "native_receipt_v1",
                "required_state": "healthy",
                "timeout_ms": 5_000,
            },
            "manifest_schema_version": 1,
            "network": {
                "required": self.requires_network,
                "service_classes": ["reviewed_https"] if self.requires_network else [],
            },
            "operations": list(self.operations),
            "permissions": [
                {
                    "action": operation,
                    "data_scope": "workspace_logical",
                    "logical_resource_classes": ["workspace.component.input"],
                    "secret_reference_classes": [],
                }
                for operation in self.operations
            ],
            "publisher": {"classification": "source_owned", "id": "omnibase"},
            "recovery": {
                "auto_replay_unknown": False,
                "retention": "retain_workspace_data",
                "safe_mode": "disable_component",
            },
            "state_migration": {
                "kind": "host_canonical_v1",
                "requires_owner_review_on_schema_change": True,
            },
            "slots": [
                {
                    "cardinality": "many",
                    "maximum_order": 10_000,
                    "minimum_order": 0,
                    "slot_id": slot,
                }
                for slot in self.slots
            ],
            "state_schema": {
                "kind": "canonical_json",
                "version": self.state_schema_version,
            },
            "uninstall": {
                "retention": "retain_workspace_data",
                "unbound_delete_forbidden": True,
            },
            "quiesce_timeout_ms": 5_000,
            "version": self.version,
        }

    @property
    def manifest_json(self) -> str:
        return canonical_json(validate_closed_manifest(self.manifest))

    @property
    def manifest_sha256(self) -> str:
        return hashlib.sha256(self.manifest_json.encode("utf-8")).hexdigest()

    @property
    def package_sha256(self) -> str:
        return digest_json(
            {
                "adapter_id": self.adapter_id,
                "component_id": self.component_id,
                "manifest_sha256": self.manifest_sha256,
                "source": "omnibase-built-in",
                "version": self.version,
            }
        )


SEEDED_COMPONENT_VERSIONS = (
    SeededComponentVersion(
        component_id="builtin.workspace-canvas",
        version="1.0.0",
        family="declarative_ui",
        display_name="Workspace Canvas",
        adapter_id="builtin-ui.v1",
        entrypoint_kind="host_view_v1",
        operations=("ui.render",),
        requires_network=False,
        slots=("editor.component", "sidebar.component"),
    ),
    SeededComponentVersion(
        component_id="builtin.workspace-canvas",
        version="1.1.0",
        family="declarative_ui",
        display_name="Workspace Canvas",
        adapter_id="builtin-ui.v1",
        entrypoint_kind="host_view_v1",
        operations=("ui.render",),
        requires_network=False,
        slots=("editor.component", "sidebar.component", "status.component"),
        state_schema_version=2,
        configuration_schema_version=2,
    ),
    SeededComponentVersion(
        component_id="builtin.instruction-skill",
        version="1.0.0",
        family="instruction_skill",
        display_name="Instruction Skill",
        adapter_id="instruction-skill.v1",
        entrypoint_kind="instruction_v1",
        operations=("skill.resolve",),
        requires_network=False,
        slots=("settings.component",),
        max_bytes_in=32_768,
        max_bytes_out=32_768,
        max_concurrency=1,
    ),
    SeededComponentVersion(
        component_id="builtin.instruction-skill",
        version="1.1.0",
        family="instruction_skill",
        display_name="Instruction Skill",
        adapter_id="instruction-skill.v1",
        entrypoint_kind="instruction_v1",
        operations=("skill.resolve",),
        requires_network=False,
        slots=("settings.component",),
        max_bytes_in=32_768,
        max_bytes_out=32_768,
        max_concurrency=1,
        configuration_schema_version=2,
    ),
    SeededComponentVersion(
        component_id="builtin.readonly-mcp",
        version="1.0.0",
        family="mcp_connector",
        display_name="Read-only MCP Connector",
        adapter_id="readonly-mcp.v1",
        entrypoint_kind="mcp_closed_v1",
        operations=("mcp.call",),
        requires_network=True,
        slots=("settings.component",),
        max_calls=32,
        max_concurrency=1,
    ),
    SeededComponentVersion(
        component_id="builtin.readonly-mcp",
        version="1.1.0",
        family="mcp_connector",
        display_name="Read-only MCP Connector",
        adapter_id="readonly-mcp.v1",
        entrypoint_kind="mcp_closed_v1",
        operations=("mcp.call",),
        requires_network=True,
        slots=("settings.component",),
        max_calls=48,
        max_concurrency=1,
        configuration_schema_version=2,
    ),
    SeededComponentVersion(
        component_id="builtin.sandbox-workload",
        version="1.0.0",
        family="sandbox_workload",
        display_name="Sandbox Workload",
        adapter_id="p34-sandbox.v1",
        entrypoint_kind="sandbox_workflow_v1",
        operations=("sandbox.run",),
        requires_network=False,
        slots=("settings.component",),
        max_calls=16,
        max_concurrency=1,
    ),
    SeededComponentVersion(
        component_id="builtin.sandbox-workload",
        version="1.1.0",
        family="sandbox_workload",
        display_name="Sandbox Workload",
        adapter_id="p34-sandbox.v1",
        entrypoint_kind="sandbox_workflow_v1",
        operations=("sandbox.run",),
        requires_network=False,
        slots=("settings.component",),
        max_calls=24,
        max_concurrency=1,
        state_schema_version=2,
    ),
    SeededComponentVersion(
        component_id="knowledge.ebook",
        version="1.0.0",
        family="trusted_local_adapter",
        display_name="Trusted Local Adapter",
        adapter_id="trusted-local-app.v1",
        entrypoint_kind="native_catalog_v1",
        operations=("local_adapter.open",),
        requires_network=False,
        slots=("editor.component", "settings.component"),
        max_calls=32,
        max_bytes_out=32 * _HOST_ADAPTER_OUTPUT_BYTES_PER_CALL,
        max_concurrency=1,
    ),
    SeededComponentVersion(
        component_id="knowledge.ebook",
        version="1.1.0",
        family="trusted_local_adapter",
        display_name="Knowledge Ebook",
        adapter_id="trusted-local-app.v1",
        entrypoint_kind="native_catalog_v1",
        operations=("local_adapter.open",),
        requires_network=False,
        slots=("editor.component", "settings.component"),
        max_calls=48,
        max_bytes_out=48 * _HOST_ADAPTER_OUTPUT_BYTES_PER_CALL,
        max_concurrency=1,
        state_schema_version=2,
    ),
)


SEEDED_BY_ID_VERSION = {
    (item.component_id, item.version): item for item in SEEDED_COMPONENT_VERSIONS
}


__all__ = [
    "SEEDED_BY_ID_VERSION",
    "SEEDED_COMPONENT_VERSIONS",
    "SeededComponentVersion",
    "canonical_json",
    "digest_json",
    "validate_closed_manifest",
]
