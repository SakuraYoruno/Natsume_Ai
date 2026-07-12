"""Compatibility import for the persistence-owned Secure Ingress adapter."""

from host_brain.persistence.secure_ingress_repository import (
    SECURE_INGRESS_MIGRATION_ASSET_KEY,
    SECURE_INGRESS_MIGRATION_SHA256,
    SQLiteSecureIngressStore,
    SecureIngressStoreError,
)

__all__ = [
    "SECURE_INGRESS_MIGRATION_ASSET_KEY",
    "SECURE_INGRESS_MIGRATION_SHA256",
    "SQLiteSecureIngressStore",
    "SecureIngressStoreError",
]
