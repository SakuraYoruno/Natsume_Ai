"""Edge RPC 的 HMAC 身份认证与重放保护辅助逻辑。"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Callable

from edge_client.app.schemas import EdgeRpcEnvelope
from natsume_contracts.edge.v1 import (
    EDGE_RPC_AUTH_EXPIRED,
    EDGE_RPC_AUTH_INVALID,
    REPLAY_DETECTED,
    now_ms,
)


S_EDGE_RPC_AUTH = "S_EDGE_RPC_AUTH"


@dataclass(frozen=True)
class EdgeRpcAuthResult:
    success: bool
    stage: str
    reason_code: str | None
    detail: str
    insecure_auth: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "stage": self.stage,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "insecure_auth": self.insecure_auth,
        }


class NonceReplayCache:
    """以 nonce 为键的进程内重放缓存。"""

    def __init__(self, *, window_ms: int) -> None:
        self.window_ms = int(window_ms)
        self._rows: dict[str, int] = {}

    def is_replay(self, *, nonce: str, now_value: int | None = None) -> bool:
        # 每次查询先淘汰窗口外记录；缓存只保护本进程存活期，因此部署重启后的
        # 重放防护仍依赖短时间戳与上游传输/会话边界。
        ts = int(now_value if now_value is not None else now_ms())
        self._purge(now_value=ts)
        return str(nonce) in self._rows

    def store(self, *, nonce: str, now_value: int | None = None) -> None:
        ts = int(now_value if now_value is not None else now_ms())
        self._purge(now_value=ts)
        self._rows[str(nonce)] = ts

    def snapshot(self) -> dict[str, Any]:
        return {
            "window_ms": self.window_ms,
            "size": len(self._rows),
        }

    def _purge(self, *, now_value: int) -> None:
        lower_bound = int(now_value) - int(self.window_ms)
        stale = [nonce for nonce, ts in self._rows.items() if int(ts) < lower_bound]
        for nonce in stale:
            self._rows.pop(nonce, None)


def build_edge_rpc_signature(
    *,
    request_id: str,
    ts_ms: int,
    nonce: str,
    payload: dict[str, Any],
    shared_key: str,
) -> str:
    canonical_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    message = f"{request_id}\n{int(ts_ms)}\n{nonce}\n{canonical_payload}"
    digest = hmac.new(
        str(shared_key).encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    )
    return digest.hexdigest()


def validate_edge_rpc_auth(
    *,
    envelope: EdgeRpcEnvelope,
    shared_key: str | None,
    allow_insecure_auth: bool,
    auth_window_ms: int,
    replay_cache: NonceReplayCache,
    now_fn: Callable[[], int] | None = None,
) -> EdgeRpcAuthResult:
    # 时间窗、nonce 与签名依次验证：签名只证明字节绑定，nonce 才阻止同一合法
    # 请求在窗口内重放。测试/开发的 insecure 模式仍写 nonce，不能变成无限重放。
    current = int(now_fn() if now_fn is not None else now_ms())
    drift = abs(current - int(envelope.ts_ms))
    if drift > int(auth_window_ms):
        return _failure(
            reason_code=EDGE_RPC_AUTH_EXPIRED,
            detail=f"ts_ms drift {drift}ms exceeds window {int(auth_window_ms)}ms",
        )

    if replay_cache.is_replay(nonce=envelope.nonce, now_value=current):
        return _failure(
            reason_code=REPLAY_DETECTED,
            detail="nonce replay detected",
        )

    if allow_insecure_auth:
        replay_cache.store(nonce=envelope.nonce, now_value=current)
        return EdgeRpcAuthResult(
            success=True,
            stage=S_EDGE_RPC_AUTH,
            reason_code=None,
            detail="insecure auth enabled; signature bypassed",
            insecure_auth=True,
        )

    key_text = str(shared_key or "").strip()
    if not key_text:
        return _failure(
            reason_code=EDGE_RPC_AUTH_INVALID,
            detail="shared key missing while insecure auth disabled",
        )

    expected = build_edge_rpc_signature(
        request_id=envelope.request_id,
        ts_ms=envelope.ts_ms,
        nonce=envelope.nonce,
        payload=envelope.payload,
        shared_key=key_text,
    )
    if not hmac.compare_digest(expected, envelope.signature):
        return _failure(
            reason_code=EDGE_RPC_AUTH_INVALID,
            detail="signature mismatch",
        )

    replay_cache.store(nonce=envelope.nonce, now_value=current)
    return EdgeRpcAuthResult(
        success=True,
        stage=S_EDGE_RPC_AUTH,
        reason_code=None,
        detail="edge rpc auth passed",
        insecure_auth=False,
    )


def _failure(*, reason_code: str, detail: str) -> EdgeRpcAuthResult:
    return EdgeRpcAuthResult(
        success=False,
        stage=S_EDGE_RPC_AUTH,
        reason_code=reason_code,
        detail=detail,
        insecure_auth=False,
    )
