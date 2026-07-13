"""Runtime-independent operator query contracts."""

from .v1 import (
    OPERATOR_PROJECTION_SCHEMA_VERSION,
    ProjectionDegradedState,
    ProjectionDocument,
)

__all__ = [
    "OPERATOR_PROJECTION_SCHEMA_VERSION",
    "ProjectionDegradedState",
    "ProjectionDocument",
]
