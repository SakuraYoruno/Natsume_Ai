"""Neutral JSON extraction helpers with no model/provider authority."""

from __future__ import annotations

import json
import re
from typing import Any


_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]+?)\s*```", re.IGNORECASE)


def extract_json_value(raw: str) -> Any:
    # 此工具只提取模型文本中的 JSON 值，不验证业务 schema、执行命令或选择 provider；
    # 调用方仍须在各自契约边界校验结构与权限。
    text = str(raw or "").strip()
    if not text:
        raise ValueError("JSON response is empty")
    fenced = _FENCE_RE.search(text)
    if fenced:
        # 优先解开 Markdown 代码围栏，兼容模型展示习惯，同时只取首个受限片段，避免
        # 将解释文字拼入 JSON 输入。
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 当整段回复混有前后说明时，从第一个对象/数组起点尝试标准 raw_decode；不做
        # 修补、补括号或宽松语法转换，解析失败仍由上层按模型输出错误处理。
        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char not in "[{":
                continue
            try:
                value, _end = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            return value
    raise ValueError("no valid JSON value found")


def extract_json_object(raw: str) -> dict[str, Any]:
    # 容器类型检查在这里明确失败，避免下游把数组/标量误当命名字段载荷继续执行。
    value = extract_json_value(raw)
    if not isinstance(value, dict):
        raise ValueError("JSON response must be an object")
    return value


def extract_json_array(raw: str) -> list[Any]:
    # 同理，数组入口不隐式包装对象，保持 prompt 契约中“对象/列表”形状的可诊断性。
    value = extract_json_value(raw)
    if not isinstance(value, list):
        raise ValueError("JSON response must be an array")
    return value


__all__ = ["extract_json_array", "extract_json_object", "extract_json_value"]
