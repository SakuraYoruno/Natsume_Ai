"""用于对账证明签名的仅验证权威。

该边界仅持有 Ed25519 公钥且不暴露签名 API；持久化层据此确认恢复/对账命令来自受信
权威，同时保持私钥与业务执行路径隔离。
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from typing import Any, Protocol

try:
    from cryptography.exceptions import InvalidSignature as _Ed25519InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey as _Ed25519PublicKey,
    )
except ImportError:  # pragma: no cover - dependency fail-closed coverage
    _Ed25519InvalidSignature = None
    _Ed25519PublicKey = None

from host_brain.persistence.task_store_types import (
    TASK_STORE_INVALID,
    TaskStoreError,
)


TASK_STORE_RECONCILIATION_PROOF_REQUIRED = "TASK_STORE_RECONCILIATION_PROOF_REQUIRED"
TASK_STORE_RECONCILIATION_PROOF_INVALID = "TASK_STORE_RECONCILIATION_PROOF_INVALID"
TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED = (
    "TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED"
)


class ReconciliationProofVerifier(Protocol):
    """仅验证边界，刻意不提供签名能力，避免存储进程扩大为授权主体。"""

    def has_key(self, key_id: str) -> bool: ...

    def verify(self, *, key_id: str, message: bytes, signature: str) -> bool: ...


class Ed25519ReconciliationProofVerifier:
    """仅用公钥验证权威证明；依赖缺失或密钥格式异常均闭合失败。"""

    def __init__(self, public_keys: Mapping[str, bytes]) -> None:
        if _Ed25519PublicKey is None:
            raise TaskStoreError(
                TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED,
                "cryptography with Ed25519 support is required for proof verification",
            )
        keys: dict[str, Any] = {}
        for raw_key_id, raw_key in public_keys.items():
            key_id = str(raw_key_id or "").strip()
            try:
                key_bytes = bytes(raw_key)
            except (TypeError, ValueError) as exc:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "Ed25519 public keys must be byte strings",
                ) from exc
            if not key_id or len(key_bytes) != 32:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "reconciliation public keys require key_id and exactly 32 bytes",
                )
            try:
                keys[key_id] = _Ed25519PublicKey.from_public_bytes(key_bytes)
            except ValueError as exc:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    f"invalid Ed25519 public key for key_id {key_id}",
                ) from exc
        if not keys:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "at least one reconciliation public key is required",
            )
        self._public_keys = keys

    def has_key(self, key_id: str) -> bool:
        return str(key_id or "").strip() in self._public_keys

    def verify(self, *, key_id: str, message: bytes, signature: str) -> bool:
        """验证规范 base64url 签名；未知 key、编码非规范或签名错误均只返回 False。"""
        key = self._public_keys.get(str(key_id or "").strip())
        supplied = str(signature or "").strip()
        if key is None or not supplied.startswith("ed25519:"):
            return False
        encoded = supplied.removeprefix("ed25519:")
        try:
            signature_bytes = base64.b64decode(
                encoded,
                altchars=b"-_",
                validate=True,
            )
        except (binascii.Error, ValueError):
            return False
        if (
            len(signature_bytes) != 64
            or base64.urlsafe_b64encode(signature_bytes).decode("ascii") != encoded
        ):
            return False
        try:
            key.verify(signature_bytes, bytes(message))
        except (ValueError, TypeError):
            return False
        except Exception as exc:
            if _Ed25519InvalidSignature is not None and isinstance(
                exc,
                _Ed25519InvalidSignature,
            ):
                return False
            raise
        return True


__all__ = [
    "Ed25519ReconciliationProofVerifier",
    "ReconciliationProofVerifier",
    "TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED",
    "TASK_STORE_RECONCILIATION_PROOF_INVALID",
    "TASK_STORE_RECONCILIATION_PROOF_REQUIRED",
]
