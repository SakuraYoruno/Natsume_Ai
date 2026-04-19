from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def _load_edge_runtime_texts() -> dict[str, dict[str, str]]:
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
    text = str(_load_edge_runtime_texts().get(section, {}).get(key, default) or "")
    if not kwargs:
        return text
    try:
        return text.format_map({str(k): "" if v is None else str(v) for k, v in kwargs.items()})
    except Exception:
        return text
