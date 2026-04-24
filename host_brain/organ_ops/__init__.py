"""OrganOps registry built from tribe agent modules."""

from host_brain.organ_ops.organ_health_profile import OrganHealthProfile
from host_brain.organ_ops.organ_registry import OrganDescriptor, OrganRegistry, build_default_organ_registry
from host_brain.organ_ops.organ_wiring_audit import build_organ_health_profiles, run_organ_wiring_audit

__all__ = [
    "OrganDescriptor",
    "OrganHealthProfile",
    "OrganRegistry",
    "build_default_organ_registry",
    "build_organ_health_profiles",
    "run_organ_wiring_audit",
]
