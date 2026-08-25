"""Execution profile：入口层路由定版，决定跑哪张图。

仅入口层可写 execution_profile；下游只读。
§9 路由定版 + §14.2 SCENE_CHAT 细分规则（fast-pass / 明确执行型）。
"""

from __future__ import annotations

from host_brain.model_runtime.prompt_builder_utils import (
    SCENE_CHAT as _SCENE_CHAT,
    SCENE_EXEC_INTENT as _SCENE_EXEC_INTENT,
    SCENE_VISION_UI as _SCENE_VISION_UI,
    SCENE_WORK_CODE as _SCENE_WORK_CODE,
    SCENE_WORK_TRANSLATE as _SCENE_WORK_TRANSLATE,
)

# 与 graph_config_builder.SCENE_TYPE_AUTO 一致，此处避免循环导入
_SCENE_TYPE_AUTO = "AUTO"

PROFILE_CHAT_FAST = "CHAT_FAST"
PROFILE_WORKFLOW_SUPERVISED = "WORKFLOW_SUPERVISED"
PROFILE_INTENT_EXEC = "INTENT_EXEC"

def resolve_execution_profile(
    scene_type: str,
    *,
    narrative_only: bool | None = None,
    classifier_says_exec: bool | None = None,
) -> str:
    """由 scene_type 与 chat 细分信号解析 execution_profile。§14.2 固定规则。

    1. scene_type == SCENE_EXEC_INTENT → INTENT_EXEC
    2. scene_type == SCENE_CHAT 且 fast-pass 命中 → CHAT_FAST
    3. scene_type == SCENE_CHAT 且 fast-pass 未命中：
       - 若 classifier/规则明确判定为执行型（classifier_says_exec=True）→ INTENT_EXEC
       - 否则 → CHAT_FAST（不得因未命中 fast-pass 默认升到 WORKFLOW_SUPERVISED）
    4. scene_type in {WORK_CODE, WORK_TRANSLATE, AUTO} → WORKFLOW_SUPERVISED

    narrative_only: True 表示 fast-pass 命中，直接 CHAT_FAST。
    classifier_says_exec: True 表示明确执行意图，在 SCENE_CHAT 下转 INTENT_EXEC。
    """
    scene = str(scene_type or _SCENE_CHAT).strip().upper() or _SCENE_CHAT
    if scene == _SCENE_EXEC_INTENT:
        return PROFILE_INTENT_EXEC
    if scene in (_SCENE_WORK_CODE, _SCENE_WORK_TRANSLATE, _SCENE_TYPE_AUTO):
        return PROFILE_WORKFLOW_SUPERVISED
    if scene in (_SCENE_CHAT, _SCENE_VISION_UI):
        if narrative_only:
            return PROFILE_CHAT_FAST
        if classifier_says_exec:
            return PROFILE_INTENT_EXEC
        return PROFILE_CHAT_FAST
    return PROFILE_CHAT_FAST
