"""为 Edge 运行时提供可失败回退的稳定文本模板。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def _load_edge_runtime_texts() -> dict[str, dict[str, str]]:
    """读取并规范化同目录模板文件。

    仅缓存一份结果，让高频错误、状态与证明输出不在关键路径反复访问磁盘；运行中
    修改 JSON 不会自动生效，避免同一请求链前后使用不同措辞。文件缺失或损坏时返回
    空映射，把可用的调用方默认值保留下来，而不是让展示文本掩盖原有业务结果。
    """
    path = Path(__file__).with_name("runtime_texts.json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for section, mapping in raw.items():
        if isinstance(mapping, dict):
            result[str(section)] = {
                str(key): str(value or "")
                for key, value in mapping.items()
            }
    return result


def edge_runtime_text(section: str, key: str, *, default: str = "", **kwargs: Any) -> str:
    """按分区取模板并进行受限格式化。

    变量只被转成文本插入，不能改变模板选择；若模板与变量不匹配则返回未格式化文本，
    使诊断/收据通路仍可给出稳定提示，而无需把格式错误升级为运行时异常。
    """
    text = str(_load_edge_runtime_texts().get(section, {}).get(key, default) or "")
    if not kwargs:
        return text
    try:
        return text.format_map({str(k): "" if v is None else str(v) for k, v in kwargs.items()})
    except Exception:
        return text
