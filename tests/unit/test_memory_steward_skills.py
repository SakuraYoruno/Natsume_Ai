from __future__ import annotations

from host_brain.skills.impl.memory_executor import execute_memory_skill
from host_brain.skills.memory_registry import MemorySkillProvider, build_memory_skill_registry


def test_memory_preference_extract_builds_soft_candidates() -> None:
    provider = MemorySkillProvider()
    payload = provider.memory_preference_extract(
        {
            "text": "我喜欢冷色系 UI，不喜欢夸张动效。以后请直接给结论。",
            "source": "unit",
            "max_candidates": 8,
        },
        {"request_id": "req-memory-pref"},
    )

    result = execute_memory_skill(payload, payload["_context"])
    detail = result["detail"]
    candidates = detail["candidates"]

    assert result["status"] == "OK"
    assert detail["write_policy"] == "candidate_only"
    assert detail["candidate_count"] == 3
    assert {item["polarity"] for item in candidates} == {"positive", "negative"}
    assert any("冷色系 UI" in item["text"] for item in candidates)
    assert any("夸张动效" in item["text"] for item in candidates)
    assert all(item["write_policy"] == "soft_memory_candidate" for item in candidates)


def test_memory_consolidate_builds_preference_and_task_candidates() -> None:
    provider = MemorySkillProvider()
    payload = provider.memory_consolidate(
        {
            "text": "我喜欢短结论。今天确认主链工具闭环通过。下一步需要扩充 Host 侧技能。",
            "source": "unit",
            "max_candidates": 8,
        },
        {"request_id": "req-memory-consolidate"},
    )

    result = execute_memory_skill(payload, payload["_context"])
    detail = result["detail"]
    candidates = detail["candidates"]

    assert result["status"] == "OK"
    assert detail["source"] == "rule_based_memory_consolidator"
    assert detail["candidate_count"] >= 3
    assert any(item["kind"] == "preference" for item in candidates)
    assert any(item["kind"] == "task_event" for item in candidates)
    assert any("主链工具闭环通过" in item["text"] for item in candidates)


def test_memory_registry_exposes_steward_skills_with_low_risk() -> None:
    registry = build_memory_skill_registry()

    preference_meta = registry.require("memory_preference_extract")
    consolidate_meta = registry.require("memory_consolidate")

    assert preference_meta.risk_level == 1
    assert preference_meta.require_confirm is False
    assert preference_meta.family == "memory"
    assert "preference" in preference_meta.capability_tags
    assert consolidate_meta.risk_level == 1
    assert consolidate_meta.require_confirm is False
    assert consolidate_meta.family == "memory"
    assert "consolidation" in consolidate_meta.capability_tags
