"""Compatibility facade for the persistence-owned Gateway transport adapter."""

from host_brain.persistence.gateway_transport_repository import (
    GATEWAY_TRANSPORT_MAX_INBOX_BODY_BYTES,
    GATEWAY_TRANSPORT_MAX_NORMALIZED_PAYLOAD_BYTES,
    GATEWAY_TRANSPORT_MAX_OUTBOX_BODY_BYTES,
    GATEWAY_TRANSPORT_MAX_SAFE_HEADER_COUNT,
    GATEWAY_TRANSPORT_MAX_SAFE_HEADERS_BYTES,
    GATEWAY_TRANSPORT_MAX_SAFE_HEADER_VALUE_BYTES,
    GATEWAY_TRANSPORT_MIGRATION_ASSET_KEY,
    GATEWAY_TRANSPORT_MIGRATION_SHA256,
    GatewayTransportStore,
    GatewayTransportStoreError,
    InboxAcceptance,
    InboxAcceptanceStatus,
    SQLiteGatewayTransportStore,
)

__all__ = [
    "GATEWAY_TRANSPORT_MAX_INBOX_BODY_BYTES",
    "GATEWAY_TRANSPORT_MAX_NORMALIZED_PAYLOAD_BYTES",
    "GATEWAY_TRANSPORT_MAX_OUTBOX_BODY_BYTES",
    "GATEWAY_TRANSPORT_MAX_SAFE_HEADER_COUNT",
    "GATEWAY_TRANSPORT_MAX_SAFE_HEADERS_BYTES",
    "GATEWAY_TRANSPORT_MAX_SAFE_HEADER_VALUE_BYTES",
    "GATEWAY_TRANSPORT_MIGRATION_ASSET_KEY",
    "GATEWAY_TRANSPORT_MIGRATION_SHA256",
    "GatewayTransportStore",
    "GatewayTransportStoreError",
    "InboxAcceptance",
    "InboxAcceptanceStatus",
    "SQLiteGatewayTransportStore",
]
