"""Deprecated compatibility facade for Host Gateway ingress helpers.

The canonical implementation lives in ``host_brain.input_runtime.ingress_policy``.
Keeping a forwarding module preserves existing external imports while ensuring
Host Input, tests and compatibility callers cannot drift into separate secure
ingress semantics.
"""

from host_brain.input_runtime.ingress_policy import (
    HostGatewayIngressOutcome,
    build_gateway_skill_manifest_lines,
    complete_host_gateway_ingress,
    evaluate_host_gateway_ingress,
    extract_first_json_object,
    looks_like_execution_intent,
    looks_like_progress_query,
    normalize_route_confidence,
)

__all__ = [
    "HostGatewayIngressOutcome",
    "build_gateway_skill_manifest_lines",
    "complete_host_gateway_ingress",
    "evaluate_host_gateway_ingress",
    "extract_first_json_object",
    "looks_like_execution_intent",
    "looks_like_progress_query",
    "normalize_route_confidence",
]
