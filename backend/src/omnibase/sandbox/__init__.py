"""P34.5A fail-closed Sandbox foundation and A1 control contracts.

No implementation in this package executes code or contacts a runtime,
container engine, network provider, data store or host control socket.
"""

from omnibase.sandbox.authorization import (
    ComposedSandboxAuthorizer,
    RejectingSandboxCapabilityVerifier,
    RejectingSandboxLeaseVerifier,
    VerifiedSandboxCapability,
    VerifiedSandboxLease,
)
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
from omnibase.sandbox.control import (
    InMemorySandboxControlAuthorizer,
    RejectingSandboxControlAuthorizer,
    SandboxControlAction,
    SandboxControlRequest,
    VerifiedSandboxControlAuthorization,
)
from omnibase.sandbox.operations import (
    InMemorySandboxOperationStore,
    SandboxOperationIntent,
    SandboxOperationState,
)
from omnibase.sandbox.provider import (
    FakeInMemorySandboxProvider,
    InMemorySandboxAuthorizer,
    UnavailableSandboxProvider,
)
from omnibase.sandbox.runner import (
    RunnerExecutionPlan,
    RunnerIsolationProfile,
    RunnerPlatform,
    RunnerTerminationPlan,
    UnavailableSandboxRunner,
)

__all__ = [
    "ComposedSandboxAuthorizer",
    "FakeInMemorySandboxProvider",
    "InMemorySandboxAuthorizer",
    "InMemorySandboxControlAuthorizer",
    "InMemorySandboxOperationStore",
    "RejectingSandboxAuthorizer",
    "RejectingSandboxCapabilityVerifier",
    "RejectingSandboxControlAuthorizer",
    "RejectingSandboxLeaseVerifier",
    "RunnerExecutionPlan",
    "RunnerIsolationProfile",
    "RunnerPlatform",
    "RunnerTerminationPlan",
    "SandboxAction",
    "SandboxAuthorizer",
    "SandboxCommandSpec",
    "SandboxConflict",
    "SandboxControlAction",
    "SandboxControlRequest",
    "SandboxExecutionDisabled",
    "SandboxIsolationPolicy",
    "SandboxNetworkMode",
    "SandboxNetworkPolicy",
    "SandboxOperationIntent",
    "SandboxOperationRequest",
    "SandboxOperationState",
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
    "UnavailableSandboxRunner",
    "VerifiedSandboxCapability",
    "VerifiedSandboxControlAuthorization",
    "VerifiedSandboxLease",
]
