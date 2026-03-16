"""入口路由层工具：fast-pass、narrative 判定等，与建图逻辑解耦（P3 职责分离）。"""

from __future__ import annotations


def is_narrative_only_fast_pass(message: str, *, max_len: int = 120) -> bool:
    """§14.2 fast-pass：纯叙事短句、无执行/代码/翻译关键词时命中，入口层用于锁 CHAT_FAST。

    仅当 message 较短且不包含 EXEC_INTENT/WORK_CODE/WORK_TRANSLATE 关键词时返回 True；
    与 pre_intent_classifier 共用关键词集（从 graph_config_builder 读取），避免未命中 fast-pass 时误升到监督图。
    """
    from host_brain.app.graph_config_builder import (  # noqa: PLC0415
        _PIC_EXEC_INTENT_KEYWORDS,
        _PIC_WORK_CODE_KEYWORDS,
        _PIC_WORK_TRANSLATE_KEYWORDS,
    )
    text = str(message or "").strip().lower()
    if len(text) > max_len:
        return False
    if any(kw in text for kw in _PIC_EXEC_INTENT_KEYWORDS):
        return False
    if any(kw in text for kw in _PIC_WORK_CODE_KEYWORDS):
        return False
    if any(kw in text for kw in _PIC_WORK_TRANSLATE_KEYWORDS):
        return False
    return True
