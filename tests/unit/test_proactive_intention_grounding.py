"""C2-3 模板兜底也用意图开场 单测。

验证：LLM 缺席/失败回退模板时，若 ctx 带 intention_hint（C2-2 算出的 grounded 项目开场），
则兜底开场用它，而不是退化成通用模板。
"""

from __future__ import annotations

import time

from host_brain.skills.impl.proactive_talk import STATUS_EXECUTED, execute_proactive_talk

INTENTION = "我最近一直在想「陪你追天文」这事，想顺便问问你。"


def _run(*, intention_hint=""):
    ctx = {
        "last_interaction_ts": int(time.time() * 1000) - 6 * 3600 * 1000,  # 6h 前，过空闲门
        "sys_mode": "normal",
        # 不放 llm_generator → 走模板兜底路径
    }
    if intention_hint:
        ctx["intention_hint"] = intention_hint
    return execute_proactive_talk(args={"idle_threshold_ms": 60 * 60 * 1000}, context=ctx)


def test_template_fallback_uses_intention_hint() -> None:
    res = _run(intention_hint=INTENTION)
    assert res["status"] == STATUS_EXECUTED
    assert "天文" in res["message"]["content"]


def test_template_fallback_without_intention_is_generic() -> None:
    res = _run()
    assert res["status"] == STATUS_EXECUTED
    assert "天文" not in res["message"]["content"]
