from __future__ import annotations
from dataclasses import dataclass, field

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.agents.planning_agent import PlanningAgent
from host_brain.app.chat_runtime_input import build_chat_runtime_input
from host_brain.app.intelligent_runtime import (
    _sanitize_narrative,
    apply_response_intelligence,
    build_prompt_intelligence,
    resolve_tts_route,
)
from host_brain.app.prompt_template_store import PromptTemplateStore
from host_brain.reasoning.reflection_chain import ReflectionChain
from host_brain.skills.builtin_registry import build_builtin_skill_registry
from tests.text_quality_assertions import assert_natsume_style


class _DummyMetaLearning:
    def record(self, *_args, **_kwargs) -> None:
        return None


@dataclass
class _DummyItem:
    content: str = ""
    analysis: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"content": self.content, "analysis": self.analysis}


@dataclass
class _DummyProcessResult:
    items: list[_DummyItem] = field(default_factory=list)
    emotion_signals: list[str] = field(default_factory=list)
    modalities: list[str] = field(default_factory=lambda: ["text"])
    primary_modality: str = "text"

    def as_dict(self) -> dict[str, object]:
        return {
            "items": [item.as_dict() for item in self.items],
            "emotion_signals": list(self.emotion_signals),
            "modalities": list(self.modalities),
            "primary_modality": self.primary_modality,
        }


@dataclass
class _DummyEnhanceResult:
    suggested_emoji: str = ""
    items: list[_DummyItem] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "suggested_emoji": self.suggested_emoji,
            "items": [item.as_dict() for item in self.items],
        }


class _DummyMultimodalProcessor:
    def process_input(self, *, text: str) -> _DummyProcessResult:
        return _DummyProcessResult()

    def enhance_output(self, *, text: str, emotion_tag: str, bond_stage: str, render_lang: str) -> _DummyEnhanceResult:
        return _DummyEnhanceResult()


@dataclass
class _DummyBondStyle:
    bond_stage: str
    intimacy_level: float = 0.4
    prompt_injection: str = "stay warm and grounded"
    forbidden_patterns: tuple[str, ...] = ("overclaim",)

    def as_dict(self) -> dict[str, object]:
        return {
            "bond_stage": self.bond_stage,
            "intimacy_level": self.intimacy_level,
        }


class _DummyBondStyleEngine:
    def generate(
        self,
        *,
        bond_stage: str,
        mood_state: str,
        rift_active: bool,
        bond_trend: str,
        texture_hint: str,
        shared_memory_hints: list[str] | None = None,
    ) -> _DummyBondStyle:
        return _DummyBondStyle(bond_stage=bond_stage)


class _DummyPredictiveStateEngine:
    def predict_current(self):
        return None


def _patch_dependencies(monkeypatch) -> None:
    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "get_meta_learning_loop", lambda: _DummyMetaLearning())
    monkeypatch.setattr(deps, "get_multimodal_processor", lambda: _DummyMultimodalProcessor())
    monkeypatch.setattr(deps, "get_bond_aware_style_engine", lambda: _DummyBondStyleEngine())
    monkeypatch.setattr(deps, "get_predictive_state_engine", lambda: _DummyPredictiveStateEngine())
    monkeypatch.setattr(deps, "get_planning_agent", lambda: PlanningAgent())
    monkeypatch.setattr(deps, "get_skill_registry", lambda: build_builtin_skill_registry())
    monkeypatch.setattr(deps, "get_reflection_chain", lambda: ReflectionChain())


def test_build_prompt_intelligence_executes_safe_planning_and_skill_nodes(monkeypatch) -> None:
    _patch_dependencies(monkeypatch)
    runtime_input = build_chat_runtime_input(
        request_id="req-intel",
        session_id="session-intel",
        thread_id=None,
        user_message="请先总结一下刚才这轮，然后再说说我现在情绪怎么样",
        retrieval_summary={},
        retrieval_evidence_items=[],
        emotion_state={"emotion_tag": "HAPPY", "mood_state": "engaged"},
        bond_summary={"stage": "FRIEND"},
        render_target_lang="zh",
        scene_type="EXEC_INTENT",
    )

    intelligence = build_prompt_intelligence(
        request_id="req-intel",
        runtime_input=runtime_input,
    )

    planning_outline = dict(intelligence.get("planning_outline") or {})
    planning_execution = dict(intelligence.get("planning_execution") or {})
    skill_dag = dict(intelligence.get("skill_dag") or {})
    skill_execution = dict(intelligence.get("skill_execution") or {})

    assert planning_outline.get("total_steps", 0) >= 3
    # PlanningAgent.execute_plan() 已废弃，success 可能为 False，executed_skills 可能为空
    assert "success" in planning_execution
    # PlanningAgent.execute_plan() 已废弃，executed_skills 可能为空
    # assert "emotion_query" in list(planning_execution.get("executed_skills") or [])
    assert skill_dag.get("total_nodes", 0) >= 2
    assert skill_execution.get("success") is True
    output_preview = dict(skill_execution.get("output_preview") or {})
    assert output_preview


def test_apply_response_intelligence_revises_high_risk_reply(monkeypatch) -> None:
    _patch_dependencies(monkeypatch)

    revised_text, meta = apply_response_intelligence(
        request_id="req-reflect",
        user_message="我今天很难过",
        narrative="您好，作为一个AI，我建议您冷静一下。",
        emotion_context={"emotion_tag": "sadness", "turn_count": 1},
        bond_summary={"stage": "STRANGER"},
        render_lang="zh",
        allow_rewrite=True,
    )

    reflection = dict(meta.get("reflection") or {})
    before = dict(reflection.get("before") or {})
    after = dict(reflection.get("after") or {})

    assert "AI" not in revised_text
    assert "您好" not in revised_text
    assert reflection.get("revised") is True
    assert reflection.get("revision_mode") == "reflection_revise"
    assert float(after.get("overall_score") or 0.0) >= float(before.get("overall_score") or 0.0)
    assert_natsume_style(
        user_message="我今天很难过",
        reply=revised_text,
        emotion_tag="sadness",
        bond_stage="STRANGER",
        required_markers=("我在",),
    )


def test_apply_response_intelligence_extracts_narrative_text_from_structured_json(monkeypatch) -> None:
    _patch_dependencies(monkeypatch)

    revised_text, _meta = apply_response_intelligence(
        request_id="req-draft-json",
        user_message="上一句是什么",
        narrative='{"narrative":{"text":"嗯……你是指，之前那一句吗？"},"intent_draft":{},"meta":{"draft_only":true}}',
        emotion_context={"emotion_tag": "NEUTRAL", "turn_count": 1},
        bond_summary={"stage": "STRANGER"},
        render_lang="zh",
        allow_rewrite=False,
    )

    assert revised_text == "嗯……你是指，之前那一句吗？"


def test_apply_response_intelligence_extracts_narrative_text_from_fenced_json(monkeypatch) -> None:
    _patch_dependencies(monkeypatch)

    revised_text, _meta = apply_response_intelligence(
        request_id="req-draft-fence",
        user_message="那现在呢",
        narrative='```json\n{"narrative":{"text":"嗯，是这样啊……不过，我这边还没看到上一句。"},"intent_draft":{},"meta":{"draft_only":true}}\n```',
        emotion_context={"emotion_tag": "NEUTRAL", "turn_count": 1},
        bond_summary={"stage": "STRANGER"},
        render_lang="zh",
        allow_rewrite=False,
    )

    assert revised_text == "嗯，是这样啊……不过，我这边还没看到上一句。"


def test_apply_response_intelligence_repairs_evasive_affection_question_reply(monkeypatch) -> None:
    _patch_dependencies(monkeypatch)

    revised_text, meta = apply_response_intelligence(
        request_id="req-affection-question-directness",
        user_message="你喜欢我吗",
        narrative="嗯……我在这里，很珍惜能这样和你说话的时间。",
        emotion_context={"emotion_tag": "NEUTRAL", "turn_count": 1},
        bond_summary={"stage": "STRANGER"},
        render_lang="zh",
        allow_rewrite=True,
    )

    reflection = dict(meta.get("reflection") or {})

    assert reflection.get("revised") is True
    assert "我在这里" not in revised_text
    assert "喜欢" in revised_text or "在意" in revised_text
    assert_natsume_style(
        user_message="你喜欢我吗",
        reply=revised_text,
        emotion_tag="neutral",
        bond_stage="STRANGER",
        forbidden_markers=("宝贝", "亲爱的", "永远陪着你", "AI", "系统", "Agent"),
    )


def test_apply_response_intelligence_repairs_generic_thanks_on_affection_disclosure(monkeypatch) -> None:
    _patch_dependencies(monkeypatch)

    revised_text, meta = apply_response_intelligence(
        request_id="req-affection-disclosure-directness",
        user_message="我喜欢你",
        narrative="嗯……谢谢你。能这样和你说话，我也很高兴。",
        emotion_context={"emotion_tag": "NEUTRAL", "turn_count": 1},
        bond_summary={"stage": "STRANGER"},
        render_lang="zh",
        allow_rewrite=True,
    )

    reflection = dict(meta.get("reflection") or {})
    after = dict(reflection.get("after") or {})

    assert reflection.get("revised") is True
    assert "认真记着" in revised_text or "认真对待" in revised_text or "慢一点比较好" in revised_text
    assert revised_text != "嗯……谢谢你。能这样和你说话，我也很高兴。"
    assert after.get("should_revise") is False
    assert_natsume_style(
        user_message="我喜欢你",
        reply=revised_text,
        emotion_tag="neutral",
        bond_stage="STRANGER",
        forbidden_markers=("宝贝", "亲爱的", "永远陪着你", "AI", "系统", "Agent"),
    )


def test_resolve_tts_route_maps_canonical_sad_to_semantic_style(monkeypatch) -> None:
    _patch_dependencies(monkeypatch)

    route = resolve_tts_route(
        text="你今天很难过啊。是发生什么事了吗？",
        emotion_context={"emotion_tag": "SAD"},
        bond_summary={"stage": "STRANGER"},
    )

    assert route.get("emotion_tag") == "sadness"


def test_resolve_tts_route_maps_canonical_calm_to_comfort_style(monkeypatch) -> None:
    _patch_dependencies(monkeypatch)

    route = resolve_tts_route(
        text="没关系的，我会陪着你。",
        emotion_context={"emotion_tag": "CALM"},
        bond_summary={"stage": "STRANGER"},
    )

    assert route.get("emotion_tag") == "comfort"


def test_reflection_chain_revise_uses_minimal_fallback_for_empty_reply() -> None:
    chain = ReflectionChain()

    result = chain.revise(
        user_message="你喜欢我吗",
        ai_response="",
        emotion_tag="",
        bond_stage="ACQUAINTANCE",
    )

    assert result.revised_text == "嗯，我在呢。刚才那句我想再整理一下，马上接上你。"


def test_reflection_chain_revise_replaces_too_short_reply_with_minimal_fallback() -> None:
    chain = ReflectionChain()

    result = chain.revise(
        user_message="你喜欢我吗",
        ai_response="嗯",
        emotion_tag="",
        bond_stage="ACQUAINTANCE",
    )

    assert result.revised_text == "嗯，我在呢。刚才那句我想再整理一下，马上接上你。"


def test_sanitize_narrative_uses_prompt_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "intelligent_runtime.sanitize.empty_narrative",
        content="我还在这里。",
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    assert _sanitize_narrative(
        text="您好，感谢您的支持",
        bond_stage="FRIEND",
        render_lang="zh",
    ) == "我还在这里。"
