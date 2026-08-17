"""将 Host 查询请求排入微信 Bridge 的受签名命令队列，并等待桥端收据。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("Natsume.edge.gateway.wechat_query")

router = APIRouter(prefix="/v1/gateway/wechat", tags=["wechat-query"])


class WeChatQueryRequest(BaseModel):
    """路由接受的命令描述；额外字段只随命令传递，不构成调用者授权。"""

    action: str = Field(..., description="Bridge action name, e.g. get_friends")
    target: str = Field("", description="Target chat / wxid for actions that require one")
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra params forwarded as extra_fields (e.g. savepath, url, title)",
    )
    timeout_ms: int = Field(24000, description="Result wait timeout in milliseconds")


@router.post("/query")
async def wechat_query(req: WeChatQueryRequest) -> JSONResponse:
    """排入允许的 Bridge 动作并在有限窗口内返回该命令的收据。

    路由的动作白名单只缩小可排队的命令集合；签名、暴露模式和消费者侧校验仍是
    实际执行的安全边界。等待超时表示尚未见到收据，不会撤销已写入队列的命令，
    上游须以 ``command_id`` 关联后续恢复或查询，而不能推断动作未发生。
    """
    from edge_client.channels.wechat_command_queue import (
        ALLOWED_WECHAT_COMMAND_ACTIONS,
        enqueue_wechat_command,
        resolve_command_file_paths,
        wait_wechat_command_result,
    )

    action = str(req.action or "").strip().lower()
    if not action:
        raise HTTPException(status_code=400, detail="'action' field is required")

    # 名称过滤不能替代授权：它阻断未知动作，而已知动作仍必须由队列签名和桥端
    # 暴露策略共同放行，尤其是其中包含会改变桌面会话的操作。
    if action not in ALLOWED_WECHAT_COMMAND_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown or disallowed action: '{action}'. "
                   f"Allowed: {sorted(ALLOWED_WECHAT_COMMAND_ACTIONS)}",
        )

    queue_file, result_file = resolve_command_file_paths()

    # Separate 'content' from extra_fields (content is a first-class queue field)
    content = str(req.extra.pop("content", "") or "")
    extra_fields = dict(req.extra) if req.extra else None

    row = enqueue_wechat_command(
        action=action,
        target=req.target,
        content=content,
        queue_file=queue_file,
        extra_fields=extra_fields,
    )

    command_id = str(row.get("command_id") or "")
    timeout_ms = max(1000, int(req.timeout_ms))

    result = wait_wechat_command_result(
        command_id=command_id,
        timeout_ms=timeout_ms,
        result_file=result_file,
    )

    if result is None:
        # 此处没有消费或删除队列行；504 仅表达本同步请求未等到结果，避免把迟到的
        # Bridge 收据误报成确定失败。
        logger.warning("wechat_query: timeout waiting for command_id=%s action=%s", command_id, action)
        return JSONResponse(
            status_code=504,
            content={
                "success": False,
                "reason_code": "TIMEOUT",
                "detail": f"No result within {timeout_ms}ms for action={action}",
                "command_id": command_id,
            },
        )

    # Pass through the bridge result as-is, normalising to JSON-safe types
    return JSONResponse(content=_to_json_safe(result))


def _to_json_safe(obj: Any) -> Any:
    """递归投影为 HTTP 可编码数据；未知对象只降为文本，避免响应层泄露运行对象。"""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_to_json_safe(item) for item in obj]
    if isinstance(obj, dict):
        return {str(k): _to_json_safe(v) for k, v in obj.items()}
    return str(obj)
