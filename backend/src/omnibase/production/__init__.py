"""P34.7 production admission contracts.

The package deliberately contains no service launcher. The personal Runtime
module records bounded operator intent and verifies an append-only canary
ledger; it does not start a process or bypass live request authorization.
"""

from omnibase.production.composition import (
    AdmissionReport,
    AdmissionState,
    ConfigurationError,
    GitSourceProvenance,
    ProductionCompositionConfig,
    ProductionCompositionGate,
    build_git_source_provenance,
    load_production_composition_config,
)
from omnibase.production.joint_gate import (
    JointGateReport,
    TrustPolicy,
    load_trust_policy,
    validate_joint_evidence,
    validate_joint_evidence_contract,
    verify_joint_evidence,
)
from omnibase.production.personal_owner_gate import (
    PersonalGateConfigurationError,
    PersonalGateState,
    PersonalOwnerGate,
    PersonalOwnerGateConfig,
    PersonalOwnerGateReport,
    PersonalOwnerGateRequest,
    load_personal_owner_gate_config,
)
from omnibase.production.personal_runtime_activation import (
    PersonalRuntimeActivationPlan,
    PersonalRuntimeCanaryConfig,
    PersonalRuntimeConfigurationError,
    PersonalRuntimeState,
    PersonalRuntimeStatus,
    activate_personal_runtime_canary,
    kill_personal_runtime_canary,
    load_personal_runtime_canary_config,
    personal_runtime_status_binding_valid,
    read_personal_runtime_status,
    rollback_personal_runtime_canary,
)

__all__ = [
    "AdmissionReport",
    "AdmissionState",
    "ConfigurationError",
    "GitSourceProvenance",
    "JointGateReport",
    "PersonalGateConfigurationError",
    "PersonalGateState",
    "PersonalOwnerGate",
    "PersonalOwnerGateConfig",
    "PersonalOwnerGateReport",
    "PersonalOwnerGateRequest",
    "PersonalRuntimeActivationPlan",
    "PersonalRuntimeCanaryConfig",
    "PersonalRuntimeConfigurationError",
    "PersonalRuntimeState",
    "PersonalRuntimeStatus",
    "ProductionCompositionConfig",
    "ProductionCompositionGate",
    "TrustPolicy",
    "activate_personal_runtime_canary",
    "build_git_source_provenance",
    "kill_personal_runtime_canary",
    "load_personal_owner_gate_config",
    "load_personal_runtime_canary_config",
    "load_production_composition_config",
    "load_trust_policy",
    "personal_runtime_status_binding_valid",
    "read_personal_runtime_status",
    "rollback_personal_runtime_canary",
    "validate_joint_evidence",
    "validate_joint_evidence_contract",
    "verify_joint_evidence",
]
