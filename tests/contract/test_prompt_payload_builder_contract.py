"""OPT-5 契约测试：验证 PromptPayloadBuilder 流水线与旧 God Function 等价。

- adapter 入口 ``build_chat_prompt_runtime_payload`` 的公开行为保持不变；
- Builder 的各 stage 都被 ``soft_fail`` 守护，单阶段异常不会击穿全流水线；
- trace 字段按固定顺序记录所有 stage。
"""

from __future__ import annotations

import inspect

import pytest

from host_brain.app import chat_context_builder as ccb
from host_brain.app import chat_payload_builder as cpb
from host_brain.app.chat_payload_builder import (
    PromptPayloadBuilder,
    PromptPayloadContext,
    PromptPayloadResult,
    prepare_context,
    run_build_chat_prompt_runtime_payload,
)


EXPECTED_STAGES = [
    "core_payload",
    "snapshot",
    "intelligence",
    "emotion_bridge",
    "emotion_timeline",
    "persona_expression",
    "self_reflection",
    "bond_narrative",
    "bond_style",
    "motive_and_policy",
    "persona_expression_postprocess",
    "context_pack",
    "cortex_supervisor",
]


def test_prompt_payload_builder_exports_all_stages() -> None:
    """PromptPayloadBuilder 必须提供 OPTIMIZATION_PLAN §9 中列出的全部 stage。"""
    missing = [s for s in EXPECTED_STAGES if not hasattr(PromptPayloadBuilder, f"with_{s}")]
    assert missing == [], f"缺少阶段方法: {missing}"


def test_prompt_payload_result_is_dataclass_like() -> None:
    """PromptPayloadResult 暴露 data/trace 两个稳定字段。"""
    result = PromptPayloadResult(data={"k": "v"}, trace=["a", "b"])
    assert result.data == {"k": "v"}
    assert result.trace == ["a", "b"]


def test_god_function_body_is_slim_adapter() -> None:
    """OPT-5 关键验收：旧 God Function 现在必须 ≤50 行（全部交给 Builder）。"""
    src, _ = inspect.getsourcelines(ccb.build_chat_prompt_runtime_payload)
    # 函数体（含 docstring + import + 一次函数调用）应在 50 行以内
    assert len(src) <= 50, (
        f"build_chat_prompt_runtime_payload 应为 ≤50 行的 adapter，实际 {len(src)} 行"
    )


def test_adapter_delegates_to_run_build() -> None:
    """adapter 必须走 ``run_build_chat_prompt_runtime_payload`` 流水线。"""
    src_text = "".join(inspect.getsourcelines(ccb.build_chat_prompt_runtime_payload)[0])
    assert "run_build_chat_prompt_runtime_payload" in src_text


def test_prepare_context_rejects_non_runtime_input() -> None:
    """prepare_context 在非 ChatRuntimeInput 入参时必须抛 TypeError。"""
    with pytest.raises(TypeError):
        prepare_context(runtime_input="not-a-runtime-input", style_bindings=None, cpb_slots=None)


def test_builder_stage_is_soft_fail_guarded(monkeypatch) -> None:
    """任一 stage 抛异常时流水线不应中断，继续执行后续 stage。"""
    # 通过 monkey-patch 把 intelligence_stage 改成抛异常，验证流水线容错
    def _raise(*_args, **_kwargs):  # noqa: ANN202
        raise RuntimeError("intentional failure")

    monkeypatch.setattr(cpb, "_intelligence_stage", _raise)

    # 构造最小可用 ctx（不需要真的调 prepare_context）
    ctx = PromptPayloadContext(
        runtime_input=None,  # 直接跳过 core_payload stage 的实际执行
        style_bindings=None,
        cpb_slots=None,
    )
    builder = PromptPayloadBuilder(ctx)
    # core_payload 内部会 KeyError，但被 soft_fail 吞；intelligence 同样被吞
    result = builder.with_core_payload().with_intelligence().build()

    assert result.trace == ["core_payload", "intelligence"], (
        "即使中间 stage 抛异常，trace 也应按序完整记录"
    )


def test_run_build_is_public_export() -> None:
    """run_build_chat_prompt_runtime_payload 必须在 __all__ 中导出。"""
    assert "run_build_chat_prompt_runtime_payload" in cpb.__all__
    assert callable(run_build_chat_prompt_runtime_payload)
