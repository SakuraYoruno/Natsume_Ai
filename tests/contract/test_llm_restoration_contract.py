from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_reflection_chain_runtime_singleton_enables_llm() -> None:
    text = _read("host_brain/app/dependencies.py")
    assert "ReflectionChain(use_llm=True)" in text


def test_bond_narrative_runtime_entrypoints_enable_llm() -> None:
    assert "use_llm=True" in _read("host_brain/tribe/social_brain_agent.py")
    assert "use_llm=True" in _read("host_brain/app/http_hooks_runtime_helpers.py")
    assert "use_llm=True" in _read("host_brain/app/intelligent_runtime.py")


def test_inner_world_dual_ticks_are_registered() -> None:
    text = _read("host_brain/skills/personality_registry.py")
    assert "inner_world_numeric_tick" in text
    assert "inner_world_monologue_tick" in text


def test_llm_restoration_prompt_templates_exist() -> None:
    text = _read("prompts/templates/chat.yaml")
    assert "reflection_chain.llm_templates" in text
    assert "bond_narrative.llm_rewrite.prompt" in text
    assert "style_distiller.llm_render.prompt" in text
    assert "memory_distillation.llm_summary.prompt" in text
    assert "hippocampus.recall_association.prompt" in text
    assert "emotional_silence.llm_hints" in text
    assert "emotion_reactivity.llm_hint.prompt" in text


def test_remaining_restoration_runtime_hooks_exist() -> None:
    assert "_maybe_generate_association_summary" in _read("host_brain/tribe/hippocampus_agent.py")
    assert "PoolSummaryClient" in _read("host_brain/memory/distillation.py")
    assert "LongTermDistiller = LongTermMemoryDistiller" in _read("host_brain/memory/long_term_distiller.py")
    assert "BACKGROUND_LLM_MODE_BLOCKED" in _read("host_brain/scheduler/scheduler.py")
    assert "strict_llm=True" in _read("host_brain/persona/evolution_orchestrator.py")
    assert "strict_llm=True" in _read("host_brain/tribe/prefrontal_agent.py")
    assert "scripts/ci/check_no_rule_degrade.py".split("/")[-1] in _read("docs/50_project/arch/NATSUME_AI_FULL_LLM_CAPABILITY_RESTORATION_PLAN_20260422.md")
