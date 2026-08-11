"""Compatibility exports for the canonical Task Runtime canary contracts.

The authority-neutral contract lives under :mod:`host_brain.contracts` so the
persistence layer never depends back on the application/runtime layer.  Keep
this module as an identity-preserving import path for existing callers.
"""

from host_brain.contracts.task_runtime_feature_canary import *
from host_brain.contracts.task_runtime_feature_canary import __all__
