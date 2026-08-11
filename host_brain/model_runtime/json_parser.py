"""Neutral JSON extraction helpers with no model/provider authority."""

from __future__ import annotations

import json
import re
from typing import Any


_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]+?)\s*```", re.IGNORECASE)


def extract_json_value(raw: str) -> Any:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("JSON response is empty")
    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
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
    value = extract_json_value(raw)
    if not isinstance(value, dict):
        raise ValueError("JSON response must be an object")
    return value


def extract_json_array(raw: str) -> list[Any]:
    value = extract_json_value(raw)
    if not isinstance(value, list):
        raise ValueError("JSON response must be an array")
    return value


__all__ = ["extract_json_array", "extract_json_object", "extract_json_value"]
