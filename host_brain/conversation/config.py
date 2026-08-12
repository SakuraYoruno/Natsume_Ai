from __future__ import annotations

import os


def _parse_int_env(
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = str(os.environ.get(name) or "").strip()
    # 环境变量属于部署输入而非业务事实；解析失败或越界时回退/截断，避免一次
    # 配置错误让历史窗口无限膨胀，或把摘要预算缩到不能表达连续性。
    try:
        value = int(raw) if raw else int(default)
    except ValueError:
        value = int(default)
    return max(int(minimum), min(int(maximum), value))


def conversation_max_turns() -> int:
    return _parse_int_env("HOST_CONVERSATION_MAX_TURNS", default=10, minimum=0, maximum=64)


def conversation_summary_threshold() -> int:
    return _parse_int_env("HOST_CONVERSATION_SUMMARY_THRESHOLD", default=20, minimum=0, maximum=200)


def conversation_summary_max_chars() -> int:
    return _parse_int_env("HOST_CONVERSATION_SUMMARY_MAX_CHARS", default=220, minimum=48, maximum=1000)
