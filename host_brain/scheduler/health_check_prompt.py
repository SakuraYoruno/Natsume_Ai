"""AgentHealthCheck — Heartbeat Prompt 模板与响应解析（TASK-0200）。

心跳检测逻辑：
  - 每 30 分钟向 LLM 节点发送 Heartbeat Prompt
  - 回复含 HEARTBEAT_OK（大小写不敏感）→ 静默丢弃
  - 否则 → 推送 HEARTBEAT_ALERT 审计事件
"""

from __future__ import annotations

from typing import Literal

# ── 默认 Heartbeat Prompt 模板 ────────────────────────────────────────────────

DEFAULT_HEARTBEAT_PROMPT: str = (
    "这是一次系统心跳检测。请评估你当前的运行状态是否正常。"
    "如果一切正常，请在回复中包含 HEARTBEAT_OK。"
    "如果你感知到任何异常（如记忆混乱、指令冲突、情绪失调等），"
    "请描述具体情况，不要包含 HEARTBEAT_OK。"
)

# 回复最大字符数（超出部分截断后写入审计）
MAX_RESPONSE_CHARS: int = 300

# 审计事件类型常量
HEARTBEAT_EVENT_TYPE_OK: str = "HEARTBEAT_OK"
HEARTBEAT_EVENT_TYPE_ALERT: str = "HEARTBEAT_ALERT"

# LLM 不可用时的占位回复
LLM_UNAVAILABLE_RESPONSE: str = "LLM_UNAVAILABLE"


# ── 响应解析 ──────────────────────────────────────────────────────────────────

def parse_heartbeat_response(text: str) -> Literal["OK", "ALERT"]:
    """解析 LLM 心跳回复，判断 Agent 精神状态。

    Args:
        text: LLM 回复的原始文本（任意长度）。

    Returns:
        "OK"    — 回复中包含 HEARTBEAT_OK（大小写不敏感），状态正常，静默丢弃。
        "ALERT" — 回复中不包含 HEARTBEAT_OK，或为空字符串，需推送告警。
    """
    if not text or not str(text).strip():
        return "ALERT"
    if "heartbeat_ok" in str(text).lower():
        return "OK"
    return "ALERT"


def truncate_response(text: str, max_chars: int = MAX_RESPONSE_CHARS) -> str:
    """截断回复文本至 max_chars 字符，防止审计字段过长。"""
    text = str(text or "")
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"
