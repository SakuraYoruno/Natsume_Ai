from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from host_brain.persona.isolated_surface_continuity import ThreeLayerContinuityContext
from host_brain.persona.isolated_surface_harness import (
    build_isolated_surface_messages,
    memory_alignment_facts,
    memory_alignment_terms,
)
from host_brain.persona.surface_memory_recall import (
    MemoryRecallPacket,
    RECALL_POLICY_MAY,
    RECALL_POLICY_MUST,
    RECALL_POLICY_NO,
    SURFACE_MEMORY_EVIDENCE_REQUIRED,
    SURFACE_MEMORY_RECALL_SKIPPED,
    SURFACE_MEMORY_RECALL_VALIDATION_FAILED,
    SurfaceMemoryRecallPacket,
    build_surface_input_with_memory_recall,
    build_surface_memory_recall_packet,
    build_surface_memory_recall_query,
    build_surface_turn_memory_text,
    memory_recall_packet_from_mapping,
    plan_surface_memory_recall,
    recall_surface_memory_for_context,
    recall_surface_memory_packet,
    write_surface_turn_memory,
)


SOUL = "你是新海天。说话短一点，有轻微二次元 galgame 私聊感。"
CN_TZ = timezone(timedelta(hours=8))


def _ts(year: int, month: int, day: int, hour: int = 12) -> int:
    return int(datetime(year, month, day, hour, tzinfo=CN_TZ).timestamp() * 1000)


def test_surface_turn_memory_text_uses_identity_neutral_attribution() -> None:
    text = build_surface_turn_memory_text(
        user_message="上次那个问题我又卡住了。",
        assistant_reply="上次卡在输入输出，这轮先看那一块。",
    )

    assert "回复" in text
    assert "夏目蓝" not in text
    assert "新海天" not in text
    assert "新海天回应" not in text


def _recall_result() -> SimpleNamespace:
    return SimpleNamespace(
        output={
            "query": "还记得上次说的展吗",
            "status": "OK",
            "stage": "S_RET_RECALL",
            "degraded": False,
            "reason_code": None,
            "routes": [
                {"route_id": "episodic", "store_id": "episodic_vector"},
                {"route_id": "semantic", "store_id": "semantic_memory"},
                {"route_id": "bond", "store_id": "neo4j_bond"},
                {"route_id": "world", "store_id": "world_canon"},
            ],
            "route_rows": [],
            "route_errors": [],
            "candidate_count": 4,
            "candidates": [
                {
                    "doc_id": "mem-1",
                    "source_ref": "memory://episodic/mem-1",
                    "evidence_refs": "evd://episodic/mem-1",
                    "snippet": 'memory written: {"text":"上次说好周末去看展"} chat://session/s1',
                    "score": 0.93,
                    "confidence": 0.93,
                    "metadata": {"store_id": "episodic_vector"},
                },
                {
                    "doc_id": "sem-1",
                    "source_ref": "ledger://semantic_memory/sem-1",
                    "evidence_ref": "semantic://sem-1",
                    "snippet": "用户低能量时偏好少追问，先给一个小步骤。",
                    "score": 0.88,
                    "metadata": {"store_id": "semantic_memory"},
                },
                {
                    "doc_id": "bond-1",
                    "source_ref": "bond://nini",
                    "evidence_ref": "evd://bond/bond-1",
                    "snippet": "关系阶段：熟人私聊，别升级亲密。",
                    "score": 0.82,
                    "metadata": {"store_id": "neo4j_bond"},
                },
                {
                    "doc_id": "world-1",
                    "source_ref": "canon://world/1",
                    "evidence_ref": "evd://world/world-1",
                    "snippet": "新海天这个名字是身份锚点。",
                    "score": 0.74,
                    "metadata": {"store_id": "world_canon"},
                },
                {
                    "doc_id": "mem-2",
                    "source_ref": "memory://episodic/mem-2",
                    "evidence_ref": "evd://episodic/mem-2",
                    "snippet": "第二条同路由记忆应当在多路优先后再补位。",
                    "score": 0.71,
                    "metadata": {"store_id": "episodic_vector"},
                },
            ],
        }
    )


def test_surface_memory_recall_packet_compresses_multi_route_candidates() -> None:
    packet = build_surface_memory_recall_packet(_recall_result(), max_facts=3)

    assert isinstance(packet, SurfaceMemoryRecallPacket)
    assert packet.facts == (
        "记忆线索：上次说好周末去看展",
        "长期记忆：用户低能量时偏好少追问，先给一个小步骤。",
    )
    assert packet.route_ids == ("episodic", "semantic", "bond")
    assert packet.evidence_refs == (
        "evd://episodic/mem-1",
        "semantic://sem-1",
        "evd://bond/bond-1",
    )
    assert packet.relationship_clues == ("关系线索：关系阶段：熟人私聊，别升级亲密。",)
    assert packet.confidence == 0.93
    assert "不要根据记忆升级亲密关系" in packet.must_not_do
    assert "用户事实记忆只补当前承接，不扩写成未确认细节" in packet.style_anchor
    assert "长期记忆只补稳定偏好和关系事实，不替代当前用户句子" in packet.style_anchor
    assert "按关系线索控制距离，不因为记忆升格亲密" in packet.style_anchor
    assert all("世界事实" not in fact for fact in packet.facts)
    assert "chat://session" not in "\n".join(packet.facts)
    assert "{" not in "\n".join(packet.facts)


def test_surface_memory_recall_normalizes_legacy_assistant_attribution() -> None:
    result = SimpleNamespace(
        output={
            "query": "还记得上次卡在哪里吗",
            "status": "OK",
            "stage": "S_RET_RECALL",
            "degraded": False,
            "routes": [{"route_id": "episodic", "store_id": "episodic_vector"}],
            "route_rows": [],
            "route_errors": [],
            "candidate_count": 1,
            "candidates": [
                {
                    "doc_id": "legacy-name",
                    "snippet": "当前回合摘要：用户提到「上次那个问题我又卡住了。」；新海天回应「先看输入输出」。",
                    "score": 0.93,
                    "metadata": {"store_id": "episodic_vector"},
                }
            ],
        }
    )

    packet = build_surface_memory_recall_packet(result)

    rendered = "\n".join(packet.facts)
    assert "回复" in rendered
    assert "夏目蓝回应" not in rendered
    assert "新海天回应" not in rendered


def test_surface_memory_recall_prefers_high_score_life_episode_over_legacy_noise() -> None:
    packet = build_surface_memory_recall_packet(
        SimpleNamespace(
            output={
                "query": "刚才今日推荐的结果是什么",
                "status": "OK",
                "candidate_count": 2,
                "candidates": [
                    {
                        "snippet": "当前回合摘要：用户提到「最近有什么好书推荐？」；回复「先说喜欢什么类型」。",
                        "confidence": 0.64,
                        "metadata": {"store_id": "episodic_vector"},
                    },
                    {
                        "snippet": "User: 调用今日推荐：从每日榜单抓封面和简介推荐轻小说\nAssistant: 已创建轻小说推荐任务。",
                        "confidence": 1.0,
                        "metadata": {"store_id": "life_episode"},
                    },
                ],
            }
        ),
        max_facts=2,
    )

    assert packet.facts[0].startswith("记忆线索：用户提到： 调用今日推荐")
    assert "已创建轻小说推荐任务" in packet.facts[0]


def test_surface_memory_recall_prioritizes_terminal_codex_production_result() -> None:
    packet = build_surface_memory_recall_packet(
        SimpleNamespace(
            output={
                "query": "现在今日推荐跑完了吗？如果失败就说失败原因。",
                "status": "OK",
                "candidate_count": 2,
                "candidates": [
                    {
                        "snippet": "User: 现在今日推荐跑完了吗？\nAssistant: 还没拿到完成结果。",
                        "confidence": 1.0,
                        "metadata": {"store_id": "life_episode"},
                    },
                    {
                        "snippet": "codex_production codex_lightnovel_recommender FAILED LIGHTNOVEL_NATIVE_NO_RECOMMENDATIONS；推荐候选：0",
                        "confidence": 1.0,
                        "metadata": {"store_id": "life_episode"},
                    },
                ],
            }
        ),
        max_facts=2,
    )

    assert "LIGHTNOVEL_NATIVE_NO_RECOMMENDATIONS" in packet.facts[0]


def test_surface_memory_recall_anaphoric_selection_uses_life_episode_not_self_echo() -> None:
    packet = build_surface_memory_recall_packet(
        SimpleNamespace(
            output={
                "query": "我问你喜欢哪本",
                "status": "OK",
                "candidate_count": 2,
                "candidates": [
                    {
                        "snippet": "当前回合摘要：用户提到「我问你喜欢哪本」；回复「哈？最近在看一本讲宇宙和人的，挺有意思的。」。",
                        "confidence": 0.99,
                        "metadata": {"store_id": "episodic_vector"},
                    },
                    {
                        "snippet": "scheduler emotion_decay_tick OK 后台任务记忆：emotion_decay_tick 状态=OK。",
                        "confidence": 0.99,
                        "metadata": {
                            "store_id": "life_episode",
                            "episode_kind": "background_episode",
                        },
                    },
                    {
                        "snippet": (
                            "codex_production lightnovel_recommendation_delivery COMPLETED；"
                            "我刚才通过 qq 给 qq_dm_1 推荐了 3 本轻小说："
                            "1. [丸焦ししゃも]给被人说“培养失败”的前美少女童星（SSR）搭了把手之后；"
                            "2. 1205.与神在河岸；"
                            "3. [軽井広]轉移到異世界後，與美少女皇女結婚的我成為皇帝。"
                        ),
                        "confidence": 0.92,
                        "metadata": {
                            "store_id": "life_episode",
                            "episode_kind": "action_episode",
                        },
                    },
                    {
                        "snippet": "_thalamus_meta: compiled_by: thalamus_agent, action: narrative_only, args: 我问你喜欢哪本",
                        "confidence": 1.0,
                        "metadata": {"store_id": "sqlite_ledger"},
                    },
                    {
                        "snippet": "User: 我问你喜欢哪本\nAssistant: 难得难得。那三本里我最中意第二本，与神在河岸。",
                        "confidence": 1.0,
                        "metadata": {
                            "store_id": "life_episode",
                            "episode_kind": "chat_episode",
                        },
                    },
                ],
            }
        ),
        query="我问你喜欢哪本",
        max_facts=3,
        max_chars_per_fact=320,
    )

    joined = "\n".join(packet.facts)
    assert packet.facts[0].startswith("记忆线索：我刚才通过 qq")
    assert "1205.与神在河岸" in joined
    assert "軽井広" in joined
    assert "最近在看一本讲宇宙" not in joined
    assert "难得难得" not in joined
    assert "_thalamus_meta" not in joined
    assert "emotion_decay_tick" not in joined
    assert any("指代选择追问" in item for item in packet.style_anchor)
    assert any("指代选择追问" in item for item in packet.must_not_do)


def test_surface_memory_recall_preserves_structured_recommendation_evidence_for_comparison() -> None:
    packet = build_surface_memory_recall_packet(
        SimpleNamespace(
            output={
                "query": "这三本书你喜欢哪本",
                "status": "OK",
                "candidate_count": 1,
                "candidates": [
                    {
                        "snippet": (
                            "codex_production lightnovel_recommendation_delivery COMPLETED；"
                            "我刚才通过 qq 推荐了 3 本轻小说：1. A；2. 与神在河岸；3. C。"
                        ),
                        "confidence": 0.95,
                        "metadata": {
                            "store_id": "life_episode",
                            "episode_kind": "action_episode",
                            "episode_metadata": {
                                "selected_recommendations": [
                                    {
                                        "title": "A",
                                        "selection_reason": "资料完整度高",
                                        "intro_snippet": "偏恋爱日常",
                                        "update_time": "1 小时前",
                                        "comment_count": "1",
                                    },
                                    {
                                        "title": "与神在河岸",
                                        "selection_reason": "公开信号较好、资料完整度高",
                                        "intro_snippet": "奇幻身份钩子，设定新鲜",
                                        "update_time": "1 小时前",
                                        "view_count": "79",
                                    },
                                    {
                                        "title": "C",
                                        "selection_reason": "设定新鲜",
                                        "intro_snippet": "转生皇帝路线",
                                    },
                                ],
                            },
                        },
                    }
                ],
            }
        ),
        query="这三本书你喜欢哪本",
        max_chars_per_fact=700,
    )

    joined = "\n".join(packet.facts)
    assert "可比较证据" in joined
    assert "看点：公开信号较好" in joined
    assert "简介：奇幻身份钩子" in joined
    assert "更新：1 小时前" in joined
    assert "热度：79浏览" in joined
    assert any("不要只看标题" in item for item in packet.style_anchor)
    assert any("不要只按标题或名字判断" in item for item in packet.must_not_do)


def test_memory_recall_packet_is_safe_boundary_shape_without_rows() -> None:
    packet = memory_recall_packet_from_mapping(
        {
            "query": "还记得上次说的展吗",
            "facts": ["记忆线索：上次说好周末看展"],
            "relationship_clues": ["关系线索：熟人私聊，不升级亲密。"],
            "style_anchor": ["承接压缩记忆"],
            "must_not_do": ["不要暴露召回来源"],
            "confidence": 0.82,
            "evidence_refs": ["evd://episodic/mem-1"],
            "source_refs": ["memory://episodic/mem-1"],
            "route_ids": ["episodic"],
            "candidate_count": 1,
        }
    )

    assert isinstance(packet, MemoryRecallPacket)
    assert packet.facts == ("记忆线索：上次说好周末看展",)
    assert packet.relationship_clues == ("关系线索：熟人私聊，不升级亲密。",)
    payload = packet.as_dict()
    assert "rows" not in payload
    assert payload["confidence"] == 0.82


def test_unprefixed_packet_facts_are_tagged_for_surface_alignment() -> None:
    packet = memory_recall_packet_from_mapping(
        {
            "facts": ["用户周末想去看展"],
            "relationship_clues": ["熟人私聊，不升级亲密。"],
        }
    )
    request = build_surface_input_with_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="你还记得我周末想去看展吗",
            task_mode_hint="recall_memory",
            question_budget=0,
        ),
        packet=packet,
        soul_md=SOUL,
        case_id="unprefixed_memory_surface",
        max_facts=8,
    )
    rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(request))

    assert memory_alignment_facts(request) == ["用户周末想去看展"]
    assert "看展" in memory_alignment_terms(request)
    assert "用户周末想去看展" in rendered
    assert "记忆线索：用户周末想去看展" not in rendered
    assert "关系线索：熟人私聊，不升级亲密。" not in rendered


def test_recall_surface_memory_packet_calls_recall_and_surface_gets_only_facts() -> None:
    captured: dict[str, object] = {}

    class _RecallService:
        def recall(self, **kwargs):  # noqa: ANN003
            captured.update(kwargs)
            result = _recall_result()
            allowed = set(kwargs.get("target_store_ids") or [])
            result.output["candidates"] = [
                item
                for item in result.output["candidates"]
                if item.get("metadata", {}).get("store_id") in allowed
            ]
            result.output["routes"] = [
                item
                for item in result.output["routes"]
                if item.get("store_id") in allowed
            ]
            return result

    packet = recall_surface_memory_packet(
        recall_service=_RecallService(),
        request_id="req-memory",
        query="还记得上次说的展吗",
        query_embedding=[0.1] * 16,
        person_id="p1",
        session_id="s1",
        target_store_ids=["episodic_vector", "neo4j_bond"],
        max_facts=2,
    )
    context = ThreeLayerContinuityContext(
        current_user_message="还记得上次说的展吗",
        mid_term_summary="尼尼和新海天在验证记忆召回压缩。",
        task_mode_hint="recall_memory",
        question_budget=0,
    )
    request = build_surface_input_with_memory_recall(
        context,
        packet=packet,
        soul_md=SOUL,
        case_id="memory_surface",
        max_facts=8,
    )
    rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(request))

    assert captured["request_id"] == "req-memory"
    assert captured["target_store_ids"] == ["episodic_vector", "neo4j_bond"]
    assert "记忆线索：上次说好周末去看展" not in rendered
    assert "关系线索：关系阶段：熟人私聊，别升级亲密。" not in rendered
    assert "上次说好周末去看展" in rendered
    assert "关系阶段：熟人私聊，别升级亲密。" not in rendered
    assert "按关系线索控制距离，不因为记忆升格亲密" in rendered
    assert "memory://episodic/mem-1" not in rendered
    assert "metadata" not in rendered


def test_plan_surface_memory_recall_skips_low_signal_phatic_turns() -> None:
    context = ThreeLayerContinuityContext(
        current_user_message="早啊",
        recent_turns=[
            {"role": "user", "content": "RAW_RECENT_SHOULD_NOT_JOIN_QUERY"},
        ],
        mid_term_summary="压缩摘要可以参与召回规划。",
        task_mode_hint="daily_good_morning",
    )

    plan = plan_surface_memory_recall(context)

    assert not plan.should_recall
    assert plan.recall_policy == RECALL_POLICY_NO
    assert plan.reason == "low_signal_surface_turn"
    assert plan.target_store_ids == ()
    assert "RAW_RECENT_SHOULD_NOT_JOIN_QUERY" not in plan.query


def test_plan_surface_memory_recall_routes_memory_followup_to_core_memory_stores() -> None:
    context = ThreeLayerContinuityContext(
        current_user_message="还记得上次说的展吗",
        recent_turns=[
            {"role": "assistant", "content": "RAW_RECENT_EXACT_REPLY"},
        ],
        mid_term_summary="尼尼和新海天在聊周末安排。",
        task_mode_hint="daily_chat",
        necessary_facts=["必要事实：用户说的上次是压缩后的记忆追问。"],
    )

    plan = plan_surface_memory_recall(context)
    query = build_surface_memory_recall_query(context)

    assert plan.should_recall
    assert plan.recall_policy == RECALL_POLICY_MUST
    assert plan.reason == "explicit_memory_recall"
    assert plan.target_store_ids == ("life_episode", "life_memory_entity", "semantic_memory", "episodic_vector", "sqlite_ledger", "neo4j_bond")
    assert plan.max_facts == 4
    assert plan.time_lookback_days == 7
    assert "必要事实：用户说的上次是压缩后的记忆追问。" in query
    assert "RAW_RECENT_EXACT_REPLY" not in query


def test_plan_surface_memory_recall_treats_background_result_followup_as_recall() -> None:
    plan = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="现在今日推荐跑完了吗？如果失败就说失败原因。",
            task_mode_hint="daily_chat",
        )
    )

    assert plan.should_recall
    assert plan.recall_policy == RECALL_POLICY_MUST
    assert plan.reason == "failure_debug_recall"
    assert plan.query_kind == "failure_debug"
    assert plan.target_store_ids[0] == "life_episode"


def test_plan_surface_memory_recall_treats_anaphoric_selection_as_recall() -> None:
    plan = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="我问你喜欢哪本",
            task_mode_hint="first_ask",
        )
    )

    assert plan.should_recall
    assert plan.recall_policy == RECALL_POLICY_MUST
    assert plan.reason == "anaphoric_selection_recall"
    assert plan.target_store_ids[0] == "life_memory_collection"
    assert plan.target_store_ids[1] == "life_memory_entity"
    assert "life_episode" in plan.target_store_ids
    assert plan.time_lookback_days == 1


def test_plan_surface_memory_recall_skips_memory_write_current_turn() -> None:
    plan = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="记一下，我喜欢芒果，不喜欢太甜的奶茶。",
            task_mode_hint="daily_chat",
        )
    )

    assert not plan.should_recall
    assert plan.recall_policy == RECALL_POLICY_NO
    assert plan.reason == "memory_write_current_turn_only"


def test_plan_surface_memory_recall_skips_generic_daily_turn_without_hint() -> None:
    plan = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="今天先随便聊两句吧。",
            task_mode_hint="plain_answer",
        )
    )

    assert not plan.should_recall
    assert plan.recall_policy == RECALL_POLICY_NO
    assert plan.reason == "no_memory_recall_needed"
    assert plan.target_store_ids == ()


def test_explicit_memory_content_query_prefers_remembered_user_facts() -> None:
    packet = build_surface_memory_recall_packet(
        SimpleNamespace(
            output={
                "query": "我刚才让你记了什么？",
                "status": "OK",
                "candidate_count": 2,
                "candidates": [
                    {
                        "snippet": "当前回合摘要：用户提到「昨晚没睡好，今天脑子有点糊。」",
                        "confidence": 0.94,
                        "metadata": {"store_id": "episodic_vector"},
                    },
                    {
                        "snippet": "当前回合摘要：用户提到「记一下，我喜欢芒果，不喜欢太甜的奶茶。」",
                        "confidence": 0.91,
                        "metadata": {"store_id": "episodic_vector"},
                    },
                ],
            }
        ),
        query="我刚才让你记了什么？",
        max_facts=4,
    )

    joined = "\n".join(packet.facts)
    assert "芒果" in joined
    assert "奶茶" in joined
    assert "昨晚没睡好" not in joined


def test_plan_surface_memory_recall_routes_task_modes_for_current_main_chain() -> None:
    tech = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="下一步怎么处理这个报错",
            task_mode_hint="next_step",
        )
    )
    identity = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="你叫什么来着",
            task_mode_hint="identity",
        )
    )
    tired = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="我有点累",
            task_mode_hint="tired",
        )
    )

    assert tech.target_store_ids == ("life_episode", "semantic_memory", "episodic_vector", "sqlite_ledger")
    assert tech.recall_policy == RECALL_POLICY_MAY
    assert identity.target_store_ids == ("life_episode", "neo4j_bond", "semantic_memory", "episodic_vector")
    assert identity.recall_policy == RECALL_POLICY_MAY
    assert tired.target_store_ids == ("life_episode", "neo4j_bond", "semantic_memory", "episodic_vector")
    assert tired.recall_policy == RECALL_POLICY_MAY


def test_plan_surface_memory_recall_routes_dated_event_lookup_to_life_episode() -> None:
    plan = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="昨天你做了什么？",
            task_mode_hint="daily_chat",
        )
    )

    assert plan.should_recall
    assert plan.recall_policy == RECALL_POLICY_MUST
    assert plan.reason == "event_memory_recall"
    assert plan.query_kind == "date_lookup"
    assert plan.temporal_scope == "yesterday"
    assert plan.target_store_ids[:2] == ("life_episode", "life_memory_collection")
    assert plan.time_lookback_days == 2
    assert plan.validation_requirements == ("event_time_scope_match", "event_or_collection_hit", "source_ref_present")


def test_preference_rule_recall_does_not_leak_same_domain_collection_rows() -> None:
    class _RecallService:
        def recall(self, **kwargs):  # noqa: ANN003
            return SimpleNamespace(
                output={
                    "query": kwargs["query"],
                    "status": "OK",
                    "stage": "S_RET_RECALL",
                    "degraded": False,
                    "routes": [
                        {"route_id": "surface_life_collection", "store_id": "life_memory_collection"},
                        {"route_id": "surface_life_episode", "store_id": "life_episode"},
                    ],
                    "candidate_count": 2,
                    "ts_ms": kwargs["ts_ms"],
                    "candidates": [
                        {
                            "doc_id": "col-today",
                            "source_ref": "life_memory_collection://col-today",
                            "evidence_refs": ["gateway://message/today/card/1"],
                            "text": "2026-05-28 当天轻小说推荐集合，记录 3 个条目；可比较证据：1. 5月28日推荐 A｜2. 5月28日推荐 B｜3. 5月28日推荐 C",
                            "score": 0.99,
                            "metadata": {
                                "store_id": "life_memory_collection",
                                "scope_key": "daily:2026-05-28",
                                "domain": "lightnovel_recommendation",
                                "item_count": 3,
                                "evidence_refs_primary": "gateway://message/today/card/1",
                            },
                        },
                        {
                            "doc_id": "ep-pref-format",
                            "source_ref": "chat://request/req-pref-format",
                            "evidence_refs": ["chat://request/req-pref-format#user"],
                            "text": "用户提到：以后推荐轻小说时，每本书的简介后应该换行，别模板腔。 回复：记住了，后面按更自然的卡片格式发。",
                            "score": 0.82,
                            "metadata": {
                                "store_id": "life_episode",
                                "episode_kind": "chat_episode",
                                "truth_state": "VERIFIED",
                                "evidence_refs_primary": "chat://request/req-pref-format#user",
                            },
                        },
                    ],
                }
            )

    context = ThreeLayerContinuityContext(
        current_user_message="还记得我说推荐格式要怎样吗？",
        task_mode_hint="daily_chat",
    )
    plan = plan_surface_memory_recall(context)
    packet = recall_surface_memory_for_context(
        recall_service=_RecallService(),
        context=context,
        request_id="req-pref-rule-recall",
        query_embedding=[0.1] * 16,
        person_id="owner",
        session_id="qq_dm_owner",
        ts_ms=_ts(2026, 5, 28, 18),
    )

    joined = "\n".join(packet.facts)
    validation = dict(dict(packet.trace or {})["validation"])
    receipt = dict(dict(packet.trace or {})["recall_receipt"])
    assert plan.query_kind == "preference_lookup"
    assert plan.recall_contract_id == "preference"
    assert "state_preference_as_user_rule" in plan.answer_obligations
    assert "简介后应该换行" in joined
    assert "5月28日推荐" not in joined
    assert validation["passed"] is True
    assert validation["selected_store_counts"] == {"life_episode": 1}
    assert receipt["meets_primary_layer"] is False
    assert receipt["meets_expected_layer"] is True


def test_recall_surface_memory_for_context_uses_plan_and_skips_without_calling_recall() -> None:
    class _RecallService:
        def recall(self, **kwargs):  # noqa: ANN003
            raise AssertionError("low signal turn should not call recall")

    packet = recall_surface_memory_for_context(
        recall_service=_RecallService(),
        context=ThreeLayerContinuityContext(
            current_user_message="嗯",
            task_mode_hint="short_ack",
        ),
        request_id="req-skip",
        query_embedding=None,
    )

    assert packet.facts == ()
    assert packet.reason_code == SURFACE_MEMORY_RECALL_SKIPPED
    assert dict(packet.trace or {})["skipped"] is True


def test_recall_surface_memory_for_context_passes_current_chain_plan_to_recall() -> None:
    captured: dict[str, object] = {}

    class _RecallService:
        def recall(self, **kwargs):  # noqa: ANN003
            captured.update(kwargs)
            return _recall_result()

    packet = recall_surface_memory_for_context(
        recall_service=_RecallService(),
        context=ThreeLayerContinuityContext(
            current_user_message="下一步怎么处理这个报错",
            task_mode_hint="next_step",
            necessary_facts=["必要事实：技术场景优先 usefulness。"],
        ),
        request_id="req-tech",
        query_embedding=[0.2] * 16,
    )

    assert captured["target_store_ids"] == ["life_episode", "semantic_memory", "episodic_vector", "sqlite_ledger"]
    assert "技术场景优先 usefulness" in str(captured["query"])
    assert len(packet.facts) <= 3
    assert dict(packet.trace or {})["plan"]["reason"] == "technical_user_memory_context"


def test_recall_surface_memory_for_context_attaches_validation_receipt_for_collection_scope() -> None:
    class _RecallService:
        def recall(self, **kwargs):  # noqa: ANN003
            return SimpleNamespace(
                output={
                    "query": kwargs["query"],
                    "status": "OK",
                    "stage": "S_RET_RECALL",
                    "degraded": False,
                    "routes": [{"route_id": "surface_life_collection", "store_id": "life_memory_collection"}],
                    "route_rows": [],
                    "route_errors": [],
                    "candidate_count": 1,
                    "ts_ms": kwargs["ts_ms"],
                    "candidates": [
                        {
                            "doc_id": "col-yesterday",
                            "source_ref": "life_memory_collection://col-yesterday",
                            "evidence_refs": ["gateway://message/yesterday/card/1"],
                            "text": "2026-05-27 当天轻小说推荐集合；可比较证据：1. Yesterday A｜2. Yesterday B｜3. Yesterday C",
                            "score": 0.91,
                            "metadata": {
                                "store_id": "life_memory_collection",
                                "scope_key": "daily:2026-05-27",
                                "item_count": 3,
                                "evidence_refs_primary": "gateway://message/yesterday/card/1",
                            },
                        }
                    ],
                }
            )

    packet = recall_surface_memory_for_context(
        recall_service=_RecallService(),
        context=ThreeLayerContinuityContext(
            current_user_message="昨天三本呢？",
            task_mode_hint="daily_chat",
        ),
        request_id="req-yesterday-validation",
        query_embedding=[0.1] * 16,
        person_id="owner",
        session_id="qq_dm_owner",
        ts_ms=_ts(2026, 5, 28, 18),
    )

    validation = dict(dict(packet.trace or {})["validation"])
    receipt = dict(dict(packet.trace or {})["recall_receipt"])
    need = dict(dict(packet.trace or {})["recall_need"])
    coverage = dict(dict(packet.trace or {})["recall_coverage"])
    assert validation["passed"] is True
    assert validation["missing"] == []
    assert {item["name"] for item in validation["checks"]} == {
        "scope_match",
        "collection_items_present",
        "evidence_refs_present",
        "collection_item_min_count",
        "collection_item_exact_count",
    }
    assert validation["min_collection_items"] == 3
    assert validation["candidate_store_counts"] == {"life_memory_collection": 1}
    assert receipt["decision"] == "validated"
    assert receipt["validation_passed"] is True
    assert receipt["recall_contract_id"] == "collection_date"
    assert receipt["temporal_scope"] == "yesterday"
    assert receipt["expected_scope_key"] == "daily:2026-05-27"
    assert receipt["selected_scope_keys"] == ["daily:2026-05-27"]
    assert receipt["selected_item_count"] == 3
    assert receipt["coverage"]["collection_min_passed"] is True
    assert receipt["coverage"]["collection_exact_passed"] is True
    assert receipt["evidence_ref_count"] == 1
    assert receipt["meets_primary_layer"] is True
    assert receipt["meets_expected_layer"] is True
    assert need["schema"] == "Natsume.surface_recall_need.v1"
    assert need["need_type"] == "collection"
    assert need["recall_contract_id"] == "collection_date"
    assert need["primary_memory_layer"] == "life_memory_collection"
    assert need["min_collection_items"] == 3
    assert need["evidence_depth"] == "evidence_anchor"
    assert coverage["schema"] == "Natsume.surface_recall_coverage.v1"
    assert coverage["passed"] is True
    assert coverage["score"] == 1.0
    assert coverage["dimensions"]["item_count"] == 1.0
    assert coverage["selected_item_count"] == 3
    assert dict(packet.trace or {})["coverage_score"] == 1.0


def test_recall_surface_memory_validation_flags_wrong_date_collection_scope() -> None:
    class _RecallService:
        def recall(self, **kwargs):  # noqa: ANN003
            return SimpleNamespace(
                output={
                    "query": kwargs["query"],
                    "status": "OK",
                    "stage": "S_RET_RECALL",
                    "degraded": False,
                    "routes": [{"route_id": "surface_life_collection", "store_id": "life_memory_collection"}],
                    "route_rows": [],
                    "route_errors": [],
                    "candidate_count": 1,
                    "ts_ms": kwargs["ts_ms"],
                    "candidates": [
                        {
                            "doc_id": "col-today",
                            "source_ref": "life_memory_collection://col-today",
                            "evidence_refs": ["gateway://message/today/card/1"],
                            "text": "2026-05-28 当天轻小说推荐集合；可比较证据：1. Today A｜2. Today B｜3. Today C",
                            "score": 0.91,
                            "metadata": {
                                "store_id": "life_memory_collection",
                                "scope_key": "daily:2026-05-28",
                                "item_count": 3,
                                "evidence_refs_primary": "gateway://message/today/card/1",
                            },
                        }
                    ],
                }
            )

    packet = recall_surface_memory_for_context(
        recall_service=_RecallService(),
        context=ThreeLayerContinuityContext(
            current_user_message="昨天三本呢？",
            task_mode_hint="daily_chat",
        ),
        request_id="req-wrong-date-validation",
        query_embedding=[0.1] * 16,
        person_id="owner",
        session_id="qq_dm_owner",
        ts_ms=_ts(2026, 5, 28, 18),
    )

    validation = dict(dict(packet.trace or {})["validation"])
    receipt = dict(dict(packet.trace or {})["recall_receipt"])
    coverage = dict(dict(packet.trace or {})["recall_coverage"])
    assert validation["passed"] is False
    assert "scope_match" in validation["missing"]
    scope_check = next(item for item in validation["checks"] if item["name"] == "scope_match")
    assert "daily:2026-05-27" in scope_check["detail"]
    assert packet.facts == ()
    assert packet.evidence_refs == ()
    assert packet.confidence == 0.0
    assert packet.degraded is True
    assert packet.reason_code == SURFACE_MEMORY_RECALL_VALIDATION_FAILED
    assert any("召回校验失败" in item for item in packet.must_not_do)
    assert receipt["decision"] == "validation_failed"
    assert receipt["validation_passed"] is False
    assert receipt["expected_scope_key"] == "daily:2026-05-27"
    assert "scope_match" in receipt["missing"]
    assert receipt["selected_item_count"] == 0
    assert coverage["passed"] is False
    assert coverage["score"] < 0.66
    assert "scope_match" in coverage["missing"]


def test_collection_scope_query_filters_self_echo_when_many_books_requested() -> None:
    class _RecallService:
        def recall(self, **kwargs):  # noqa: ANN003
            return SimpleNamespace(
                output={
                    "query": kwargs["query"],
                    "status": "OK",
                    "stage": "S_RET_RECALL",
                    "degraded": False,
                    "routes": [
                        {"route_id": "surface_life_collection", "store_id": "life_memory_collection"},
                        {"route_id": "surface_life_episode", "store_id": "life_episode"},
                    ],
                    "candidate_count": 2,
                    "ts_ms": kwargs["ts_ms"],
                    "candidates": [
                        {
                            "doc_id": "col-recent",
                            "source_ref": "life_memory_collection://col-recent",
                            "evidence_refs": ["gateway://message/recent/card/1"],
                            "text": "最近轻小说推荐集合，记录 15 个条目；可比较证据：1. Recent A｜2. Recent B",
                            "score": 0.91,
                            "metadata": {
                                "store_id": "life_memory_collection",
                                "scope_key": "recent",
                                "item_count": 15,
                                "evidence_refs_primary": "gateway://message/recent/card/1",
                            },
                        },
                        {
                            "doc_id": "col-recent-small",
                            "source_ref": "life_memory_collection://col-recent-small",
                            "evidence_refs": ["gateway://message/old/card/1"],
                            "text": "最近轻小说推荐集合，记录 3 个条目；可比较证据：1. Old A｜2. Old B",
                            "score": 0.93,
                            "metadata": {
                                "store_id": "life_memory_collection",
                                "scope_key": "recent",
                                "item_count": 3,
                                "evidence_refs_primary": "gateway://message/old/card/1",
                            },
                        },
                        {
                            "doc_id": "ep-self-echo",
                            "source_ref": "chat://request/self-echo",
                            "evidence_refs": ["chat://request/self-echo"],
                            "text": "用户提到：这三本书，你喜欢哪本？ 回复：那三本里我会选第二本。",
                            "score": 0.89,
                            "metadata": {"store_id": "life_episode"},
                        },
                    ],
                }
            )

    packet = recall_surface_memory_for_context(
        recall_service=_RecallService(),
        context=ThreeLayerContinuityContext(
            current_user_message="这十几本里有哪些？",
            task_mode_hint="daily_chat",
        ),
        request_id="req-many-book-listing",
        query_embedding=[0.1] * 16,
        person_id="owner",
        session_id="qq_dm_owner",
        ts_ms=_ts(2026, 5, 29, 8),
    )

    joined = "\n".join(packet.facts)
    receipt = dict(dict(packet.trace or {})["recall_receipt"])
    assert packet.reason_code is None
    assert "最近轻小说推荐集合，记录 15 个条目" in joined
    assert "记录 3 个条目" not in joined
    assert "用户提到" not in joined
    assert receipt["decision"] == "validated"
    assert receipt["selected_item_count"] == 15
    assert receipt["coverage"]["min_collection_items"] == 10
    assert receipt["coverage"]["collection_min_passed"] is True
    assert receipt["selected_scope_keys"] == ["recent"]


def test_many_book_collection_recall_fails_receipt_when_item_coverage_is_too_small() -> None:
    class _RecallService:
        def recall(self, **kwargs):  # noqa: ANN003
            return SimpleNamespace(
                output={
                    "query": kwargs["query"],
                    "status": "OK",
                    "stage": "S_RET_RECALL",
                    "degraded": False,
                    "routes": [{"route_id": "surface_life_collection", "store_id": "life_memory_collection"}],
                    "candidate_count": 1,
                    "ts_ms": kwargs["ts_ms"],
                    "candidates": [
                        {
                            "doc_id": "col-recent-small",
                            "source_ref": "life_memory_collection://col-recent-small",
                            "evidence_refs": ["gateway://message/recent/card/1"],
                            "text": "最近轻小说推荐集合，记录 3 个条目；可比较证据：1. Old A｜2. Old B｜3. Old C",
                            "score": 0.95,
                            "metadata": {
                                "store_id": "life_memory_collection",
                                "scope_key": "recent",
                                "item_count": 3,
                                "evidence_refs_primary": "gateway://message/recent/card/1",
                            },
                        }
                    ],
                }
            )

    packet = recall_surface_memory_for_context(
        recall_service=_RecallService(),
        context=ThreeLayerContinuityContext(
            current_user_message="这十几本里有哪些？",
            task_mode_hint="daily_chat",
        ),
        request_id="req-many-book-small-coverage",
        query_embedding=[0.1] * 16,
        person_id="owner",
        session_id="qq_dm_owner",
        ts_ms=_ts(2026, 5, 29, 8),
    )

    validation = dict(dict(packet.trace or {})["validation"])
    receipt = dict(dict(packet.trace or {})["recall_receipt"])
    assert validation["passed"] is False
    assert validation["min_collection_items"] == 10
    assert "collection_item_min_count" in validation["missing"]
    assert packet.facts == ()
    assert packet.reason_code == SURFACE_MEMORY_RECALL_VALIDATION_FAILED
    assert receipt["decision"] == "validation_failed"
    assert receipt["selected_item_count"] == 3
    assert receipt["coverage"]["collection_min_passed"] is False


def test_write_surface_turn_memory_writes_compact_evidence_gated_record() -> None:
    captured: dict[str, object] = {}

    class _Store:
        def write_memory(self, **kwargs):  # noqa: ANN003
            captured.update(kwargs)
            return SimpleNamespace(success=True, reason_code=None, detail="memory written", memory_id="mem-1")

    result = write_surface_turn_memory(
        episodic_store=_Store(),
        request_id="req-1",
        session_id="sess-1",
        person_id="person-1",
        user_message="我今天有点累，但还想继续调文本质量",
        assistant_reply="先别硬撑，我们只看下一处问题。",
        ts_ms=123,
        intent_id="intent-1",
    )

    assert result.success
    assert result.memory_id == "mem-1"
    assert captured["evidence_refs"] == [
        "chat://request/req-1",
        "chat://session/sess-1",
        "chat://person/person-1",
    ]
    assert "当前回合摘要" in str(captured["text"])
    assert "调文本质量" in str(captured["text"])
    assert captured["decay_profile_id"] == "SLOW"
    assert captured["visibility"] == "internal"


def test_write_surface_turn_memory_requires_evidence_refs() -> None:
    class _Store:
        def write_memory(self, **kwargs):  # noqa: ANN003
            raise AssertionError("should not be called")

    result = write_surface_turn_memory(
        episodic_store=_Store(),
        request_id="",
        session_id="",
        person_id="",
        user_message="早",
        assistant_reply="早啊。",
    )

    assert not result.success
    assert result.reason_code == SURFACE_MEMORY_EVIDENCE_REQUIRED
