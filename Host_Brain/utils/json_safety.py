from __future__ import annotations

import json
import re
from typing import Any, Dict, Tuple


_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")
_JSON_ARRAY_RE = re.compile(r"\[[\s\S]*\]")


def extract_json_candidate(text: str) -> str:
    """
    从模型输出中抓取第一个看起来像 JSON 的对象/数组片段（用于“有脏输出”时的自救）。
    """
    if not text:
        return ""
    m = _JSON_OBJECT_RE.search(text)
    if m:
        return m.group(0)
    m = _JSON_ARRAY_RE.search(text)
    if m:
        return m.group(0)
    return ""


def repair_json_like(text: str) -> str:
    """
    针对常见 JSON 漂移做启发式修复：
    - 去掉 ```json 围栏
    - 去掉尾逗号
    - True/False/None/undefined 统一为 JSON 语义
    注意：这是 best-effort，不保证所有情况都能修好。
    """
    if not text:
        return ""
    s = text.strip()

    # 去掉 markdown 代码围栏
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```$", "", s)

    # 只保留第一个对象/数组（避免前后掺杂解释文本）
    cand = extract_json_candidate(s)
    if cand:
        s = cand

    # 单引号转双引号（非常粗糙；只在没有双引号时尝试）
    if "'" in s and '"' not in s:
        s = s.replace("'", '"')

    # 尾逗号
    s = re.sub(r",\s*([}\]])", r"\1", s)

    # Python/JS 布尔与空值
    s = re.sub(r"\bTrue\b", "true", s)
    s = re.sub(r"\bFalse\b", "false", s)
    s = re.sub(r"\bNone\b", "null", s)
    s = re.sub(r"\bundefined\b", "null", s)

    return s


def safe_json_loads(text: str) -> Tuple[Any, Dict[str, Any]]:
    """
    安全解析 JSON：
    - 先直接 json.loads
    - 失败则 repair_json_like 后再试
    返回 (obj, meta)，meta 会标记 repaired 与 error。
    """
    meta: Dict[str, Any] = {"repaired": False, "error": ""}
    if not text or not text.strip():
        meta["error"] = "empty"
        return None, meta

    try:
        return json.loads(text), meta
    except Exception:
        pass

    repaired = repair_json_like(text)
    meta["repaired"] = True
    try:
        return json.loads(repaired), meta
    except Exception as e:
        meta["error"] = f"json_parse_failed: {type(e).__name__}: {e}"
        return None, meta
