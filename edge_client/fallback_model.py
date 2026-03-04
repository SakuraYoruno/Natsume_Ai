"""Edge 兜底小模型门控（M15-D）。

FallbackModelGate：
- 默认卸载，按需加载（不常驻 GPU）
- 输出永远标记为 draft_only=True
- 不可用时快速失败（FALLBACK_MODEL_UNAVAILABLE）
- 不得包含裁决或执行事实宣告
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from host_brain.core.errors import (
    FALLBACK_MODEL_UNAVAILABLE,
    VOICE_GATE_TASK_INVALID,
    VOICE_SCHEMA_INVALID,
)
from host_brain.core.ids import new_request_id, now_ms


# 合法的 task_type 枚举
VALID_TASK_TYPES = frozenset({
    "scene_response",
    "input_clean",
    "summary",
    "retrieval_hint",
})


@dataclass(frozen=True)
class FallbackOutput:
    """兜底小模型输出（永远 draft_only=True）。"""
    output_id: str
    text: str
    draft_only: bool = True         # 永远为 True（不得包含裁决或执行事实）
    reason_code: str | None = None  # 不可用时填 FALLBACK_MODEL_UNAVAILABLE
    stage: str | None = None
    evidence_ref: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "output_id": self.output_id,
            "text": self.text,
            "draft_only": self.draft_only,
            "reason_code": self.reason_code,
            "stage": self.stage,
            "evidence_ref": self.evidence_ref,
        }


class FallbackModelGate:
    """
    Edge 兜底小模型门控（M15-D）。

    - 默认卸载，按需加载（不常驻 GPU）
    - 输出永远标记为 draft_only=True
    - 不可用时快速失败（不阻塞主流程）
    - 不得包含裁决或执行事实宣告
    """

    def __init__(
        self,
        *,
        model_name: str = "ollama-3b-q4",
        auto_load: bool = False,
    ) -> None:
        self.model_name = model_name
        self._loaded: bool = False
        self._auto_load = auto_load

    # ── 生命周期 ──────────────────────────────────────────────────────────

    def load(self) -> bool:
        """按需加载兜底模型（默认卸载）。返回是否加载成功。"""
        self._loaded = True
        return True

    def unload(self) -> None:
        """卸载兜底模型（释放资源）。"""
        self._loaded = False

    def is_available(self) -> bool:
        """检查兜底模型是否可用（已加载）。"""
        return self._loaded

    # ── 生成接口 ──────────────────────────────────────────────────────────

    def generate(
        self,
        prompt: str,
        *,
        task_type: str,
        request_id: str,
        ts_ms: int | None = None,
    ) -> FallbackOutput:
        """
        调用兜底小模型（Ollama 3B 量化）。

        - 输出永远标记为 draft_only=True
        - 不可用时快速失败（FALLBACK_MODEL_UNAVAILABLE）
        - task_type 非法 → VOICE_SCHEMA_INVALID（快速失败）
        - 不得包含裁决或执行事实宣告
        """
        output_id = new_request_id()

        # task_type 校验
        if task_type not in VALID_TASK_TYPES:
            err = ValueError(f"invalid fallback task_type: {task_type!r}")
            err.reason_code = VOICE_SCHEMA_INVALID  # type: ignore[attr-defined]
            raise err

        # 不可用时快速失败
        if not self._loaded:
            if self._auto_load:
                self.load()
            else:
                return FallbackOutput(
                    output_id=output_id,
                    text="",
                    draft_only=True,
                    reason_code=FALLBACK_MODEL_UNAVAILABLE,
                    stage="model_load_check",
                )

        # 生成（stub：实际接入 Ollama RPC）
        text = self._stub_generate(prompt, task_type=task_type)

        return FallbackOutput(
            output_id=output_id,
            text=text,
            draft_only=True,
            reason_code=None,
            stage=None,
        )

    # ── 内部 stub（实际接入 Ollama RPC 时替换）────────────────────────────

    def _stub_generate(self, prompt: str, *, task_type: str) -> str:
        """Stub 生成（测试用）。实际接入 Ollama RPC 时替换。"""
        return f"[draft:{task_type}] {prompt[:64]}"
