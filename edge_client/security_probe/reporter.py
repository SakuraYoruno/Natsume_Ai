from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


def _join_url(base_url: str, path: str) -> str:
    return f"{str(base_url).rstrip('/')}/{path.lstrip('/')}"


def post_heartbeat(
    *,
    host_url: str,
    asset: dict[str, Any],
    source: str = "edge_security_probe",
    timeout_s: float = 5.0,
) -> dict[str, Any]:
    # 上报只传递已由 collector 生成的快照；HTTP 失败返回结构化结果给调用循环，
    # 不在这里重试，以免网络退化时产生无界阻塞或隐藏连续失败。
    body = json.dumps(
        {
            "source": source,
            "asset": asset,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        _join_url(host_url, "/internal/security/probe/heartbeat"),
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            payload = response.read().decode("utf-8", errors="replace")
            return {
                "ok": 200 <= int(response.status) < 300,
                "status_code": int(response.status),
                "response": json.loads(payload) if payload else {},
            }
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            parsed = {"raw": payload[:500]}
        return {"ok": False, "status_code": int(exc.code), "response": parsed}
    except Exception as exc:
        return {"ok": False, "status_code": 0, "error": str(exc)}
