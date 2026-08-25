"""Execution profile 单元测试（P1 先选图再跑图）。"""

from __future__ import annotations

import pytest

from host_brain.app.execution_profile import (
    PROFILE_CHAT_FAST,
    PROFILE_INTENT_EXEC,
    PROFILE_WORKFLOW_SUPERVISED,
    resolve_execution_profile,
)
from host_brain.model_runtime.prompt_builder_utils import (
    SCENE_CHAT,
    SCENE_EXEC_INTENT,
    SCENE_VISION_UI,
    SCENE_WORK_CODE,
    SCENE_WORK_TRANSLATE,
)


def test_resolve_chat_returns_chat_fast() -> None:
    assert resolve_execution_profile(SCENE_CHAT) == PROFILE_CHAT_FAST
    assert resolve_execution_profile("CHAT") == PROFILE_CHAT_FAST


def test_resolve_vision_ui_returns_chat_fast() -> None:
    assert resolve_execution_profile(SCENE_VISION_UI) == PROFILE_CHAT_FAST


def test_resolve_exec_intent_returns_intent_exec() -> None:
    assert resolve_execution_profile(SCENE_EXEC_INTENT) == PROFILE_INTENT_EXEC
    assert resolve_execution_profile("EXEC_INTENT") == PROFILE_INTENT_EXEC


def test_resolve_work_code_returns_workflow_supervised() -> None:
    assert resolve_execution_profile(SCENE_WORK_CODE) == PROFILE_WORKFLOW_SUPERVISED


def test_resolve_work_translate_returns_workflow_supervised() -> None:
    assert resolve_execution_profile(SCENE_WORK_TRANSLATE) == PROFILE_WORKFLOW_SUPERVISED


def test_resolve_auto_returns_workflow_supervised() -> None:
    assert resolve_execution_profile("AUTO") == PROFILE_WORKFLOW_SUPERVISED


def test_resolve_unknown_falls_back_to_chat_fast() -> None:
    assert resolve_execution_profile("UNKNOWN_XYZ") == PROFILE_CHAT_FAST
    assert resolve_execution_profile("") == PROFILE_CHAT_FAST
    assert resolve_execution_profile(None) == PROFILE_CHAT_FAST  # type: ignore[arg-type]


def test_resolve_chat_narrative_only_stays_chat_fast() -> None:
    """§14.2：SCENE_CHAT + fast-pass 命中 → CHAT_FAST。"""
    assert resolve_execution_profile(SCENE_CHAT, narrative_only=True) == PROFILE_CHAT_FAST


def test_resolve_chat_classifier_says_exec_goes_intent_exec() -> None:
    """§14.2：SCENE_CHAT + 明确执行型 → INTENT_EXEC。"""
    assert resolve_execution_profile(SCENE_CHAT, classifier_says_exec=True) == PROFILE_INTENT_EXEC


def test_resolve_chat_no_signal_stays_chat_fast() -> None:
    """§14.2：SCENE_CHAT 且未命中 fast-pass、未明确执行型 → 仍 CHAT_FAST。"""
    assert resolve_execution_profile(SCENE_CHAT, narrative_only=False, classifier_says_exec=False) == PROFILE_CHAT_FAST
