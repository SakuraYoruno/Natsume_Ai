from __future__ import annotations

import host_brain.tribe.language_system_agent as lsa
from host_brain.tribe.language_system_agent import LanguageSystemAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


def test_language_system_agent_wernicke_and_broca_dual_system() -> None:
    agent = LanguageSystemAgent(state_registry=TribeStateRegistry())

    dual = agent.resolve_dual_system(
        user_text="请帮我整理今天的需求，并且我现在有点焦虑。",
        bond_stage="friend",
        emotion_tag="ANXIOUS",
        loneliness_level=0.64,
        high_loneliness_flag=True,
        response_goal="chat_reply",
    )

    assert dual["wernicke"]["intent_type"] == "task_request"
    assert dual["wernicke"]["emotion_tag"] == "ANXIOUS"
    assert dual["broca"]["response_mode"] == "structured"
    assert dual["style"]["tone"] in {"calm", "caring"}


def test_language_system_agent_get_language_style_compatibility() -> None:
    agent = LanguageSystemAgent(state_registry=TribeStateRegistry())

    style_only = agent.get_language_style(
        bond_stage="familiar",
        emotion_tag="NEUTRAL",
        loneliness_level=0.1,
        high_loneliness_flag=False,
    )
    style_with_dual = agent.get_language_style(
        bond_stage="friend",
        emotion_tag="SAD",
        loneliness_level=0.82,
        high_loneliness_flag=True,
        user_text="为什么我总是觉得很孤单？",
    )

    assert "dual_system" not in style_only
    assert style_with_dual["tone"] == "caring"
    assert "陪伴感" in style_with_dual["loneliness_hint"]
    assert "dual_system" in style_with_dual
    assert style_with_dual["dual_system"]["wernicke"]["intent_type"] in {"question", "emotional_disclosure"}


def test_language_system_agent_runtime_texts_override(monkeypatch) -> None:
    monkeypatch.setattr(
        lsa,
        "resolve_prompt_template_json_merged",
        lambda template_id: {
            "loneliness_hints": {
                "high": "自定义高孤独提示。",
                "mid": "自定义中孤独提示。",
            }
        },
    )

    agent = LanguageSystemAgent(state_registry=TribeStateRegistry())

    high_style = agent.get_language_style(
        bond_stage="friend",
        emotion_tag="NEUTRAL",
        loneliness_level=0.85,
        high_loneliness_flag=True,
    )
    mid_style = agent.get_language_style(
        bond_stage="friend",
        emotion_tag="NEUTRAL",
        loneliness_level=0.5,
        high_loneliness_flag=False,
    )

    assert high_style["loneliness_hint"] == "自定义高孤独提示。"
    assert mid_style["loneliness_hint"] == "自定义中孤独提示。"
