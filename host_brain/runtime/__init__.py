"""Host runtime package.

DigitalLifeSupervisor was a fixture/soak harness, not the production life
authority.  It is intentionally no longer exported from the runtime surface;
validation tools import it through :mod:`host_brain.validation`.
"""

from .world_resolver import WorldResolver, WorldResolverError

__all__ = ["WorldResolver", "WorldResolverError"]
