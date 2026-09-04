"""Surface runtime 的无副作用输入归一化工具。

这些小函数把不可信的兼容输入收束为新的本地值，供多个 Surface 子链共享；
它们刻意不包含业务判断，以免隐式改变回复计划或生命状态的授权顺序。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from host_brain.core.runtime_helpers import _copy_mapping as _safe_mapping, _first_nonempty


def _set_agent_input_mapping(agent_input: Any, name: str, value: Mapping[str, Any]) -> None:
    # 兼容输入对象未必可写；赋值失败在这里降级，调用方仍可使用本地计划继续，
    # 不能因可选 trace/适配字段而中断一轮 Surface 回复。
    try:
        setattr(agent_input, name, dict(value))
    except Exception:
        return


def _clean_list(values: Sequence[Any] | None) -> list[str]:
    """规范化、去空和稳定去重，保留首次出现的事实优先级。"""
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in list(values or []):
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def _coerce_sequence(value: Any) -> list[Any]:
    # 文本是单个事实而不是字符序列；其它 Sequence 才展开，避免提示约束被拆成
    # 单字并在后续清理时悄然丢失。
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)):
        text = str(value or "").strip()
        return [text] if text else []
    if isinstance(value, Sequence):
        return list(value)
    return [value]




def _coerce_int(value: Any, *, default: int) -> int:
    """兼容数值解析失败时显式使用调用方提供的安全默认值。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


__all__ = [
    "_clean_list",
    "_coerce_int",
    "_coerce_sequence",
    "_first_nonempty",
    "_safe_mapping",
    "_set_agent_input_mapping",
]
