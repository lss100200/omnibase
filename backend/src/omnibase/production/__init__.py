"""P34.7 production admission contracts.

The package deliberately contains no service launcher.  Production components
remain unavailable until :mod:`omnibase.production.composition` admits a clean
checkout with every required external proof.
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

__all__ = [
    "AdmissionReport",
    "AdmissionState",
    "ConfigurationError",
    "GitSourceProvenance",
    "JointGateReport",
    "ProductionCompositionConfig",
    "ProductionCompositionGate",
    "TrustPolicy",
    "build_git_source_provenance",
    "load_production_composition_config",
    "load_trust_policy",
    "validate_joint_evidence",
    "validate_joint_evidence_contract",
    "verify_joint_evidence",
]
