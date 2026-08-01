"""P34.5A fail-closed Sandbox foundation.

No implementation in this package executes code or contacts a runtime,
container engine, network provider, data store or host control socket.
"""

from omnibase.sandbox.contracts import (
    RejectingSandboxAuthorizer,
    SandboxAction,
    SandboxAuthorizer,
    SandboxCommandSpec,
    SandboxConflict,
    SandboxExecutionDisabled,
    SandboxIsolationPolicy,
    SandboxNetworkMode,
    SandboxNetworkPolicy,
    SandboxOperationRequest,
    SandboxProvider,
    SandboxRejected,
    SandboxRelativePath,
    SandboxResourceLimits,
    SandboxRuntimeHandle,
    SandboxRuntimeSpec,
    SandboxRuntimeState,
    SandboxRuntimeView,
    SandboxSnapshot,
    SandboxUnavailable,
)
from omnibase.sandbox.provider import (
    FakeInMemorySandboxProvider,
    InMemorySandboxAuthorizer,
    UnavailableSandboxProvider,
)

__all__ = [
    "FakeInMemorySandboxProvider",
    "InMemorySandboxAuthorizer",
    "RejectingSandboxAuthorizer",
    "SandboxAction",
    "SandboxAuthorizer",
    "SandboxCommandSpec",
    "SandboxConflict",
    "SandboxExecutionDisabled",
    "SandboxIsolationPolicy",
    "SandboxNetworkMode",
    "SandboxNetworkPolicy",
    "SandboxOperationRequest",
    "SandboxProvider",
    "SandboxRejected",
    "SandboxRelativePath",
    "SandboxResourceLimits",
    "SandboxRuntimeHandle",
    "SandboxRuntimeSpec",
    "SandboxRuntimeState",
    "SandboxRuntimeView",
    "SandboxSnapshot",
    "SandboxUnavailable",
    "UnavailableSandboxProvider",
]
