from __future__ import annotations

from host_brain.skills.impl.memory_executor import execute_memory_skill
from host_brain.skills.memory_registry import MemorySkillProvider
from host_brain.skills.registry import SkillRegistry


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


def test_memory_search_defaults_task_events_to_recent_episode_without_semantic() -> None:
    seen_semantic_args: dict[str, object] = {}

    def semantic_recaller(**kwargs: object) -> list[dict[str, object]]:
        seen_semantic_args.update(kwargs)
        return []

    class _EpisodicStoreStub:
        def recall(self, *, query_text: str, top_k: int = 5) -> list[dict[str, object]]:
            return [
                {
                    "memory_id": "mem-tool-loop-001",
                    "text": f"Blue remembers {query_text}",
                    "score": 0.91,
                    "metadata": {"source": "unit"},
                }
            ][:top_k]

    payload = {
        "action": "search",
        "manifest_ref": "memory_search::tool loop",
        "query": "tool loop",
        "memory_type": "all",
        "tags": ["task", "event"],
        "top_k": 5,
        "threshold": 0.0,
        "session_id": "unit-session",
        "session_filter": False,
    }

    result = execute_memory_skill(
        payload,
        {
            "semantic_recaller": semantic_recaller,
            "episodic_store": _EpisodicStoreStub(),
        },
    )

    detail = result["detail"]
    assert result["status"] == "OK"
    assert seen_semantic_args == {}
    assert detail["search_scope"] == "recent_episode"
    assert detail["source"] == "episodic_store"
    assert detail["results"][0]["content"] == "Blue remembers tool loop"
    assert detail["results"][0]["doc_id"] == "mem-tool-loop-001"


def test_memory_search_hybrid_falls_back_to_episodic_when_semantic_is_empty() -> None:
    seen_semantic_args: dict[str, object] = {}

    def semantic_recaller(**kwargs: object) -> list[dict[str, object]]:
        seen_semantic_args.update(kwargs)
        return []

    class _EpisodicStoreStub:
        def recall(self, *, query_text: str, top_k: int = 5) -> list[dict[str, object]]:
            return [
                {
                    "memory_id": "mem-hybrid-001",
                    "text": f"Hybrid fallback remembers {query_text}",
                    "score": 0.93,
                    "metadata": {"source": "unit"},
                }
            ][:top_k]

    result = execute_memory_skill(
        {
            "action": "search",
            "manifest_ref": "memory_search::hybrid",
            "query": "tool loop",
            "memory_type": "all",
            "search_scope": "hybrid",
            "top_k": 5,
            "threshold": 0.0,
            "session_id": "unit-session",
            "session_filter": False,
        },
        {
            "semantic_recaller": semantic_recaller,
            "episodic_store": _EpisodicStoreStub(),
        },
    )

    detail = result["detail"]
    assert result["status"] == "OK"
    assert seen_semantic_args["session_id"] is None
    assert detail["search_scope"] == "hybrid"
    assert detail["source"] == "episodic_store"
    assert detail["results"][0]["content"] == "Hybrid fallback remembers tool loop"


def test_memory_search_long_term_does_not_fall_back_to_recent_episode() -> None:
    def semantic_recaller(**_: object) -> list[dict[str, object]]:
        return []

    class _EpisodicStoreStub:
        def recall(self, *, query_text: str, top_k: int = 5) -> list[dict[str, object]]:
            _ = query_text, top_k
            return [{"memory_id": "mem-recent-001", "text": "recent event", "score": 1.0}]

    result = execute_memory_skill(
        {
            "action": "search",
            "manifest_ref": "memory_search::long_term",
            "query": "user preference",
            "memory_type": "semantic",
            "search_scope": "long_term",
            "top_k": 5,
            "threshold": 0.0,
        },
        {
            "semantic_recaller": semantic_recaller,
            "episodic_store": _EpisodicStoreStub(),
        },
    )

    detail = result["detail"]
    assert result["status"] == "OK"
    assert detail["search_scope"] == "long_term"
    assert detail["source"] == "semantic_recaller"
    assert detail["results"] == []


def test_memory_registry_exposes_steward_skills_with_low_risk() -> None:
    registry = SkillRegistry()
    registry.register_decorated(MemorySkillProvider())

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
