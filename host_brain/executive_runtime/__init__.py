"""Retired Executive compatibility namespace.

Production routes are admitted by ``GovernedHostRuntime`` and executed only
from immutable ``TaskGraph`` nodes.  This package deliberately has no import
side-effect composition, router, planner, or runtime-capability override.
``skill_execution`` remains a private low-level worker helper reached only
through ``governed_skill_execution_ports`` after node admission.
"""
