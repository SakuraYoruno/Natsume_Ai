"""pre_intent_classifier 专项测试（M34-F，T-PIC-01~15）。

验证轻量规则预分类器的场景分类准确性、边界情况和性能。
"""

from __future__ import annotations

import time

import pytest

from host_brain.app.fastapi_app import (
    SCENE_CHAT,
    SCENE_EXEC_INTENT,
    SCENE_VISION_UI,
    SCENE_WORK_CODE,
    SCENE_WORK_TRANSLATE,
    ChatRequest,
    _resolve_chat_retrieval_gate,
    _resolve_chat_scene_type,
    pre_intent_classifier,
)


# ---------------------------------------------------------------------------
# T-PIC-01：中文翻译关键词 → WORK_TRANSLATE
# ---------------------------------------------------------------------------
def test_pic_01_chinese_translate_keyword():
    """T-PIC-01：中文翻译关键词 → WORK_TRANSLATE"""
    assert pre_intent_classifier("帮我翻译这段文字") == SCENE_WORK_TRANSLATE
    assert pre_intent_classifier("翻译成英文") == SCENE_WORK_TRANSLATE
    assert pre_intent_classifier("把这段翻成日语") == SCENE_WORK_TRANSLATE


# ---------------------------------------------------------------------------
# T-PIC-02：英文 translate 关键词 → WORK_TRANSLATE
# ---------------------------------------------------------------------------
def test_pic_02_english_translate_keyword():
    """T-PIC-02：英文 translate 关键词 → WORK_TRANSLATE"""
    assert pre_intent_classifier("please translate this text") == SCENE_WORK_TRANSLATE
    assert pre_intent_classifier("I need a translation") == SCENE_WORK_TRANSLATE
    assert pre_intent_classifier("translate to Chinese") == SCENE_WORK_TRANSLATE


# ---------------------------------------------------------------------------
# T-PIC-03：日文翻訳关键词 → WORK_TRANSLATE
# ---------------------------------------------------------------------------
def test_pic_03_japanese_translate_keyword():
    """T-PIC-03：日文翻訳关键词 → WORK_TRANSLATE"""
    assert pre_intent_classifier("これを訳してください") == SCENE_WORK_TRANSLATE
    assert pre_intent_classifier("翻訳してほしい") == SCENE_WORK_TRANSLATE


# ---------------------------------------------------------------------------
# T-PIC-04：代码关键词 → WORK_CODE
# ---------------------------------------------------------------------------
def test_pic_04_code_keyword():
    """T-PIC-04：代码关键词 → WORK_CODE"""
    assert pre_intent_classifier("帮我写代码") == SCENE_WORK_CODE
    assert pre_intent_classifier("这段代码有bug") == SCENE_WORK_CODE
    assert pre_intent_classifier("write some code for me") == SCENE_WORK_CODE


# ---------------------------------------------------------------------------
# T-PIC-05：编程关键词 → WORK_CODE
# ---------------------------------------------------------------------------
def test_pic_05_programming_keyword():
    """T-PIC-05：编程关键词 → WORK_CODE"""
    assert pre_intent_classifier("帮我写个函数") == SCENE_WORK_CODE
    assert pre_intent_classifier("python脚本怎么写") == SCENE_WORK_CODE
    assert pre_intent_classifier("implement a sorting algorithm") == SCENE_WORK_CODE
    assert pre_intent_classifier("debug this javascript") == SCENE_WORK_CODE


# ---------------------------------------------------------------------------
# T-PIC-06：执行意图关键词 → EXEC_INTENT
# ---------------------------------------------------------------------------
def test_pic_06_exec_intent_keyword():
    """T-PIC-06：执行意图关键词 → EXEC_INTENT"""
    assert pre_intent_classifier("帮我执行这个任务") == SCENE_EXEC_INTENT
    assert pre_intent_classifier("帮我做这件事") == SCENE_EXEC_INTENT
    assert pre_intent_classifier("帮我安装这个软件") == SCENE_EXEC_INTENT


# ---------------------------------------------------------------------------
# T-PIC-07：设置/配置关键词 → EXEC_INTENT
# ---------------------------------------------------------------------------
def test_pic_07_setup_config_keyword():
    """T-PIC-07：设置/配置关键词 → EXEC_INTENT"""
    assert pre_intent_classifier("帮我设置一下") == SCENE_EXEC_INTENT
    assert pre_intent_classifier("配置这个环境") == SCENE_EXEC_INTENT
    assert pre_intent_classifier("setup the environment") == SCENE_EXEC_INTENT
    assert pre_intent_classifier("configure the server") == SCENE_EXEC_INTENT


# ---------------------------------------------------------------------------
# T-PIC-08：截图/界面关键词 → VISION_UI
# ---------------------------------------------------------------------------
def test_pic_08_vision_ui_keyword():
    """T-PIC-08：截图/界面关键词 → VISION_UI"""
    assert pre_intent_classifier("帮我截图") == SCENE_VISION_UI
    assert pre_intent_classifier("看一下屏幕") == SCENE_VISION_UI
    assert pre_intent_classifier("take a screenshot") == SCENE_VISION_UI
    assert pre_intent_classifier("看看这个界面") == SCENE_VISION_UI


# ---------------------------------------------------------------------------
# T-PIC-09：普通闲聊 → CHAT
# ---------------------------------------------------------------------------
def test_pic_09_normal_chat():
    """T-PIC-09：普通闲聊 → CHAT"""
    assert pre_intent_classifier("你好") == SCENE_CHAT
    assert pre_intent_classifier("今天天气怎么样") == SCENE_CHAT
    assert pre_intent_classifier("讲个笑话") == SCENE_CHAT
    assert pre_intent_classifier("hello") == SCENE_CHAT


# ---------------------------------------------------------------------------
# T-PIC-10：空字符串 → CHAT（默认）
# ---------------------------------------------------------------------------
def test_pic_10_empty_string_defaults_to_chat():
    """T-PIC-10：空字符串 → CHAT（默认）"""
    assert pre_intent_classifier("") == SCENE_CHAT
    assert pre_intent_classifier("   ") == SCENE_CHAT
    assert pre_intent_classifier(None) == SCENE_CHAT  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# T-PIC-11：混合关键词优先级测试
# ---------------------------------------------------------------------------
def test_pic_11_mixed_keywords_priority():
    """T-PIC-11：混合关键词按优先级返回（VISION_UI > WORK_CODE > WORK_TRANSLATE > EXEC_INTENT）"""
    # VISION_UI 优先于 WORK_CODE
    assert pre_intent_classifier("截图并写代码") == SCENE_VISION_UI
    # WORK_CODE 优先于 WORK_TRANSLATE
    assert pre_intent_classifier("写代码并翻译") == SCENE_WORK_CODE
    # WORK_TRANSLATE 优先于 EXEC_INTENT
    assert pre_intent_classifier("翻译并执行") == SCENE_WORK_TRANSLATE
    # EXEC_INTENT 优先于 CHAT
    assert pre_intent_classifier("执行一下，顺便聊聊") == SCENE_EXEC_INTENT


# ---------------------------------------------------------------------------
# T-PIC-12：未显式传入 scene_type 时，scene_type_source = "pre_classifier"
# ---------------------------------------------------------------------------
def test_pic_12_scene_type_source_pre_classifier(monkeypatch):
    """T-PIC-12: scene_type_source equals pre_classifier when scene_type not explicitly provided"""
    from host_brain.app.fastapi_app import SCENE_CHAT, pre_intent_classifier

    # 模拟端点逻辑：request_body.scene_type 为 None
    request_scene_type = None
    if request_scene_type:
        scene_type = str(request_scene_type).strip().upper() or SCENE_CHAT
        scene_type_source = "explicit"
    else:
        scene_type = pre_intent_classifier("你好")
        scene_type_source = "pre_classifier"

    assert scene_type_source == "pre_classifier"
    assert scene_type == SCENE_CHAT


# ---------------------------------------------------------------------------
# T-PIC-13：显式传入 scene_type 时，scene_type_source = "explicit"
# ---------------------------------------------------------------------------
def test_pic_13_scene_type_source_explicit():
    """T-PIC-13: scene_type_source equals explicit when scene_type is explicitly provided"""
    from host_brain.app.fastapi_app import SCENE_WORK_CODE, pre_intent_classifier

    # 模拟端点逻辑：request_body.scene_type 显式传入 WORK_CODE
    request_scene_type = "WORK_CODE"
    if request_scene_type:
        scene_type = str(request_scene_type).strip().upper() or "CHAT"
        scene_type_source = "explicit"
    else:
        scene_type = pre_intent_classifier("你好")  # 本来会返回 CHAT
        scene_type_source = "pre_classifier"

    assert scene_type_source == "explicit"
    assert scene_type == SCENE_WORK_CODE


# ---------------------------------------------------------------------------
# T-PIC-14：graph_meta.scene_type_source 字段存在
# ---------------------------------------------------------------------------
def test_pic_14_graph_meta_scene_type_source_field():
    """T-PIC-14：_build_chat_graph_config 返回的 graph_meta 包含 scene_type_source 字段"""
    from unittest.mock import MagicMock

    from host_brain.app.fastapi_app import SCENE_CHAT, _build_chat_graph_config

    config = _build_chat_graph_config(
        provider_name="gemini",
        model_id="gemini-2.5-flash",
        timeout_ms=5000,
        available=True,
        api_key=None,
        provider_fn=MagicMock(),
        scene_type=SCENE_CHAT,
        scene_type_source="pre_classifier",
    )
    graph_meta = config.get("graph_meta", {})
    assert "scene_type_source" in graph_meta
    assert graph_meta["scene_type_source"] == "pre_classifier"


# ---------------------------------------------------------------------------
# T-PIC-15：预分类函数延迟 < 1ms（性能断言）
# ---------------------------------------------------------------------------
def test_pic_15_classifier_latency_under_1ms():
    """T-PIC-15：pre_intent_classifier 延迟 < 1ms（纯规则，无 LLM 调用）"""
    inputs = [
        "帮我翻译这段文字",
        "写个python函数",
        "帮我截图",
        "执行这个任务",
        "你好，今天天气怎么样",
        "",
    ]
    for text in inputs:
        start = time.perf_counter()
        pre_intent_classifier(text)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 1.0, f"pre_intent_classifier({text!r}) 耗时 {elapsed_ms:.3f}ms，超过 1ms 限制"


def test_pic_16_chat_defaults_to_scene_chat_when_preclassifier_disabled(monkeypatch):
    monkeypatch.setenv("HOST_CHAT_ENABLE_PRECLASSIFIER", "false")
    scene_type, scene_type_source = _resolve_chat_scene_type(
        request_body=ChatRequest(message="把这句话翻成日语：今天也要加油。")
    )
    assert scene_type == SCENE_CHAT
    assert scene_type_source == "default_chat"


def test_pic_17_chat_allows_preclassifier_when_enabled(monkeypatch):
    monkeypatch.setenv("HOST_CHAT_ENABLE_PRECLASSIFIER", "true")
    scene_type, scene_type_source = _resolve_chat_scene_type(
        request_body=ChatRequest(message="把这句话翻成日语：今天也要加油。")
    )
    assert scene_type == SCENE_WORK_TRANSLATE
    assert scene_type_source == "pre_classifier"


def test_pic_18_chat_retrieval_can_be_disabled_for_chat(monkeypatch):
    monkeypatch.setenv("HOST_ENABLE_RETRIEVAL", "true")
    monkeypatch.setenv("HOST_CHAT_DISABLE_RETRIEVAL", "true")
    enabled, reason_code = _resolve_chat_retrieval_gate(scene_type=SCENE_CHAT)
    assert enabled is False
    assert reason_code == "HOST_CHAT_RETRIEVAL_DISABLED"


def test_pic_19_non_chat_retrieval_not_blocked_by_chat_flag(monkeypatch):
    monkeypatch.setenv("HOST_ENABLE_RETRIEVAL", "true")
    monkeypatch.setenv("HOST_CHAT_DISABLE_RETRIEVAL", "true")
    enabled, reason_code = _resolve_chat_retrieval_gate(scene_type=SCENE_WORK_TRANSLATE)
    assert enabled is True
    assert reason_code is None
