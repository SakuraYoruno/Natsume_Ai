"""Host runtime package.

DigitalLifeSupervisor was a fixture/soak harness, not the production life
authority.  It is intentionally no longer exported from the runtime surface;
validation tools import it through :mod:`host_brain.validation`.
"""

from .world_resolver import WorldResolver, WorldResolverError
from .intent_arbiter import IntentArbiter
from .intent_authority import (
    IntentAuthorityAssembler,
    IntentAuthorityAttestationV1,
    IntentAuthorityError,
    IntentAuthorityPort,
    IntentPolicyIdentityV1,
)
from .scene_resolver import SceneResolver, SceneResolverError
from .runtime_guardian import (
    GuardianEventV1,
    GuardianSnapshotV1,
    RuntimeGuardian,
    RuntimeGuardianError,
)
from .production_composition import (
    RuntimeContextComposition,
    RuntimeContextCompositionError,
    build_runtime_context_composition,
)

__all__ = [
    "GuardianEventV1",
    "GuardianSnapshotV1",
    "IntentArbiter",
    "IntentAuthorityAssembler",
    "IntentAuthorityAttestationV1",
    "IntentAuthorityError",
    "IntentAuthorityPort",
    "IntentPolicyIdentityV1",
    "RuntimeGuardian",
    "RuntimeGuardianError",
    "RuntimeContextComposition",
    "RuntimeContextCompositionError",
    "SceneResolver",
    "SceneResolverError",
    "WorldResolver",
    "WorldResolverError",
    "build_runtime_context_composition",
]
