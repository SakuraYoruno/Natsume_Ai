from __future__ import annotations

from typing import Any, Mapping

from .schema import Turn


def turns_to_messages(turns: list[Turn | Mapping[str, Any]]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for turn in turns:
        # 历史记录来自持久层，进入模型前再次拒绝损坏或空内容；渲染层不尝试修复
        # 它们，避免把不可靠数据重新包装成看似可信的对话事实。
        corrupted = bool(turn.corrupted) if isinstance(turn, Turn) else bool(turn.get("corrupted"))
        if corrupted:
            continue
        content = str(turn.content if isinstance(turn, Turn) else turn.get("content") or "").strip()
        if not content:
            continue
        raw_role = turn.role if isinstance(turn, Turn) else turn.get("role")
        # 对外消息协议只接受 user/assistant。未知角色一律降为 user，避免存储层
        # 的任意字符串获得 system 或 assistant 的提示词权限。
        role = "assistant" if str(raw_role or "").strip().lower() == "assistant" else "user"
        messages.append({"role": role, "content": content})
    return messages


def build_chat_messages(
    *,
    system_prompt: str,
    history_turns: list[Turn | Mapping[str, Any]],
    user_message: str,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    base_system = str(system_prompt or "").strip()
    if base_system:
        messages.append({"role": "system", "content": base_system})
    # 系统约束、已清洗历史和本轮输入按固定顺序拼接；本轮始终置尾，确保模型
    # 把它视为需要回应的当前请求而不是可被历史覆盖的上下文片段。
    messages.extend(turns_to_messages(history_turns))
    messages.append({"role": "user", "content": str(user_message or "")})
    return messages
