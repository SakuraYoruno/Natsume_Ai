"""聊天入站的免疫风险检查与非阻塞审计适配器。"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

from host_brain.app.schemas import ChatRequest


def evaluate_immune_gate_for_chat(
    request_body: ChatRequest,
    *,
    immune_agent_provider: Callable[[], Any],
) -> dict[str, Any]:
    """经 Host 注入的 ImmuneAgent 评估聊天文本。

    仅传递文本及会话/人格关联元数据；适配器不解释决策、更不自行允许或拒绝请求，
    保持风险判断权在组合后的免疫 Agent。
    """

    immune_agent = immune_agent_provider()
    return immune_agent.inspect_text(
        text=str(request_body.message or ""),
        source="http.v1.chat",
        metadata={
            "session_id": str(request_body.session_id or "").strip() or None,
            "person_id": str(request_body.person_id or "").strip() or None,
            "input_source": str(request_body.input_source or "").strip() or None,
        },
    )


def log_chat_immune_gate_event(
    *,
    request_id: str,
    request_body: ChatRequest,
    immune_result: Mapping[str, Any],
    get_ledger: Callable[[], Any],
    logger: logging.Logger,
) -> None:
    """尽力记录免疫判断的脱敏审计事件；账本不可用不能改变已作出的门控结果。"""
    try:
        ledger = get_ledger()
    except Exception:
        return
    if ledger is None:
        return
    decision = str(immune_result.get("decision") or "ALLOW").strip().upper()
    status = "OK"
    if decision == "REPORT":
        status = "DEGRADED"
    elif decision == "BLOCK":
        status = "REJECTED"
    try:
        ledger.log_voice_event(
            {
                "request_id": request_id,
                "session_id": str(request_body.session_id or "").strip() or None,
                "event_type": "IMMUNE_CHAT_GATE",
                "direction": "INGRESS",
                "status": status,
                "reason_code": "IMMUNE_BLOCKED_HIGH_RISK" if decision == "BLOCK" else None,
                "stage": "chat.immune_gate",
                "detail": f"decision={decision} risk={immune_result.get('risk_level')} score={immune_result.get('score')}",
                "payload": {
                    "decision": decision,
                    "risk_level": immune_result.get("risk_level"),
                    "score": immune_result.get("score"),
                    "reason_codes": list(immune_result.get("reason_codes") or []),
                    "matched_rules": list(immune_result.get("matched_rules") or [])[:6],
                    "input_source": str(request_body.input_source or "").strip() or None,
                    "person_id": str(request_body.person_id or "").strip() or None,
                },
                "ts_ms": int(time.time() * 1000),
            }
        )
    except Exception as exc:
        logger.debug("chat immune gate audit skipped: %s", exc)


def schedule_chat_immune_gate_event(
    *,
    request_id: str,
    request_body: ChatRequest,
    immune_result: Mapping[str, Any],
    get_ledger: Callable[[], Any],
    logger: logging.Logger,
) -> None:
    """异步安排审计写入，并复制请求/结果以防调用方在后台任务前后修改它们。"""
    try:
        asyncio.create_task(
            asyncio.to_thread(
                log_chat_immune_gate_event,
                request_id=request_id,
                request_body=request_body.model_copy(deep=True),
                immune_result=dict(immune_result or {}),
                get_ledger=get_ledger,
                logger=logger,
            )
        )
    except Exception as exc:
        logger.debug("chat immune gate audit schedule skipped: %s", exc)


__all__ = [
    "evaluate_immune_gate_for_chat",
    "log_chat_immune_gate_event",
    "schedule_chat_immune_gate_event",
]
