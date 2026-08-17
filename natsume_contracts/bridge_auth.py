"""Host、Edge 与 Bridge 共用的认证载荷契约，冻结字段顺序与 HTTP 头名称。"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping


BRIDGE_AUTH_VERSION = "natsume.bridge-auth.v1"
HTTP_AUTH_HEADERS = {
    "version": "X-Natsume-Bridge-Version",
    "key_id": "X-Natsume-Bridge-Key-Id",
    "timestamp_ms": "X-Natsume-Bridge-Timestamp",
    "nonce": "X-Natsume-Bridge-Nonce",
    "principal": "X-Natsume-Bridge-Principal",
    "scope": "X-Natsume-Bridge-Scope",
    "signature": "X-Natsume-Bridge-Signature",
}


def body_sha256(raw_body: bytes) -> str:
    """摘要 HTTP 线上的原始字节，而非解析后的 JSON，防止同语义不同编码绕过签名。"""
    if not isinstance(raw_body, bytes):
        raise TypeError("raw_body must be bytes")
    return hashlib.sha256(raw_body).hexdigest()


def _canonical_text(value: Any, *, lower: bool = False, upper: bool = False) -> str:
    """规范化单个签名字段并拒绝换行，避免字段边界在拼接载荷中变得歧义。"""
    text = str(value).strip()
    if "\n" in text or "\r" in text:
        raise ValueError("bridge authentication fields cannot contain newlines")
    if lower:
        return text.lower()
    if upper:
        return text.upper()
    return text


def canonical_hmac_payload(
    *,
    version: str,
    key_id: str,
    transport: str,
    method: str,
    path: str,
    timestamp_ms: int | str,
    nonce: str,
    principal: str,
    scope: str,
    body_digest: str,
) -> bytes:
    """按固定十字段顺序生成待签名载荷。

    时间戳和 nonce 被签入只是让验证端能够实施时效与重放保护；本函数不保存 nonce、
    不检查时钟，也不作权限决定，因此不能单独视为认证通过。
    """

    return "\n".join(
        (
            _canonical_text(version),
            _canonical_text(key_id),
            _canonical_text(transport, lower=True),
            _canonical_text(method, upper=True),
            _canonical_text(path),
            _canonical_text(timestamp_ms),
            _canonical_text(nonce),
            _canonical_text(principal),
            _canonical_text(scope),
            _canonical_text(body_digest, lower=True),
        )
    ).encode("utf-8")


def build_hmac_digest(secret: bytes, **canonical_fields: Any) -> str:
    """计算 HMAC-SHA256 摘要；只返回摘要，绝不把密钥或规范化中间值带出边界。"""
    key = bytes(secret)
    if not key:
        raise ValueError("bridge HMAC secret is required")
    payload = canonical_hmac_payload(**canonical_fields)
    block_size = 64
    normalized = hashlib.sha256(key).digest() if len(key) > block_size else key
    normalized = normalized.ljust(block_size, b"\0")
    outer = bytes(byte ^ 0x5C for byte in normalized)
    inner = bytes(byte ^ 0x36 for byte in normalized)
    return hashlib.sha256(outer + hashlib.sha256(inner + payload).digest()).hexdigest()


def build_hmac_signature(secret: bytes, **canonical_fields: Any) -> str:
    """为传输协议加上算法前缀，使接收端不会把裸摘要误当成其他签名格式。"""
    return "sha256=" + build_hmac_digest(secret, **canonical_fields)


def build_http_auth_headers(
    *,
    secret: bytes,
    key_id: str,
    method: str,
    path: str,
    timestamp_ms: int,
    nonce: str,
    principal: str,
    scope: str,
    raw_body: bytes,
    header_names: Mapping[str, str] = HTTP_AUTH_HEADERS,
) -> dict[str, str]:
    """构造 HTTP 请求的认证头。

    这一步只确保发送端使用一致的原始 body、方法与路径参与签名；接收端仍应验证
    版本、key_id、签名、时间窗、nonce 与 scope，且不得因头已经存在而跳过授权。
    """
    signature = build_hmac_signature(
        secret,
        version=BRIDGE_AUTH_VERSION,
        key_id=key_id,
        transport="http",
        method=method,
        path=path,
        timestamp_ms=timestamp_ms,
        nonce=nonce,
        principal=principal,
        scope=scope,
        body_digest=body_sha256(raw_body),
    )
    return {
        str(header_names["version"]): BRIDGE_AUTH_VERSION,
        str(header_names["key_id"]): str(key_id).strip(),
        str(header_names["timestamp_ms"]): str(int(timestamp_ms)),
        str(header_names["nonce"]): str(nonce).strip(),
        str(header_names["principal"]): str(principal).strip(),
        str(header_names["scope"]): str(scope).strip(),
        str(header_names["signature"]): signature,
    }


__all__ = [
    "BRIDGE_AUTH_VERSION",
    "HTTP_AUTH_HEADERS",
    "body_sha256",
    "build_hmac_digest",
    "build_hmac_signature",
    "build_http_auth_headers",
    "canonical_hmac_payload",
]
