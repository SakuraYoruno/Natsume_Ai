from __future__ import annotations

from host_brain.core.errors import CHROMA_ENDPOINT_UNAVAILABLE, CHROMA_VECTOR_DIM_MISMATCH
from host_brain.retrieval.recall_fusion_utils import _map_route_exception


def test_map_route_exception_detects_chroma_dim_mismatch_message() -> None:
    exc = RuntimeError("Collection expecting embedding with dimension of 384, got 1024")
    assert _map_route_exception(exc) == CHROMA_VECTOR_DIM_MISMATCH


def test_map_route_exception_defaults_to_endpoint_unavailable() -> None:
    exc = RuntimeError("connection refused")
    assert _map_route_exception(exc) == CHROMA_ENDPOINT_UNAVAILABLE
