"""Compatibility exports for the public Edge service-catalog contract."""

from __future__ import annotations

from natsume_contracts.edge.service_catalog_v1 import (
    EDGE_REQUIRED_SERVICES,
    EdgeServiceDescriptor,
    build_edge_transport_probe_statuses,
    edge_required_services,
)

# 历史名称只为兼容 Edge 本地调用方保留；服务目录的定义与权威来源在中立、版本化的
# 契约包中，避免各端分别维护“必需服务”清单而产生不一致。
EdgeServiceSpec = EdgeServiceDescriptor


__all__ = [
    "EDGE_REQUIRED_SERVICES",
    "EdgeServiceSpec",
    "build_edge_transport_probe_statuses",
    "edge_required_services",
]
