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
    SURFACE_MEMORY_RECALL_COVERAGE_FAILED,
    SURFACE_MEMORY_RECALL_SKIPPED,
    SURFACE_MEMORY_RECALL_VALIDATION_FAILED,
    SurfaceMemoryRecallPacket,
    build_surface_input_with_memory_recall,
    build_surface_memory_recall_decision_trace,
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


def test_correction_query_packet_prefers_correction_fact_over_echo() -> None:
    packet = build_surface_memory_recall_packet(
        {
            "query": "你刚才是不是记错了封面猫那本？",
            "status": "OK",
            "stage": "S_RET_RECALL",
            "degraded": False,
            "candidate_count": 3,
            "candidates": [
                {
                    "doc_id": "echo-bad",
                    "source_ref": "memory://episodic/echo-bad",
                    "evidence_refs": "evd://episodic/echo-bad",
                    "snippet": "当前回合摘要：用户提到「你刚才是不是记错了封面猫那本？」；回复「哈？那本封面确实有只猫。」",
                    "score": 0.95,
                    "metadata": {"store_id": "episodic_vector"},
                },
                {
                    "doc_id": "fix",
                    "source_ref": "life_episode://fix",
                    "evidence_refs": "chat://request/fix-cat-cover#user",
                    "snippet": "User: 不对，没有封面有猫这本。\nAssistant: 你说得对，我撤回这个判断。",
                    "score": 0.80,
                    "metadata": {"store_id": "life_episode"},
                },
            ],
        },
        query="你刚才是不是记错了封面猫那本？",
        max_facts=3,
        max_chars_per_fact=240,
        validation_requirements=("correction_state_checked", "suppressed_not_fact"),
        query_kind="correction",
        recall_contract_id="correction",
        primary_memory_layer="life_episode",
        expected_memory_layers=("life_episode", "semantic_memory", "sqlite_ledger", "episodic_vector"),
    )

    joined = "\n".join(packet.facts)
    assert "没有封面有猫" in joined
    assert "确实有只猫" not in joined


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
        "长期事实：用户低能量时偏好少追问，先给一个小步骤。",
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
    assert "长期事实只补稳定偏好和关系事实，不替代当前用户句子" in packet.style_anchor
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


def test_relationship_feedback_lookup_prefers_matching_subtopic_life_episode() -> None:
    packet = build_surface_memory_recall_packet(
        SimpleNamespace(
            output={
                "query": "我之前说文本质量不像人话，是哪里的问题？",
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "routes": [
                    {"route_id": "life", "store_id": "life_episode"},
                    {"route_id": "semantic", "store_id": "semantic_memory"},
                ],
                "candidate_count": 2,
                "candidates": [
                    {
                        "doc_id": "sem-recall-quality",
                        "source_ref": "ledger://semantic_memory/sem-recall-quality",
                        "evidence_ref": "semantic://sem-recall-quality",
                        "snippet": "关系反馈：用户强烈反馈：记忆召回和判断必须回到证据，不能把没核实的内容说成事实或胡说八道。",
                        "score": 0.98,
                        "metadata": {
                            "store_id": "semantic_memory",
                            "tags": ["semantic_kind:relationship_feedback", "relationship_feedback"],
                        },
                    },
                    {
                        "doc_id": "ep-natural-chat",
                        "source_ref": "life_episode://ep-natural-chat",
                        "evidence_ref": "chat://request/feedback-natural#user",
                        "snippet": "User: 文本质量不像人话，太台词腔了。\nAssistant: 收到，我以后把证据转成更自然的聊天方式。",
                        "score": 0.72,
                        "metadata": {"store_id": "life_episode"},
                    },
                    {
                        "doc_id": "echo-old-lookup",
                        "source_ref": "memory://episodic/echo-old-lookup",
                        "evidence_ref": "evd://episodic/echo-old-lookup",
                        "snippet": "当前回合摘要：用户提到「我之前说文本质量不像人话，是哪里的问题？」；回复「哈？你发一段我帮你看看。」。",
                        "score": 0.90,
                        "metadata": {"store_id": "episodic_vector"},
                    },
                    {
                        "doc_id": "pref-format-template",
                        "source_ref": "life_episode://pref-format-template",
                        "evidence_ref": "chat://request/pref-format-template#user",
                        "snippet": "User: 以后推荐轻小说时，每本书的简介后应该换行，别模板腔。\nAssistant: 记住了，后面按自然的卡片格式发。",
                        "score": 0.89,
                        "metadata": {"store_id": "life_episode"},
                    },
                ],
            }
        ),
        query="我之前说文本质量不像人话，是哪里的问题？",
        query_kind="preference_lookup",
        recall_contract_id="preference",
        primary_memory_layer="semantic_memory",
        target_domains=("preference",),
        expected_memory_layers=("semantic_memory", "life_episode"),
    )

    joined = "\n".join(packet.facts)
    assert "回答要像正常聊天" in joined
    assert "胡说八道" not in joined
    assert "你发一段我帮你看看" not in joined
    assert "简介后应该换行" not in joined


def test_preference_lookup_recommendation_format_excludes_llm_routing_and_relationship_feedback() -> None:
    packet = build_surface_memory_recall_packet(
        SimpleNamespace(
            output={
                "query": "还记得我说推荐格式要怎样吗？",
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "routes": [{"route_id": "semantic", "store_id": "semantic_memory"}],
                "candidate_count": 3,
                "candidates": [
                    {
                        "doc_id": "sem-format",
                        "source_ref": "ledger://semantic_memory/sem-format",
                        "evidence_ref": "chat://request/format#user",
                        "snippet": "用户偏好：以后推荐轻小说时，不要拟人化发送；前面的总览完整一段，后面各个卡片各一段；每本简介后应该换行",
                        "score": 0.80,
                        "metadata": {"store_id": "semantic_memory", "tags": ["semantic_kind:user_preference"]},
                    },
                    {
                        "doc_id": "sem-relationship",
                        "source_ref": "ledger://semantic_memory/sem-relationship",
                        "evidence_ref": "chat://request/relationship#user",
                        "snippet": "关系反馈：用户强烈反馈：记忆召回和判断必须回到证据，不能把没核实的内容说成事实或胡说八道。",
                        "score": 0.99,
                        "metadata": {"store_id": "semantic_memory", "tags": ["semantic_kind:relationship_feedback"]},
                    },
                    {
                        "doc_id": "sem-routing",
                        "source_ref": "ledger://semantic_memory/sem-routing",
                        "evidence_ref": "chat://request/routing#user",
                        "snippet": "用户偏好：Thalamus LLM 用模型池，不要用 Codex；模型池当前优先使用 DeepSeek，DeepSeek 不可用再用 Gemini；不要用 Pro",
                        "score": 0.98,
                        "metadata": {"store_id": "semantic_memory", "tags": ["semantic_kind:user_preference"]},
                    },
                ],
            }
        ),
        query="还记得我说推荐格式要怎样吗？",
        max_facts=4,
        max_chars_per_fact=240,
        temporal_scope="unspecified",
        query_kind="preference_lookup",
        recall_contract_id="preference",
        primary_memory_layer="semantic_memory",
        target_domains=("preference",),
        expected_memory_layers=("semantic_memory", "life_episode", "sqlite_ledger", "episodic_vector"),
        validation_requirements=("trusted_memory_hit", "source_ref_present"),
        answer_obligations=("state_preference_as_user_rule", "keep_source_episode_traceable"),
    )

    joined = "\n".join(packet.facts)
    assert "简介后应该换行" in joined
    assert "DeepSeek" not in joined
    assert "胡说八道" not in joined
    assert packet.evidence_refs == ("chat://request/format#user",)


def test_action_pattern_semantic_memory_is_humanized_for_surface_packet() -> None:
    packet = build_surface_memory_recall_packet(
        SimpleNamespace(
            output={
                "query": "你之前会回去翻正文再判断哪本轻小说好看吗？",
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "routes": [{"route_id": "semantic", "store_id": "semantic_memory"}],
                "candidate_count": 1,
                "candidates": [
                    {
                        "doc_id": "sem-action-pattern",
                        "source_ref": "ledger://semantic_memory/sem-action-pattern",
                        "evidence_ref": "action://event/deep-review-1",
                        "snippet": "主动行动模式：在轻小说推荐追问里，她会回去打开详情页或正文再比较哪本更值得看；已记录 5 次深读/回看行动，其中5 次拿到可用详情，5 次读到正文级证据。判断时主要看：开篇是不是已经进入故事、对白和人物互动。",
                        "score": 0.92,
                        "metadata": {"store_id": "semantic_memory", "tags": ["semantic_kind:action_pattern", "action_pattern"]},
                    }
                ],
            }
        ),
        query="你之前会回去翻正文再判断哪本轻小说好看吗？",
        max_facts=2,
        max_chars_per_fact=320,
        query_kind="event_lookup",
        recall_contract_id="event_lookup",
        primary_memory_layer="semantic_memory",
        expected_memory_layers=("semantic_memory", "life_episode"),
    )

    joined = "\n".join(packet.facts)
    assert "我会回去打开详情页或正文" in joined
    assert "实际回看记录" in joined
    assert "已记录 5 次" not in joined
    assert "正文级证据" not in joined
    assert "她会" not in joined


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


def test_recall_decision_trace_exposes_granularity_for_collection_selection() -> None:
    plan = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="这三本书，你喜欢哪本？",
            task_mode_hint="daily_chat",
        )
    )

    decision = build_surface_memory_recall_decision_trace(plan, invoked=True)

    assert decision["need_type"] == "collection_selection"
    assert decision["recall_grain"] == "collection_selection"
    assert decision["retrieval_unit"] == "life_memory_collection.items"
    assert decision["answer_scope"] == "compare_recalled_items_with_detail_gate"
    assert "detail_or_body_evidence_before_quality_judgment" in decision["verification_gate"]
    assert "recall_grain:collection_selection" in decision["selection_basis"]


def test_plan_surface_memory_recall_routes_dated_collection_quality_as_selection() -> None:
    plan = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="昨天那几本好看吗",
            task_mode_hint="daily_chat",
        )
    )

    assert plan.should_recall
    assert plan.recall_policy == RECALL_POLICY_MUST
    assert plan.reason == "anaphoric_selection_recall"
    assert plan.query_kind == "anaphoric_selection"
    assert plan.recall_contract_id == "anaphoric_selection"
    assert plan.temporal_scope == "yesterday"
    assert plan.time_lookback_days == 2
    assert plan.target_store_ids[0] == "life_memory_collection"
    assert plan.min_collection_items == 3
    assert "lightnovel_recommendation" in plan.target_domains
    assert "collection_or_episode_hit" in plan.validation_requirements
    assert "collection_item_exact_count" in plan.validation_requirements
    assert "compare_recalled_items_before_answering" in plan.answer_obligations
    assert "read_detail_evidence_before_deep_quality_judgment" in plan.answer_obligations


def test_recall_decision_trace_exposes_entity_grain_for_specific_item_followup() -> None:
    plan = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="5月27日推荐 B 这本好看吗？",
            task_mode_hint="daily_chat",
        )
    )

    decision = build_surface_memory_recall_decision_trace(plan, invoked=True)

    assert plan.primary_memory_layer == "life_memory_entity"
    assert decision["need_type"] == "entity_or_collection_selection"
    assert decision["recall_grain"] == "entity_selection"
    assert decision["retrieval_unit"] == "life_memory_entity.with_source_collection"
    assert decision["answer_scope"] == "judge_named_or_referenced_item_only"


def test_plan_surface_memory_recall_routes_collection_listing_without_choice_as_collection_summary() -> None:
    plan = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="这几天推荐过哪些轻小说？",
            task_mode_hint="daily_chat",
        )
    )

    assert plan.should_recall
    assert plan.recall_policy == RECALL_POLICY_MUST
    assert plan.reason == "collection_memory_recall"
    assert plan.query_kind == "collection_summary"
    assert plan.recall_contract_id == "collection_recent"
    assert plan.temporal_scope == "recent_days"
    assert plan.target_store_ids[0] == "life_memory_collection"
    assert "read_detail_evidence_before_deep_quality_judgment" not in plan.answer_obligations


def test_plan_surface_memory_recall_routes_last_week_collection_listing() -> None:
    plan = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="上周推过哪些书？",
            task_mode_hint="daily_chat",
        )
    )

    assert plan.should_recall
    assert plan.recall_policy == RECALL_POLICY_MUST
    assert plan.reason == "collection_memory_recall"
    assert plan.query_kind == "collection_summary"
    assert plan.temporal_scope == "recent_days"
    assert plan.time_lookback_days == 14
    assert plan.recall_contract_id == "collection_recent"
    assert plan.min_collection_items == 3
    assert "collection_item_min_count" in plan.validation_requirements


def test_plan_surface_memory_recall_treats_which_books_as_three_item_collection() -> None:
    plan = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="昨天给我推荐了哪几本书？",
            task_mode_hint="daily_chat",
        )
    )

    assert plan.should_recall
    assert plan.recall_policy == RECALL_POLICY_MUST
    assert plan.reason == "collection_memory_recall"
    assert plan.query_kind == "date_lookup"
    assert plan.temporal_scope == "yesterday"
    assert plan.min_collection_items == 3
    assert "collection_item_exact_count" in plan.validation_requirements


def test_plan_surface_memory_recall_routes_generic_web_judgment_to_detail_evidence_contract() -> None:
    plan = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="昨天那个网页内容靠谱吗？依据是什么？",
            task_mode_hint="daily_chat",
        )
    )

    assert plan.should_recall
    assert plan.recall_policy == RECALL_POLICY_MUST
    assert plan.reason == "evidence_review_recall"
    assert plan.query_kind == "evidence_review"
    assert plan.recall_contract_id == "evidence_review"
    assert plan.primary_memory_layer == "life_episode"
    assert plan.target_store_ids[:3] == ("life_episode", "life_memory_collection", "life_memory_entity")
    assert "web" in plan.target_domains
    assert "content" in plan.target_domains
    assert "target_domain_match" in plan.validation_requirements
    assert "use_detail_or_body_evidence" in plan.answer_obligations
    assert "say_if_detail_evidence_is_missing" in plan.answer_obligations
    decision = build_surface_memory_recall_decision_trace(plan, invoked=True)
    assert decision["recall_grain"] == "evidence"
    assert decision["retrieval_unit"] == "life_episode.evidence"
    assert decision["answer_scope"] == "judge_claim_from_bound_evidence"


def test_plan_surface_memory_recall_treats_prior_browser_activity_as_event_lookup() -> None:
    plan = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="昨天打开浏览器查了什么？",
            task_mode_hint="daily_chat",
        )
    )

    assert plan.should_recall
    assert plan.recall_policy == RECALL_POLICY_MUST
    assert plan.reason == "event_memory_recall"
    assert plan.query_kind == "date_lookup"
    assert "action" in plan.target_domains
    assert "web" in plan.target_domains
    assert plan.primary_memory_layer == "life_episode"


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


def test_plan_surface_memory_recall_skips_current_preference_update_without_lookup() -> None:
    plan = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="不要用 pro，没有必要。",
            task_mode_hint="daily_chat",
        )
    )

    assert not plan.should_recall
    assert plan.recall_policy == RECALL_POLICY_NO
    assert plan.reason == "memory_write_current_turn_only"


def test_plan_surface_memory_recall_skips_response_style_feedback_not_collection_lookup() -> None:
    plan = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="昨天那几本我能对上，这种说辞不像人话。",
            task_mode_hint="daily_chat",
        )
    )

    assert not plan.should_recall
    assert plan.recall_policy == RECALL_POLICY_NO
    assert plan.reason == "response_style_feedback_current_turn"
    assert plan.temporal_scope == "yesterday"


def test_plan_surface_memory_recall_routes_past_response_quality_feedback() -> None:
    plan = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="我之前说文本质量不像人话，是哪里的问题？",
            task_mode_hint="daily_chat",
        )
    )

    assert plan.should_recall
    assert plan.recall_policy == RECALL_POLICY_MUST
    assert plan.reason == "explicit_memory_recall"
    assert plan.query_kind == "preference_lookup"
    assert plan.target_store_ids[0] == "semantic_memory"


def test_plan_surface_memory_recall_routes_open_loop_to_semantic_first() -> None:
    plan = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="记忆系统还有什么待办没完成？",
            task_mode_hint="daily_chat",
        )
    )

    assert plan.should_recall
    assert plan.recall_policy == RECALL_POLICY_MUST
    assert plan.reason == "explicit_memory_recall"
    assert plan.query_kind == "open_loop"
    assert plan.recall_contract_id == "open_loop"
    assert plan.primary_memory_layer == "semantic_memory"
    assert plan.target_store_ids[0] == "semantic_memory"
    assert "open_loop" in plan.target_domains
    assert "self_change" in plan.target_domains
    decision = build_surface_memory_recall_decision_trace(plan, invoked=True)
    assert decision["need_type"] == "open_loop_memory"


def test_plan_surface_memory_recall_routes_self_change_to_semantic_first() -> None:
    plan = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="记忆系统改过什么？",
            task_mode_hint="daily_chat",
        )
    )

    assert plan.should_recall
    assert plan.recall_policy == RECALL_POLICY_MUST
    assert plan.reason == "explicit_memory_recall"
    assert plan.query_kind == "self_change"
    assert plan.recall_contract_id == "self_change"
    assert plan.primary_memory_layer == "semantic_memory"
    assert plan.target_store_ids[0] == "semantic_memory"
    decision = build_surface_memory_recall_decision_trace(plan, invoked=True)
    assert decision["need_type"] == "self_change_memory"


def test_plan_surface_memory_recall_routes_stuck_followup_to_failure_debug() -> None:
    plan = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="为什么又卡住了？",
            task_mode_hint="daily_chat",
        )
    )

    assert plan.should_recall
    assert plan.recall_policy == RECALL_POLICY_MUST
    assert plan.reason == "failure_debug_recall"
    assert plan.query_kind == "failure_debug"
    assert "failure" in plan.target_domains


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
    decision = build_surface_memory_recall_decision_trace(plan, invoked=True)
    assert decision["need_type"] == "episode_event"
    assert decision["primary_memory_layer"] == "life_episode"
    assert "event_time_scope_match" in decision["validation_requirements"]


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
    assert "preference" in plan.target_domains
    assert "recommendation" not in plan.target_domains
    assert "lightnovel_recommendation" not in plan.target_domains
    assert "state_preference_as_user_rule" in plan.answer_obligations
    assert "简介后应该换行" in joined
    assert "5月28日推荐" not in joined
    assert validation["passed"] is True
    assert validation["selected_store_counts"] == {"life_episode": 1}
    assert receipt["meets_primary_layer"] is False
    assert receipt["meets_expected_layer"] is True


def test_preference_rule_recall_prefers_semantic_profile_over_episode_echo() -> None:
    captured: dict[str, object] = {}

    class _RecallService:
        def recall(self, **kwargs):  # noqa: ANN003
            captured.update(kwargs)
            return SimpleNamespace(
                output={
                    "query": kwargs.get("query", ""),
                    "status": "OK",
                    "stage": "S_RET_RECALL",
                    "degraded": False,
                    "candidate_count": 2,
                    "routes": [
                        {"route_id": "semantic", "store_id": "semantic_memory"},
                        {"route_id": "life_episode", "store_id": "life_episode"},
                    ],
                    "candidates": [
                        {
                            "source_ref": "chat://request/req-pref-episode",
                            "evidence_refs": ["chat://request/req-pref-episode#user"],
                            "text": "用户提到：还记得我说推荐格式要怎样吗？ 回复：记得，简介后应该换行，别模板腔。",
                            "score": 0.96,
                            "metadata": {"store_id": "life_episode", "truth_state": "VERIFIED"},
                        },
                        {
                            "source_ref": "ledger://semantic_memory/sem-pref-format",
                            "evidence_refs": ["chat://request/req-pref-format#user"],
                            "text": "用户偏好：以后推荐轻小说时，每本书的简介后应该换行，别模板腔。",
                            "score": 0.80,
                            "metadata": {
                                "store_id": "semantic_memory",
                                "tags": ["semantic_kind:user_preference", "memory_family:user_model"],
                                "semantic_provenance_status": "resolved",
                                "semantic_evidence_refs": ["chat://request/req-pref-format#user"],
                            },
                        },
                    ],
                }
            )

    packet = recall_surface_memory_for_context(
        recall_service=_RecallService(),
        context=ThreeLayerContinuityContext(
            current_user_message="我的推荐格式偏好是什么？",
            task_mode_hint="daily_chat",
        ),
        request_id="req-pref-semantic-first",
        query_embedding=[0.1] * 16,
        person_id="owner",
        session_id="qq_dm_owner",
        ts_ms=_ts(2026, 5, 28, 19),
    )

    validation = dict(dict(packet.trace or {})["validation"])
    receipt = dict(dict(packet.trace or {})["recall_receipt"])
    assert list(captured["target_store_ids"])[:2] == ["semantic_memory", "life_episode"]
    assert packet.rows[0].store_id == "semantic_memory"
    assert packet.facts[0] == "长期事实：以后推荐轻小说时，每本书的简介后应该换行，别模板腔。"
    semantic_usage = dict(dict(packet.trace or {})["semantic_usage"])
    assert semantic_usage["mode"] == "answer_memory_lookup"
    assert semantic_usage["facts"] == ["以后推荐轻小说时，每本书的简介后应该换行，别模板腔。"]
    semantic_provenance = dict(receipt["semantic_provenance"])
    assert semantic_provenance["semantic_candidate_count"] == 1
    assert semantic_provenance["resolved_count"] == 1
    assert semantic_provenance["unresolved_count"] == 0
    assert semantic_provenance["evidence_refs"] == ["chat://request/req-pref-format#user"]
    assert "还记得我说" not in packet.facts[0]
    assert validation["selected_store_counts"] == {"semantic_memory": 1}
    assert receipt["meets_primary_layer"] is True


def test_relationship_feedback_life_episode_fact_is_normalized_for_recall() -> None:
    packet = build_surface_memory_recall_packet(
        {
            "query": "我之前说文本质量不像人话，是哪里的问题？",
            "status": "OK",
            "stage": "S_RET_RECALL",
            "degraded": False,
            "candidate_count": 1,
            "routes": [{"route_id": "life_episode", "store_id": "life_episode"}],
            "candidates": [
                {
                    "source_ref": "chat://request/req-relationship-feedback",
                    "evidence_refs": ["chat://request/req-relationship-feedback#user"],
                    "text": "用户提到：文本质量不像人话，太台词腔了。 回复：收到，我以后把证据转成更自然的聊天方式。",
                    "score": 0.96,
                    "metadata": {"store_id": "life_episode", "truth_state": "VERIFIED", "memory_class": "relationship_feedback"},
                }
            ],
        },
        query="我之前说文本质量不像人话，是哪里的问题？",
        max_facts=4,
        max_chars_per_fact=180,
        validation_requirements=("trusted_memory_hit", "source_ref_present"),
        temporal_scope="all_time",
        query_kind="preference_lookup",
        target_domains=("preference",),
        recall_contract_id="preference",
        primary_memory_layer="semantic_memory",
        expected_memory_layers=("semantic_memory", "life_episode", "sqlite_ledger", "episodic_vector", "neo4j_bond"),
        answer_obligations=("state_preference_as_user_rule", "keep_source_episode_traceable"),
    )

    joined = "\n".join(packet.facts)
    assert "回答要像正常聊天" in joined
    assert "台词腔" in joined


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
    decision = dict(dict(packet.trace or {})["recall_decision"])
    assert decision["schema"] == "Natsume.surface_recall_decision.v1"
    assert decision["decision"] == "skip"
    assert decision["invoked"] is False
    assert decision["skip_reason"] == "low_signal_surface_turn"
    assert decision["recall_policy"] == RECALL_POLICY_NO


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
    decision = dict(dict(packet.trace or {})["recall_decision"])
    assert decision["decision"] == "recall"
    assert decision["invoked"] is True
    assert decision["recall_policy"] == RECALL_POLICY_MAY
    assert decision["primary_memory_layer"] == ""
    assert "life_episode" in decision["target_store_ids"]


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
    readiness = dict(dict(packet.trace or {})["recall_answer_readiness"])
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
    assert dict(packet.trace or {})["answer_ready"] is True
    assert readiness["schema"] == "Natsume.surface_recall_answer_readiness.v1"
    assert readiness["status"] == "READY"
    assert readiness["ready_to_answer"] is True
    assert readiness["response_mode"] == "answer_from_collection_memory"
    assert readiness["selected_item_count"] == 3
    assert readiness["min_collection_items"] == 3


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
    readiness = dict(dict(packet.trace or {})["recall_answer_readiness"])
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
    assert dict(packet.trace or {})["answer_ready"] is False
    assert readiness["status"] == "NOT_READY"
    assert readiness["ready_to_answer"] is False
    assert readiness["response_mode"] == "state_recall_validation_failed"
    assert "validation_failed" in readiness["reasons"]
    assert "scope_match" in readiness["missing"]


def test_recall_surface_memory_coverage_failure_drops_partial_collection_hit() -> None:
    packet = build_surface_memory_recall_packet(
        SimpleNamespace(
            output={
                "query": "这几天十几本里有哪些？",
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "routes": [{"route_id": "surface_life_episode", "store_id": "life_episode"}],
                "candidate_count": 1,
                "ts_ms": _ts(2026, 5, 29, 8),
                "candidates": [
                    {
                        "doc_id": "ep-partial",
                        "source_ref": "chat://request/partial",
                        "evidence_refs": ["chat://request/partial#assistant"],
                        "text": "用户之前问过轻小说推荐格式，但没有推荐集合条目。",
                        "score": 0.95,
                        "metadata": {
                            "store_id": "life_episode",
                            "source_ref": "chat://request/partial",
                            "evidence_refs_primary": "chat://request/partial#assistant",
                        },
                    }
                ],
            }
        ),
        query="这几天十几本里有哪些？",
        max_facts=4,
        max_chars_per_fact=260,
        temporal_scope="recent_days",
        query_kind="collection_summary",
        recall_contract_id="collection_recent",
        primary_memory_layer="life_memory_collection",
        expected_memory_layers=("life_memory_collection", "life_memory_entity"),
        min_collection_items=10,
        validation_requirements=(),
        answer_obligations=("summarize_cross_day_collection", "preserve_item_identity"),
    )

    coverage = dict(dict(packet.trace or {})["recall_coverage"])
    readiness = dict(dict(packet.trace or {})["recall_answer_readiness"])
    assert coverage["passed"] is False
    assert coverage["score"] < 0.66
    assert dict(packet.trace or {})["coverage_failed"] is True
    assert packet.facts == ()
    assert packet.evidence_refs == ()
    assert packet.confidence == 0.0
    assert packet.degraded is True
    assert packet.reason_code == SURFACE_MEMORY_RECALL_COVERAGE_FAILED
    assert any("召回覆盖度不足" in item for item in packet.must_not_do)
    assert readiness["status"] == "NOT_READY"
    assert readiness["response_mode"] == "state_recall_coverage_missing"
    assert "coverage_failed" in readiness["reasons"]
    assert "insufficient_item_count" in readiness["reasons"]


def test_failure_debug_recall_rejects_unrelated_failure_semantic_memory() -> None:
    packet = build_surface_memory_recall_packet(
        SimpleNamespace(
            output={
                "query": "DeepSeek失败原因是什么？",
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "routes": [{"route_id": "surface_semantic", "store_id": "semantic_memory"}],
                "candidate_count": 1,
                "ts_ms": _ts(2026, 5, 29, 8),
                "candidates": [
                    {
                        "doc_id": "sem-lightnovel-failure",
                        "source_ref": "ledger://semantic_memory/sem-lightnovel-failure",
                        "evidence_refs": ["codex_production://event/lightnovel-failed"],
                        "text": "故障经验：codex_lightnovel_recommender 失败；原因 LIGHTNOVEL_NATIVE_NO_RECOMMENDATIONS。",
                        "score": 0.98,
                        "metadata": {
                            "store_id": "semantic_memory",
                            "source_ref": "ledger://semantic_memory/sem-lightnovel-failure",
                            "evidence_refs_primary": "codex_production://event/lightnovel-failed",
                            "domain": "failure",
                        },
                    }
                ],
            }
        ),
        query="DeepSeek失败原因是什么？",
        max_facts=4,
        max_chars_per_fact=220,
        temporal_scope="unspecified",
        query_kind="failure_debug",
        target_domains=("failure",),
        recall_contract_id="failure_debug",
        primary_memory_layer="life_episode",
        expected_memory_layers=("life_episode", "semantic_memory", "sqlite_ledger", "episodic_vector"),
        validation_requirements=("error_episode_or_semantic_hit", "source_ref_present"),
        answer_obligations=("explain_failure_from_recorded_result", "do_not_invent_root_cause"),
    )

    coverage = dict(dict(packet.trace or {})["recall_coverage"])
    readiness = dict(dict(packet.trace or {})["recall_answer_readiness"])
    relevance = dict(coverage["query_relevance"])
    assert relevance["required"] is True
    assert relevance["score"] == 0.0
    assert "deepseek" in relevance["missing"]
    assert "query_relevance" in coverage["missing"]
    assert packet.facts == ()
    assert packet.reason_code == SURFACE_MEMORY_RECALL_COVERAGE_FAILED
    assert readiness["status"] == "NOT_READY"
    assert "query_relevance" in readiness["reasons"]


def test_query_relevance_uses_current_turn_not_context_tail_for_failure_hit() -> None:
    packet = build_surface_memory_recall_packet(
        SimpleNamespace(
            output={
                "query": "DeepSeek失败原因是什么？；最近推荐格式要简介换行；昨天三本呢",
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "routes": [{"route_id": "surface_life_episode", "store_id": "life_episode"}],
                "candidate_count": 1,
                "ts_ms": _ts(2026, 5, 29, 8),
                "candidates": [
                    {
                        "doc_id": "ep-deepseek-timeout",
                        "source_ref": "action://event/deepseek-timeout",
                        "evidence_refs": ["action://event/deepseek-timeout#result"],
                        "text": "codex_production llm_pool_call FAILED DEEPSEEK_TIMEOUT DeepSeek 失败原因：供应商超时，后续优先检查网络和 provider 健康状态。",
                        "score": 0.98,
                        "metadata": {
                            "store_id": "life_episode",
                            "source_ref": "action://event/deepseek-timeout",
                            "evidence_refs_primary": "action://event/deepseek-timeout#result",
                            "memory_class": "failure_lesson",
                            "domain": "failure",
                        },
                    }
                ],
            }
        ),
        query="DeepSeek失败原因是什么？；最近推荐格式要简介换行；昨天三本呢",
        max_facts=4,
        max_chars_per_fact=220,
        temporal_scope="unspecified",
        query_kind="failure_debug",
        target_domains=("failure",),
        recall_contract_id="failure_debug",
        primary_memory_layer="life_episode",
        expected_memory_layers=("life_episode", "semantic_memory", "sqlite_ledger", "episodic_vector"),
        validation_requirements=("error_episode_or_semantic_hit", "source_ref_present"),
        answer_obligations=("explain_failure_from_recorded_result", "do_not_invent_root_cause"),
    )

    coverage = dict(dict(packet.trace or {})["recall_coverage"])
    relevance = dict(coverage["query_relevance"])
    assert packet.reason_code is None
    assert "供应商超时" in "\n".join(packet.facts)
    assert coverage["passed"] is True
    assert relevance["score"] == 1.0
    assert relevance["missing"] == []


def test_failure_debug_recall_drops_unrelated_failure_noise_when_precise_hit_exists() -> None:
    packet = build_surface_memory_recall_packet(
        SimpleNamespace(
            output={
                "query": "DeepSeek失败原因是什么？",
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "routes": [
                    {"route_id": "surface_life_episode", "store_id": "life_episode"},
                    {"route_id": "surface_sqlite", "store_id": "sqlite_ledger"},
                ],
                "candidate_count": 2,
                "ts_ms": _ts(2026, 5, 29, 8),
                "candidates": [
                    {
                        "doc_id": "ep-deepseek-timeout",
                        "source_ref": "action://event/deepseek-timeout",
                        "evidence_refs": ["action://event/deepseek-timeout#result"],
                        "text": "codex_production llm_pool_call FAILED DEEPSEEK_TIMEOUT DeepSeek 失败原因：供应商超时，后续优先检查网络和 provider 健康状态。",
                        "score": 0.98,
                        "metadata": {
                            "store_id": "life_episode",
                            "source_ref": "action://event/deepseek-timeout",
                            "evidence_refs_primary": "action://event/deepseek-timeout#result",
                            "memory_class": "failure_lesson",
                            "domain": "failure",
                        },
                    },
                    {
                        "doc_id": "noise-lightnovel-fetch-failed",
                        "source_ref": "action://event/lightnovel-fetch-failed",
                        "evidence_refs": ["action://event/lightnovel-fetch-failed#result"],
                        "text": "LIGHTNOVEL_DEEP_REVIEW_FETCH_FAILED；昨天推荐 A：培养失败的前美少女童星；昨天推荐 B：与神在河岸。",
                        "score": 0.73,
                        "metadata": {
                            "store_id": "sqlite_ledger",
                            "source_ref": "action://event/lightnovel-fetch-failed",
                            "evidence_refs_primary": "action://event/lightnovel-fetch-failed#result",
                            "domain": "failure",
                        },
                    },
                ],
            }
        ),
        query="DeepSeek失败原因是什么？",
        max_facts=4,
        max_chars_per_fact=260,
        temporal_scope="unspecified",
        query_kind="failure_debug",
        target_domains=("failure",),
        recall_contract_id="failure_debug",
        primary_memory_layer="life_episode",
        expected_memory_layers=("life_episode", "semantic_memory", "sqlite_ledger", "episodic_vector"),
        validation_requirements=("error_episode_or_semantic_hit", "source_ref_present"),
        answer_obligations=("explain_failure_from_recorded_result", "do_not_invent_root_cause"),
    )

    joined = "\n".join(packet.facts)
    assert "供应商超时" in joined
    assert "昨天推荐" not in joined
    assert dict(dict(packet.trace or {})["recall_answer_readiness"])["status"] == "READY"


def test_preference_recall_rejects_unrelated_preference_memory_by_query_fit() -> None:
    packet = build_surface_memory_recall_packet(
        SimpleNamespace(
            output={
                "query": "还记得我说不要用 pro 吗？",
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "routes": [{"route_id": "surface_semantic", "store_id": "semantic_memory"}],
                "candidate_count": 1,
                "ts_ms": _ts(2026, 5, 29, 8),
                "candidates": [
                    {
                        "doc_id": "sem-pref-lightnovel-format",
                        "source_ref": "ledger://semantic_memory/sem-pref-lightnovel-format",
                        "evidence_refs": ["chat://request/req-pref-format#user"],
                        "text": "用户偏好：以后推荐轻小说时，每本书的简介后应该换行，别模板腔。",
                        "score": 0.98,
                        "metadata": {
                            "store_id": "semantic_memory",
                            "source_ref": "ledger://semantic_memory/sem-pref-lightnovel-format",
                            "evidence_refs_primary": "chat://request/req-pref-format#user",
                            "domain": "preference",
                        },
                    }
                ],
            }
        ),
        query="还记得我说不要用 pro 吗？",
        max_facts=4,
        max_chars_per_fact=220,
        temporal_scope="unspecified",
        query_kind="preference_lookup",
        recall_contract_id="preference",
        primary_memory_layer="semantic_memory",
        expected_memory_layers=("semantic_memory", "life_episode", "sqlite_ledger", "episodic_vector"),
        validation_requirements=("trusted_memory_hit", "source_ref_present"),
        answer_obligations=("state_preference_as_user_rule", "keep_source_episode_traceable"),
    )

    coverage = dict(dict(packet.trace or {})["recall_coverage"])
    readiness = dict(dict(packet.trace or {})["recall_answer_readiness"])
    relevance = dict(coverage["query_relevance"])
    assert relevance["required"] is True
    assert "pro" in relevance["missing"]
    assert "query_relevance" in coverage["missing"]
    assert packet.facts == ()
    assert packet.reason_code == SURFACE_MEMORY_RECALL_COVERAGE_FAILED
    assert readiness["status"] == "NOT_READY"
    assert "query_relevance" in readiness["reasons"]


def test_relationship_feedback_recall_matches_semantic_quality_summary_by_query_fit() -> None:
    packet = build_surface_memory_recall_packet(
        SimpleNamespace(
            output={
                "query": "我之前说文本质量不像人话，是哪里的问题？",
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "routes": [{"route_id": "surface_semantic", "store_id": "semantic_memory"}],
                "candidate_count": 1,
                "ts_ms": _ts(2026, 5, 29, 8),
                "candidates": [
                    {
                        "doc_id": "sem-relationship-feedback",
                        "source_ref": "ledger://semantic_memory/sem-relationship-feedback",
                        "evidence_refs": ["chat://request/req-feedback-natural-chat#user"],
                        "text": "关系反馈：用户强烈反馈：回答要像正常聊天，避免模板腔、台词腔和生硬的证据条目话术。",
                        "score": 0.98,
                        "metadata": {
                            "store_id": "semantic_memory",
                            "source_ref": "ledger://semantic_memory/sem-relationship-feedback",
                            "evidence_refs_primary": "chat://request/req-feedback-natural-chat#user",
                            "tags": ["semantic_kind:relationship_feedback", "response_quality"],
                            "domain": "relationship_feedback",
                        },
                    },
                    {
                        "doc_id": "entity-current-recommendation",
                        "source_ref": "life_memory_entity://book-a",
                        "evidence_refs": ["action://event/job-20260528:lightnovel_gateway_delivery"],
                        "text": "实体记忆：5月28日推荐 A；看点：恋爱日常；详情：https://www.lightnovel.fun/cn/detail/2801",
                        "score": 0.93,
                        "metadata": {
                            "store_id": "life_memory_entity",
                            "source_ref": "life_memory_entity://book-a",
                            "evidence_refs_primary": "action://event/job-20260528:lightnovel_gateway_delivery",
                        },
                    }
                ],
            }
        ),
        query="我之前说文本质量不像人话，是哪里的问题？",
        max_facts=4,
        max_chars_per_fact=220,
        temporal_scope="unspecified",
        query_kind="preference_lookup",
        recall_contract_id="preference",
        primary_memory_layer="semantic_memory",
        expected_memory_layers=("semantic_memory", "life_episode", "sqlite_ledger", "episodic_vector"),
        validation_requirements=("trusted_memory_hit", "source_ref_present"),
        answer_obligations=("state_preference_as_user_rule", "keep_source_episode_traceable"),
    )

    coverage = dict(dict(packet.trace or {})["recall_coverage"])
    relevance = dict(coverage["query_relevance"])
    assert packet.reason_code is None
    assert packet.facts
    assert "回答要像正常聊天" in packet.facts[0]
    assert "模板腔" in packet.facts[0]
    assert "5月28日推荐" not in "\n".join(packet.facts)
    assert packet.evidence_refs == ("chat://request/req-feedback-natural-chat#user",)
    assert coverage["passed"] is True
    assert relevance["required"] is True
    assert relevance["score"] >= 1.0
    assert relevance["missing"] == []


def test_open_loop_recall_prefers_open_commitment_semantic_memory() -> None:
    packet = build_surface_memory_recall_packet(
        SimpleNamespace(
            output={
                "query": "记忆系统还有什么待办没完成？",
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "routes": [{"route_id": "surface_semantic", "store_id": "semantic_memory"}],
                "candidate_count": 3,
                "ts_ms": _ts(2026, 5, 29, 8),
                "candidates": [
                    {
                        "doc_id": "sem-closed-loop",
                        "source_ref": "ledger://semantic_memory/sem-closed-loop",
                        "evidence_refs": ["chat://request/req-closed-loop#user"],
                        "text": "承诺完成：记忆系统的旧召回修复已经完成，关闭这个待办。",
                        "score": 0.99,
                        "metadata": {
                            "store_id": "semantic_memory",
                            "tags": ["semantic_kind:commitment", "commitment", "open_loop", "open_loop:closed"],
                            "source_ref": "ledger://semantic_memory/sem-closed-loop",
                            "evidence_refs_primary": "chat://request/req-closed-loop#user",
                        },
                    },
                    {
                        "doc_id": "sem-open-loop",
                        "source_ref": "ledger://semantic_memory/sem-open-loop",
                        "evidence_refs": ["chat://request/req-open-loop#user"],
                        "text": "承诺和待办：后续要把记忆系统的召回覆盖 open loop 和自我改变，记成待办。",
                        "score": 0.88,
                        "metadata": {
                            "store_id": "semantic_memory",
                            "tags": ["semantic_kind:commitment", "commitment", "open_loop", "open_loop:open"],
                            "source_ref": "ledger://semantic_memory/sem-open-loop",
                            "evidence_refs_primary": "chat://request/req-open-loop#user",
                        },
                    },
                    {
                        "doc_id": "entity-current-recommendation",
                        "source_ref": "life_memory_entity://book-a",
                        "evidence_refs": ["action://event/job-20260528:lightnovel_gateway_delivery"],
                        "text": "实体记忆：5月28日推荐 A；看点：恋爱日常。",
                        "score": 0.93,
                        "metadata": {"store_id": "life_memory_entity"},
                    },
                ],
            }
        ),
        query="记忆系统还有什么待办没完成？",
        max_facts=4,
        max_chars_per_fact=240,
        temporal_scope="all_time",
        query_kind="open_loop",
        recall_contract_id="open_loop",
        primary_memory_layer="semantic_memory",
        expected_memory_layers=("semantic_memory", "life_episode", "sqlite_ledger", "episodic_vector", "neo4j_bond"),
        validation_requirements=("trusted_memory_hit", "source_ref_present"),
        answer_obligations=("state_open_loop_status", "separate_done_from_pending"),
    )

    coverage = dict(dict(packet.trace or {})["recall_coverage"])
    relevance = dict(coverage["query_relevance"])
    joined = "\n".join(packet.facts)
    assert packet.reason_code is None
    assert "召回覆盖 open loop 和自我改变" in joined
    assert "旧召回修复已经完成" not in joined
    assert "5月28日推荐" not in joined
    assert packet.evidence_refs == ("chat://request/req-open-loop#user",)
    assert coverage["passed"] is True
    assert relevance["score"] >= 1.0


def test_self_change_recall_prefers_self_model_semantic_memory() -> None:
    packet = build_surface_memory_recall_packet(
        SimpleNamespace(
            output={
                "query": "记忆系统改过什么？",
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "routes": [{"route_id": "surface_semantic", "store_id": "semantic_memory"}],
                "candidate_count": 2,
                "ts_ms": _ts(2026, 5, 29, 8),
                "candidates": [
                    {
                        "doc_id": "sem-self-change",
                        "source_ref": "ledger://semantic_memory/sem-self-change",
                        "evidence_refs": ["chat://request/req-self-change#user"],
                        "text": "自我改变：她做过的事都要留下生命经历锚点，但不等于所有日志都进入可召回记忆。",
                        "score": 0.91,
                        "metadata": {
                            "store_id": "semantic_memory",
                            "tags": ["semantic_kind:self_change", "self_change", "memory_family:self_model"],
                            "source_ref": "ledger://semantic_memory/sem-self-change",
                            "evidence_refs_primary": "chat://request/req-self-change#user",
                        },
                    },
                    {
                        "doc_id": "sem-open-loop",
                        "source_ref": "ledger://semantic_memory/sem-open-loop",
                        "evidence_refs": ["chat://request/req-open-loop#user"],
                        "text": "承诺和待办：后续要把记忆系统的召回覆盖 open loop 和自我改变，记成待办。",
                        "score": 0.97,
                        "metadata": {
                            "store_id": "semantic_memory",
                            "tags": ["semantic_kind:commitment", "commitment", "open_loop", "open_loop:open"],
                            "source_ref": "ledger://semantic_memory/sem-open-loop",
                            "evidence_refs_primary": "chat://request/req-open-loop#user",
                        },
                    },
                ],
            }
        ),
        query="记忆系统改过什么？",
        max_facts=4,
        max_chars_per_fact=240,
        temporal_scope="all_time",
        query_kind="self_change",
        recall_contract_id="self_change",
        primary_memory_layer="semantic_memory",
        expected_memory_layers=("semantic_memory", "life_episode", "sqlite_ledger", "episodic_vector", "neo4j_bond"),
        validation_requirements=("trusted_memory_hit", "source_ref_present"),
        answer_obligations=("state_self_change_and_current_effect", "do_not_hide_correction_history"),
    )

    coverage = dict(dict(packet.trace or {})["recall_coverage"])
    joined = "\n".join(packet.facts)
    assert packet.reason_code is None
    assert "生命经历锚点" in joined
    assert "所有日志都进入可召回记忆" in joined
    assert "召回覆盖 open loop" not in joined
    assert packet.evidence_refs == ("chat://request/req-self-change#user",)
    assert coverage["passed"] is True


def test_correction_recall_rejects_unrelated_correction_memory_by_query_fit() -> None:
    packet = build_surface_memory_recall_packet(
        SimpleNamespace(
            output={
                "query": "你刚才是不是记错了 OneBot 在哪？",
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "routes": [{"route_id": "surface_life_episode", "store_id": "life_episode"}],
                "candidate_count": 1,
                "ts_ms": _ts(2026, 5, 29, 8),
                "candidates": [
                    {
                        "doc_id": "ep-cat-cover-correction",
                        "source_ref": "chat://request/req-corrected-fix-cat-cover",
                        "evidence_refs": ["chat://request/req-corrected-fix-cat-cover#user"],
                        "text": "纠错记忆：用户指出没有封面有猫这本，我已经撤回了之前那个判断。",
                        "score": 0.97,
                        "metadata": {
                            "store_id": "life_episode",
                            "source_ref": "chat://request/req-corrected-fix-cat-cover",
                            "evidence_refs_primary": "chat://request/req-corrected-fix-cat-cover#user",
                        },
                    }
                ],
            }
        ),
        query="你刚才是不是记错了 OneBot 在哪？",
        max_facts=4,
        max_chars_per_fact=220,
        temporal_scope="current",
        query_kind="correction",
        recall_contract_id="correction",
        primary_memory_layer="life_episode",
        expected_memory_layers=("life_episode", "semantic_memory", "sqlite_ledger", "episodic_vector"),
        validation_requirements=("correction_state_checked", "suppressed_not_fact"),
        answer_obligations=("prefer_corrected_version", "do_not_repeat_suppressed_fact"),
    )

    coverage = dict(dict(packet.trace or {})["recall_coverage"])
    readiness = dict(dict(packet.trace or {})["recall_answer_readiness"])
    relevance = dict(coverage["query_relevance"])
    assert relevance["required"] is True
    assert "onebot" in relevance["missing"]
    assert "query_relevance" in coverage["missing"]
    assert packet.facts == ()
    assert packet.reason_code == SURFACE_MEMORY_RECALL_COVERAGE_FAILED
    assert readiness["status"] == "NOT_READY"
    assert "query_relevance" in readiness["reasons"]


def test_selection_recall_requires_detail_anchor_for_deep_quality_judgment() -> None:
    packet = build_surface_memory_recall_packet(
        SimpleNamespace(
            output={
                "query": "这三本里哪本好看？",
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "routes": [{"route_id": "surface_life_collection", "store_id": "life_memory_collection"}],
                "candidate_count": 1,
                "ts_ms": _ts(2026, 5, 29, 8),
                "candidates": [
                    {
                        "doc_id": "col-current-no-detail",
                        "source_ref": "life_memory_collection://col-current-no-detail",
                        "evidence_refs": ["gateway://message/current/card/1"],
                        "text": "本次轻小说推荐集合，记录 3 个条目；可比较证据：1. A｜2. B｜3. C",
                        "score": 0.95,
                        "metadata": {
                            "store_id": "life_memory_collection",
                            "scope_key": "delivery:ep-current",
                            "domain": "lightnovel_recommendation",
                            "item_count": 3,
                            "evidence_refs_primary": "gateway://message/current/card/1",
                            "items": [
                                {"item_title": "A", "item_summary": "看点：恋爱日常", "position": 1},
                                {"item_title": "B", "item_summary": "看点：奇幻身份", "position": 2},
                                {"item_title": "C", "item_summary": "看点：日常喜剧", "position": 3},
                            ],
                        },
                    }
                ],
            }
        ),
        query="这三本里哪本好看？",
        max_facts=4,
        max_chars_per_fact=700,
        temporal_scope="current",
        query_kind="anaphoric_selection",
        recall_contract_id="anaphoric_selection",
        primary_memory_layer="life_memory_collection",
        expected_memory_layers=("life_memory_collection", "life_memory_entity", "life_episode"),
        min_collection_items=3,
        validation_requirements=("collection_or_episode_hit", "evidence_refs_present", "no_self_echo", "collection_item_min_count", "collection_item_exact_count"),
        answer_obligations=("ground_selection_in_recalled_items", "read_detail_evidence_before_deep_quality_judgment"),
    )

    receipt = dict(dict(packet.trace or {})["recall_receipt"])
    coverage = dict(dict(packet.trace or {})["recall_coverage"])
    readiness = dict(dict(packet.trace or {})["recall_answer_readiness"])
    strategy = dict(dict(packet.trace or {})["recall_answer_strategy"])
    assert receipt["detail_evidence"]["required"] is True
    assert receipt["detail_evidence"]["basis"] == "missing_detail_evidence"
    assert coverage["passed"] is False
    assert "detail_evidence" in coverage["missing"]
    assert dict(packet.trace or {})["coverage_failed"] is True
    assert packet.facts == ()
    assert packet.reason_code == SURFACE_MEMORY_RECALL_COVERAGE_FAILED
    assert readiness["status"] == "NOT_READY"
    assert readiness["ready_to_answer"] is False
    assert readiness["response_mode"] == "fetch_or_request_detail_evidence"
    assert readiness["surface_action"] == "fetch_more_evidence"
    assert "missing_detail_evidence" in readiness["reasons"]
    assert strategy["action"] == "fetch_more_evidence"
    assert strategy["claim_scope"] == "no_fact_claim"
    assert strategy["should_fetch_more_evidence"] is True


def test_selection_recall_must_not_infer_user_reading_state() -> None:
    packet = build_surface_memory_recall_packet(
        SimpleNamespace(
            output={
                "query": "昨天那几本好看吗？",
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "routes": [{"route_id": "surface_life_collection", "store_id": "life_memory_collection"}],
                "candidate_count": 1,
                "ts_ms": _ts(2026, 5, 29, 8),
                "candidates": [
                    {
                        "doc_id": "col-yesterday-books",
                        "source_ref": "life_memory_collection://col-yesterday-books",
                        "evidence_refs": ["gateway://message/yesterday/card/1"],
                        "text": "2026-05-28 当天轻小说推荐集合，记录 3 个条目；可比较证据：1. A｜2. B｜3. C",
                        "score": 0.95,
                        "metadata": {
                            "store_id": "life_memory_collection",
                            "scope_key": "daily:2026-05-28",
                            "domain": "lightnovel_recommendation",
                            "item_count": 3,
                            "evidence_refs_primary": "gateway://message/yesterday/card/1",
                            "items": [
                                {"item_title": "A", "item_summary": "看点：恋爱日常", "detail_url": "https://www.lightnovel.fun/cn/detail/a"},
                                {"item_title": "B", "item_summary": "看点：奇幻身份", "detail_url": "https://www.lightnovel.fun/cn/detail/b"},
                                {"item_title": "C", "item_summary": "看点：日常喜剧", "detail_url": "https://www.lightnovel.fun/cn/detail/c"},
                            ],
                        },
                    }
                ],
            }
        ),
        query="昨天那几本好看吗？",
        max_facts=4,
        max_chars_per_fact=700,
        temporal_scope="yesterday",
        query_kind="anaphoric_selection",
        recall_contract_id="anaphoric_selection",
        primary_memory_layer="life_memory_collection",
        expected_memory_layers=("life_memory_collection", "life_memory_entity", "life_episode"),
        min_collection_items=3,
        validation_requirements=("collection_or_episode_hit", "evidence_refs_present", "no_self_echo", "collection_item_min_count", "collection_item_exact_count"),
        answer_obligations=("ground_selection_in_recalled_items", "read_detail_evidence_before_deep_quality_judgment"),
    )

    assert any("不要臆测用户昨天有没有看" in item and "终于来问" in item for item in packet.must_not_do)


def test_selection_recall_treats_detail_anchor_as_fetch_needed_before_quality_judgment() -> None:
    packet = build_surface_memory_recall_packet(
        SimpleNamespace(
            output={
                "query": "这两本里哪本好看？",
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "routes": [{"route_id": "surface_life_episode", "store_id": "life_episode"}],
                "candidate_count": 1,
                "ts_ms": _ts(2026, 5, 29, 8),
                "candidates": [
                    {
                        "doc_id": "ep-current-detail",
                        "source_ref": "action://event/recommendation-delivery",
                        "evidence_refs": ["gateway://message/current/card/1"],
                        "text": "我刚才推荐了 2 本轻小说：1. A；2. B。",
                        "score": 0.95,
                        "metadata": {
                            "store_id": "life_episode",
                            "source_ref": "action://event/recommendation-delivery",
                            "evidence_refs_primary": "gateway://message/current/card/1",
                            "episode_metadata": {
                                "selected_recommendations": [
                                    {"title": "A", "detail_url": "https://www.lightnovel.fun/cn/detail/a"},
                                    {"title": "B", "detail_url": "https://www.lightnovel.fun/cn/detail/b"},
                                ]
                            },
                        },
                    }
                ],
            }
        ),
        query="这两本里哪本好看？",
        max_facts=4,
        max_chars_per_fact=700,
        temporal_scope="current",
        query_kind="anaphoric_selection",
        recall_contract_id="anaphoric_selection",
        primary_memory_layer="life_memory_collection",
        expected_memory_layers=("life_memory_collection", "life_memory_entity", "life_episode"),
        min_collection_items=2,
        validation_requirements=("collection_or_episode_hit", "evidence_refs_present", "no_self_echo", "collection_item_min_count", "collection_item_exact_count"),
        answer_obligations=("ground_selection_in_recalled_items", "read_detail_evidence_before_deep_quality_judgment"),
    )

    receipt = dict(dict(packet.trace or {})["recall_receipt"])
    coverage = dict(dict(packet.trace or {})["recall_coverage"])
    readiness = dict(dict(packet.trace or {})["recall_answer_readiness"])
    strategy = dict(dict(packet.trace or {})["recall_answer_strategy"])
    assert packet.reason_code == SURFACE_MEMORY_RECALL_COVERAGE_FAILED
    assert packet.facts == ()
    assert receipt["selected_item_count"] == 2
    assert receipt["detail_evidence"]["basis"] == "detail_anchor"
    assert receipt["detail_evidence"]["ready_for_deep_review"] is True
    assert coverage["passed"] is False
    assert coverage["dimensions"]["detail_evidence"] == 0.0
    assert "detail_evidence" in coverage["missing"]
    assert readiness["status"] == "NOT_READY"
    assert readiness["ready_to_answer"] is False
    assert readiness["response_mode"] == "fetch_or_request_detail_evidence"
    assert readiness["detail_evidence"]["basis"] == "detail_anchor"
    assert strategy["action"] == "fetch_more_evidence"
    assert strategy["claim_scope"] == "no_fact_claim"
    assert strategy["evidence_level"] == "missing"
    assert strategy["should_state_memory_only"] is False
    assert "missing_detail_evidence" in strategy["reasons"]


def test_generic_evidence_review_requires_body_or_detail_anchor() -> None:
    packet = build_surface_memory_recall_packet(
        SimpleNamespace(
            output={
                "query": "昨天那个网页内容靠谱吗？依据是什么？",
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "routes": [{"route_id": "surface_life_episode", "store_id": "life_episode"}],
                "candidate_count": 1,
                "ts_ms": _ts(2026, 5, 29, 8),
                "candidates": [
                    {
                        "doc_id": "ep-web-summary-only",
                        "source_ref": "action://event/web-check",
                        "text": "昨天查过一个网页，结论是看起来还行。",
                        "score": 0.94,
                        "metadata": {
                            "store_id": "life_episode",
                            "source_ref": "action://event/web-check",
                        },
                    }
                ],
            }
        ),
        query="昨天那个网页内容靠谱吗？依据是什么？",
        max_facts=4,
        max_chars_per_fact=320,
        temporal_scope="yesterday",
        query_kind="evidence_review",
        target_domains=("web", "content"),
        recall_contract_id="evidence_review",
        primary_memory_layer="life_episode",
        expected_memory_layers=("life_episode", "life_memory_collection", "semantic_memory"),
        validation_requirements=("trusted_memory_hit", "source_ref_present", "target_domain_match"),
        answer_obligations=("use_detail_or_body_evidence", "say_if_detail_evidence_is_missing"),
    )

    receipt = dict(dict(packet.trace or {})["recall_receipt"])
    coverage = dict(dict(packet.trace or {})["recall_coverage"])
    readiness = dict(dict(packet.trace or {})["recall_answer_readiness"])
    assert receipt["detail_evidence"]["required"] is True
    assert receipt["detail_evidence"]["basis"] == "missing_detail_evidence"
    assert coverage["passed"] is False
    assert "detail_evidence" in coverage["missing"]
    assert packet.facts == ()
    assert packet.reason_code == SURFACE_MEMORY_RECALL_COVERAGE_FAILED
    assert readiness["status"] == "NOT_READY"
    assert readiness["response_mode"] == "fetch_or_request_detail_evidence"
    assert "missing_detail_evidence" in readiness["reasons"]


def test_generic_evidence_review_url_anchor_still_requires_body_evidence() -> None:
    packet = build_surface_memory_recall_packet(
        SimpleNamespace(
            output={
                "query": "昨天那个网页内容靠谱吗？依据是什么？",
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "routes": [{"route_id": "surface_life_episode", "store_id": "life_episode"}],
                "candidate_count": 1,
                "ts_ms": _ts(2026, 5, 29, 8),
                "candidates": [
                    {
                        "doc_id": "ep-web-url-only",
                        "source_ref": "https://example.com/report",
                        "text": "昨天查过一个网页报告，旧摘要说看起来还行。",
                        "score": 0.94,
                        "metadata": {
                            "store_id": "life_episode",
                            "source_ref": "https://example.com/report",
                            "domain": "web",
                        },
                    }
                ],
            }
        ),
        query="昨天那个网页内容靠谱吗？依据是什么？",
        max_facts=4,
        max_chars_per_fact=320,
        temporal_scope="yesterday",
        query_kind="evidence_review",
        target_domains=("web", "content"),
        recall_contract_id="evidence_review",
        primary_memory_layer="life_episode",
        expected_memory_layers=("life_episode", "semantic_memory"),
        validation_requirements=("trusted_memory_hit", "source_ref_present", "target_domain_match"),
        answer_obligations=("use_detail_or_body_evidence", "say_if_detail_evidence_is_missing"),
    )

    receipt = dict(dict(packet.trace or {})["recall_receipt"])
    coverage = dict(dict(packet.trace or {})["recall_coverage"])
    readiness = dict(dict(packet.trace or {})["recall_answer_readiness"])
    assert receipt["detail_evidence"]["basis"] == "detail_anchor"
    assert receipt["detail_evidence"]["ready_for_deep_review"] is True
    assert coverage["passed"] is False
    assert coverage["dimensions"]["detail_evidence"] == 0.0
    assert "detail_evidence" in coverage["missing"]
    assert packet.facts == ()
    assert packet.reason_code == SURFACE_MEMORY_RECALL_COVERAGE_FAILED
    assert readiness["response_mode"] == "fetch_or_request_detail_evidence"
    assert "missing_detail_evidence" in readiness["reasons"]


def test_generic_evidence_review_filters_wrong_domain_candidates() -> None:
    packet = build_surface_memory_recall_packet(
        SimpleNamespace(
            output={
                "query": "昨天那个网页内容靠谱吗？依据是什么？",
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "routes": [{"route_id": "surface_life_episode", "store_id": "life_episode"}],
                "candidate_count": 2,
                "ts_ms": _ts(2026, 5, 29, 8),
                "candidates": [
                    {
                        "doc_id": "ep-deepseek-failure",
                        "source_ref": "action://event/deepseek-failure",
                        "text": "昨天 DeepSeek 失败原因是 provider timeout。",
                        "score": 0.99,
                        "metadata": {
                            "store_id": "life_episode",
                            "source_ref": "action://event/deepseek-failure",
                        },
                    },
                    {
                        "doc_id": "ep-web-body",
                        "source_ref": "https://example.com/report",
                        "evidence_refs": ["doc://web/report#body"],
                        "text": "昨天阅读了一个网页报告，正文里列出了样本来源和更新时间。",
                        "score": 0.80,
                        "metadata": {
                            "store_id": "life_episode",
                            "source_ref": "https://example.com/report",
                            "evidence_refs_primary": "doc://web/report#body",
                            "episode_metadata": {
                                "items": [
                                    {
                                        "title": "网页报告",
                                        "detail_url": "https://example.com/report",
                                        "content_excerpt": "正文列出了样本来源和更新时间。",
                                    }
                                ]
                            },
                        },
                    },
                ],
            }
        ),
        query="昨天那个网页内容靠谱吗？依据是什么？",
        max_facts=4,
        max_chars_per_fact=320,
        temporal_scope="yesterday",
        query_kind="evidence_review",
        target_domains=("web", "content"),
        recall_contract_id="evidence_review",
        primary_memory_layer="life_episode",
        expected_memory_layers=("life_episode", "life_memory_collection", "semantic_memory"),
        validation_requirements=("trusted_memory_hit", "source_ref_present", "target_domain_match"),
        answer_obligations=("use_detail_or_body_evidence", "say_if_detail_evidence_is_missing"),
    )

    joined = "\n".join(packet.facts)
    validation = dict(dict(packet.trace or {})["validation"])
    assert packet.reason_code is None
    assert "网页报告" in joined or "网页" in joined
    assert "DeepSeek" not in joined
    assert validation["passed"] is True
    assert any(check["name"] == "target_domain_match" and check["passed"] for check in validation["checks"])


def test_generic_evidence_review_accepts_body_excerpt_as_deep_evidence() -> None:
    packet = build_surface_memory_recall_packet(
        SimpleNamespace(
            output={
                "query": "昨天那个网页内容靠谱吗？依据是什么？",
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "routes": [{"route_id": "surface_life_episode", "store_id": "life_episode"}],
                "candidate_count": 1,
                "ts_ms": _ts(2026, 5, 29, 8),
                "candidates": [
                    {
                        "doc_id": "ep-web-body",
                        "source_ref": "https://example.com/report",
                        "evidence_refs": ["doc://web/report#body"],
                        "text": "昨天查过网页报告。",
                        "score": 0.94,
                        "metadata": {
                            "store_id": "life_episode",
                            "source_ref": "https://example.com/report",
                            "evidence_refs_primary": "doc://web/report#body",
                            "episode_metadata": {
                                "items": [
                                    {
                                        "title": "网页报告",
                                        "detail_url": "https://example.com/report",
                                        "content_excerpt": "报告正文列出了样本来源、更新时间和限制条件，可据此判断可信度。",
                                    }
                                ]
                            },
                        },
                    }
                ],
            }
        ),
        query="昨天那个网页内容靠谱吗？依据是什么？",
        max_facts=4,
        max_chars_per_fact=320,
        temporal_scope="yesterday",
        query_kind="evidence_review",
        recall_contract_id="evidence_review",
        primary_memory_layer="life_episode",
        expected_memory_layers=("life_episode", "life_memory_collection", "semantic_memory"),
        target_domains=("web", "content"),
        validation_requirements=("trusted_memory_hit", "source_ref_present", "target_domain_match"),
        answer_obligations=("use_detail_or_body_evidence", "say_if_detail_evidence_is_missing"),
    )

    receipt = dict(dict(packet.trace or {})["recall_receipt"])
    coverage = dict(dict(packet.trace or {})["recall_coverage"])
    readiness = dict(dict(packet.trace or {})["recall_answer_readiness"])
    assert packet.reason_code is None
    assert packet.facts
    assert receipt["detail_evidence"]["basis"] == "body_evidence"
    assert receipt["detail_evidence"]["body_item_count"] == 1
    assert coverage["passed"] is True
    assert coverage["dimensions"]["detail_evidence"] == 1.0
    assert readiness["status"] == "READY"
    assert readiness["ready_to_answer"] is True
    assert readiness["response_mode"] == "answer_from_evidence"
    assert readiness["detail_evidence"]["basis"] == "body_evidence"


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
