"""Compatibility wrapper for migrated observability.trace_node_builder module."""

from host_brain.observability.trace_node_builder import TraceNodeBuilder, _IO_MAX_BYTES

__all__ = ["TraceNodeBuilder", "_IO_MAX_BYTES"]
