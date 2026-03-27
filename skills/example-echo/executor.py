"""Example Echo Executor — 示例 Skill 执行器。"""

from __future__ import annotations

from typing import Any


def execute(payload: dict[str, Any]) -> dict[str, Any]:
    """回显传入的文本。"""
    text = str(payload.get("text") or payload.get("content") or "").strip()
    return {
        "status": "OK",
        "manifest_ref": f"example_echo::run::{text[:40]}",
        "echo": text,
        "detail": f"echoed {len(text)} chars",
    }
