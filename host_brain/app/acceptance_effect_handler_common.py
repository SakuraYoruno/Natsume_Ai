"""生产 Acceptance Effect handler 共享的纯校验与回执辅助函数。

这些函数不访问 authority、网络或存储；它们把跨 handler 重复的输入规范化、
稳定身份和错误脱敏约束收敛到同一处，确保不同副作用在持久化回执中不会因
格式差异、秘密泄漏或非确定性摘要而获得不同的恢复语义。
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re
import secrets
from typing import Any
import urllib.parse


def _json_object(value: Any, field_name: str) -> dict[str, Any]:
    """接收 mapping 或规范 JSON 对象，拒绝数组/标量等结构漂移。"""
    if isinstance(value, Mapping):
        return dict(value)
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field_name} is not canonical JSON") from exc
    if not isinstance(decoded, Mapping):
        raise ValueError(f"{field_name} is not an object")
    return dict(decoded)


def _is_exact_effect_identity(value: Any, *, limit: int) -> bool:
    """仅接受已规范化的不可变 effect identity，拒绝空白与控制字符。"""

    return bool(
        isinstance(value, str)
        and value
        and value == value.strip()
        and len(value) <= limit
        and re.search(r"[\x00-\x1f\x7f]", value) is None
    )


def _opaque_failure(exc: BaseException) -> str:
    """生成可持久化但不泄露错误正文的失败关联身份。

    错误文字可能包含上游响应、用户输入或凭据，低熵文本的哈希也可能被离线
    猜测；因此持久回执只保存随机 incident id，完整异常仅留在瞬时链路中。
    """

    # Do not hash attacker/provider-controlled text: low-entropy errors and
    # secrets could be guessed offline.  Correlation uses a random incident id;
    # the full exception remains only in ephemeral exception chaining/logging.
    return f"{type(exc).__name__}[incident:{secrets.token_hex(12)}]"


def _canonical_id_ref(kind: str, identity: str) -> str:
    """将存储主键编码为可逆、带类型且可跨 handler 传递的 durable 引用。"""

    normalized_kind = str(kind or "").strip().lower()
    normalized_identity = str(identity or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,31}", normalized_kind):
        raise ValueError("canonical reference kind is invalid")
    if not normalized_identity:
        raise ValueError("canonical reference identity is required")
    return (
        f"{normalized_kind}://id/"
        + urllib.parse.quote(normalized_identity, safe="")
    )


def _receipt(kind: str, payload: Mapping[str, Any]) -> str:
    """以 canonical JSON 生成内容寻址回执，用于比较而非暴露原始 payload。"""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"{kind}://sha256/{digest}"


__all__ = [
    "_canonical_id_ref",
    "_is_exact_effect_identity",
    "_json_object",
    "_opaque_failure",
    "_receipt",
]
