"""Edge 持有的 MateEngine 物理传输适配器。

命令根据最小作用域签名后才发送到本机桥：健康查询为只读作用域，控制命令
需要管理作用域。此模块只报告桥的本地响应，不能声称物理设备最终已完成动作。
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import time
from typing import Any, Mapping
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from natsume_contracts.bridge_auth import build_http_auth_headers


def execute_mateengine_effect(*, action: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    # action 映射决定所需作用域；不能因为 payload 自称只读就降低命令端点的
    # 授权级别。
    normalized = str(action or "command").strip().lower()
    body = dict(payload or {})
    if normalized == "health":
        return _http_json(method="GET", path="/health", scope="bridge:mateengine:read")
    if normalized == "signal":
        return _send_udp(dict(body.get("signal") or body))
    command = body.get("command")
    command_payload = dict(command) if isinstance(command, Mapping) else body
    return _http_json(
        method="POST",
        path="/command",
        scope="bridge:mateengine:admin",
        payload=command_payload,
    )


def _http_json(
    *, method: str, path: str, scope: str, payload: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    # 认证覆盖精确 HTTP 方法、路径、时间戳、随机 nonce 与原始正文；桥端据此
    # 拒绝改包和重放。默认地址仅为本机回环，远程暴露需由部署层显式配置。
    base_url = str(
        os.getenv("EDGE_MATEENGINE_BRIDGE_BASE_URL")
        or os.getenv("NATSUME_MATEENGINE_BRIDGE_BASE_URL")
        or "http://127.0.0.1:18770"
    ).rstrip("/")
    url = f"{base_url}{path}"
    raw_body = (
        json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if payload is not None else b""
    )
    headers = {"Content-Type": "application/json; charset=utf-8"}
    key_id = str(os.getenv("NATSUME_MATEENGINE_BRIDGE_HMAC_KEY_ID") or "").strip()
    secret = str(os.getenv("NATSUME_MATEENGINE_BRIDGE_HMAC_SECRET") or "")
    principal = str(
        os.getenv("EDGE_MATEENGINE_BRIDGE_CLIENT_PRINCIPAL")
        or "edge.mateengine.authority"
    ).strip()
    if not key_id or len(secret.encode("utf-8")) < 32:
        # 缺少独立桥凭据时 fail closed，绝不退回通用 RPC 密钥或无签名调用。
        return {
            "success": False,
            "status": "REJECTED",
            "reason_code": "EDGE_MATEENGINE_BRIDGE_CREDENTIAL_REQUIRED",
            "detail": "Edge MateEngine bridge credentials are required",
            "target": url,
        }
    headers.update(
        build_http_auth_headers(
            secret=secret.encode("utf-8"), key_id=key_id, method=method,
            path=urlsplit(url).path or "/", timestamp_ms=int(time.time() * 1000),
            nonce=secrets.token_urlsafe(24), principal=principal, scope=scope,
            raw_body=raw_body,
        )
    )
    request = urllib.request.Request(
        url=url,
        data=(raw_body if payload is not None else None),
        headers=headers,
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(request, timeout=_timeout_s()) as response:
            raw = response.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw.strip() else {}
            success = 200 <= int(response.status) < 300 and bool(data.get("success", True))
            return {
                "success": success,
                "status": "COMPLETED" if success else "REJECTED",
                "reason_code": None if success else str(data.get("reason_code") or "MATEENGINE_REJECTED"),
                "detail": str(data.get("detail") or "MateEngine effect completed"),
                "target": url,
                "response": dict(data) if isinstance(data, dict) else {"data": data},
            }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {"success": False, "status": "REJECTED", "reason_code": "MATEENGINE_HTTP_ERROR", "detail": f"HTTP {exc.code}: {detail}", "target": url}
    except (OSError, json.JSONDecodeError) as exc:
        return {"success": False, "status": "DEGRADED", "reason_code": "MATEENGINE_UNAVAILABLE", "detail": str(exc), "target": url}


def _send_udp(payload: Mapping[str, Any]) -> dict[str, Any]:
    # UDP 信号是专用低延迟通道，成功仅表示本机 socket 已交给内核；没有远端
    # 收据时不能把它升级为设备效果完成。
    host = str(os.getenv("EDGE_MATEENGINE_SIGNAL_HOST") or "127.0.0.1")
    try:
        port = int(os.getenv("EDGE_MATEENGINE_SIGNAL_PORT") or "32145")
        data = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sent = sock.sendto(data, (host, port))
        return {"success": True, "status": "COMPLETED", "reason_code": None, "detail": "MateEngine signal sent", "host": host, "port": port, "bytes_sent": sent}
    except (OSError, ValueError) as exc:
        return {"success": False, "status": "DEGRADED", "reason_code": "MATEENGINE_SIGNAL_UNAVAILABLE", "detail": str(exc), "host": host}


def _timeout_s() -> float:
    try:
        return max(0.2, min(float(os.getenv("EDGE_MATEENGINE_BRIDGE_TIMEOUT_S") or "2.5"), 15.0))
    except ValueError:
        return 2.5


__all__ = ["execute_mateengine_effect"]
