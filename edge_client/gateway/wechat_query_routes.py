"""WeChat query routes — Phase 2.

Provides a single HTTP endpoint for Host Brain (or any caller) to execute
query-type commands against the wxautox4 Bridge without going through the
standard inbound-message flow:

    POST /v1/gateway/wechat/query

Supported actions (see ALLOWED_WECHAT_COMMAND_ACTIONS):
  Phase 1: get_my_info, get_friends, get_groups, get_recent_chats,
           get_history_messages, listen_add, listen_remove, listen_stop,
           chat_with
  Phase 2: get_sub_window, get_all_sub_windows, get_chat_info, get_dialog,
           send_url_card, get_all_messages, get_new_friends, get_friend_details
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("Natsume.edge.gateway.wechat_query")

router = APIRouter(prefix="/v1/gateway/wechat", tags=["wechat-query"])


class WeChatQueryRequest(BaseModel):
    """Request body for POST /v1/gateway/wechat/query."""

    action: str = Field(..., description="Bridge action name, e.g. get_friends")
    target: str = Field("", description="Target chat / wxid for actions that require one")
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra params forwarded as extra_fields (e.g. savepath, url, title)",
    )
    timeout_ms: int = Field(24000, description="Result wait timeout in milliseconds")


@router.post("/query")
async def wechat_query(req: WeChatQueryRequest) -> JSONResponse:
    """Execute a query-type command via the wxautox4 Bridge and return the result.

    The command is enqueued in the JSONL command queue and this endpoint
    waits synchronously for the result (up to ``timeout_ms``).

    Returns::

        {"success": true, "data": ..., "count": ...}   on success
        {"success": false, "reason_code": ..., "detail": ...}  on failure
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
    """Recursively convert to JSON-serializable types."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_to_json_safe(item) for item in obj]
    if isinstance(obj, dict):
        return {str(k): _to_json_safe(v) for k, v in obj.items()}
    return str(obj)
