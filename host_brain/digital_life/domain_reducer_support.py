"""Digital Life 领域 reducer 共享的纯有界值工具。

这些函数只做确定性的复制、归一化和范围约束，不读取环境或修改输入对象；共同
约束可避免不同领域对同一稀疏/异常 payload 得出不一致的重放结果。
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any


def _mapping(value: Any) -> dict[str, Any]:
    """安全复制 mapping，非对象统一视为缺失，避免调用方意外保留外部可变引用。"""
    return dict(value) if isinstance(value, Mapping) else {}


def _merge_patch(base: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """以键排序递归合并 patch，``None`` 表示删除，确保合并顺序可重放。"""
    result = deepcopy(dict(base))
    for key in sorted(str(item) for item in patch):
        incoming = patch.get(key)
        if incoming is None:
            result.pop(key, None)
        elif isinstance(incoming, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _merge_patch(dict(result[key]), incoming)
        elif isinstance(incoming, Mapping):
            result[key] = _merge_patch({}, incoming)
        else:
            result[key] = deepcopy(incoming)
    return result


def _clamp(value: Any, default: float = 0.0) -> float:
    """将无符号比例限制为 [0, 1]，无效值退回调用者指定默认值。"""
    return max(0.0, min(1.0, _float(value, default)))


def _float(value: Any, default: float = 0.0) -> float:
    """宽容读取浮点；格式异常不抛入 reducer，而是使用确定性默认值。"""
    try:
        return float(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return float(default)


def _integer(value: Any, default: int = 0) -> int:
    """宽容读取整数；格式异常使用确定性默认值。"""
    try:
        return int(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return int(default)


def _required_text(value: Any, name: str, *, limit: int) -> str:
    """校验命令所需的有界文本字段；禁止空标识进入状态或事件身份。"""
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    if len(normalized) > limit:
        raise ValueError(f"{name} exceeds {limit} characters")
    return normalized


def _approach(current: float, target: float, weight: float) -> float:
    """按有界权重平滑靠近目标并限制结果，供无符号/一般数值投影使用。"""
    ratio = _clamp(weight)
    return round(max(-1.0, min(1.0, current + (target - current) * ratio)), 4)


def _approach_signed(current: float, target: float, weight: float) -> float:
    """对带正负方向的量执行同样的确定性平滑，保留更高精度。"""
    ratio = _clamp(weight)
    return round(max(-1.0, min(1.0, current + (target - current) * ratio)), 6)


def _first_float(
    values: Mapping[str, Any],
    keys: tuple[str, ...],
    default: float,
) -> float:
    """按优先键选择首个有效比例，兼容旧字段时仍保持固定优先级。"""
    for key in keys:
        if values.get(key) not in (None, ""):
            return _clamp(values.get(key), default)
    return _clamp(default)


__all__ = ["reduce_life_domains"]
