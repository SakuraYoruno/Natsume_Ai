from __future__ import annotations

from host_brain.app.observability_persist import persist_life_action_event_memory
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.life_memory.consolidator import LifeMemoryConsolidator
from host_brain.memory.life_memory.store import LifeMemoryStore
from host_brain.persona.surface_memory_recall import build_surface_memory_recall_packet
from host_brain.retrieval.recall_fusion import RecallFusionServiceContract
from host_brain.retrieval.recall_schemas import RecallRouteConfig, RecallRuntimeConfig


class _SemanticLedger:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def list_semantic_memories(self, *, limit: int, offset: int):  # noqa: ANN201
        assert limit > 0
        assert offset == 0
        return (
            [
                {
                    "semantic_id": "sem-low-energy",
                    "summary": "用户低能量时偏好少追问，先给一个小步骤。",
                    "source_memory_ids": ["mem-1", "mem-2"],
                    "distill_strategy": "llm_summary",
                    "confidence": 0.92,
                    "tags": ["preference", "low_energy"],
                    "ts_ms": 1000,
                },
                {
                    "semantic_id": "sem-unrelated",
                    "summary": "用户喜欢芒果和不太甜的奶茶。",
                    "source_memory_ids": ["mem-3"],
                    "confidence": 0.88,
                    "tags": ["preference"],
                    "ts_ms": 900,
                },
            ],
            2,
        )

    def log_retrieval_event(self, row: dict) -> None:
        self.events.append(dict(row))


class _RowsSemanticLedger:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.events: list[dict] = []

    def list_semantic_memories(self, *, limit: int, offset: int):  # noqa: ANN201
        assert limit > 0
        assert offset == 0
        return (self.rows[:limit], len(self.rows))

    def log_retrieval_event(self, row: dict) -> None:
        self.events.append(dict(row))


def _semantic_service(ledger) -> RecallFusionServiceContract:  # noqa: ANN001
    return RecallFusionServiceContract(
        ledger=ledger,  # type: ignore[arg-type]
        runtime_config=RecallRuntimeConfig(
            enabled=True,
            default_route="semantic",
            routes={
                "semantic": RecallRouteConfig(
                    route_id="semantic",
                    store_id="semantic_memory",
                    collection="semantic_memory",
                    top_k=4,
                    score_weight=1.0,
                    node_selector="surface_main_chain_memory_recall",
                )
            },
        ),
        node_id="surface_main_chain_memory_recall",
        use_hybrid=False,
    )


def test_recall_fusion_reads_semantic_memory_as_long_term_route() -> None:
    ledger = _SemanticLedger()
    runtime_config = RecallRuntimeConfig(
        enabled=True,
        default_route="semantic",
        routes={
            "semantic": RecallRouteConfig(
                route_id="semantic",
                store_id="semantic_memory",
                collection="semantic_memory",
                top_k=3,
                score_weight=1.0,
                node_selector="surface_main_chain_memory_recall",
            )
        },
    )
    service = RecallFusionServiceContract(
        ledger=ledger,  # type: ignore[arg-type]
        runtime_config=runtime_config,
        node_id="surface_main_chain_memory_recall",
        use_hybrid=False,
    )

    result = service.recall(
        request_id="req-semantic",
        query="我有点累，下一步怎么办",
        query_embedding=[0.1] * 8,
        target_store_ids=["semantic_memory"],
        ts_ms=2_000,
    )

    assert result.success
    assert result.output["routes"][0]["store_id"] == "semantic_memory"
    assert result.output["candidates"][0]["doc_id"] == "sem-low-energy"
    assert result.output["candidates"][0]["metadata"]["memory_layer"] == "semantic_long_term"
    assert result.output["candidates"][0]["source_ref"] == "ledger://semantic_memory/sem-low-energy"
    assert ledger.events


def test_semantic_recall_exposes_profiled_score_reasons_for_failure_query() -> None:
    ledger = _RowsSemanticLedger(
        [
            {
                "semantic_id": "sem-collection",
                "summary": "经历集合：最近的轻小说推荐记录了 18 个条目。题材线索：恋爱日常、奇幻身份钩子。",
                "source_memory_ids": ["life_memory_collection::col-1"],
                "confidence": 0.94,
                "tags": ["semantic_kind:experience_collection", "experience_collection", "recommendation_collection"],
                "ts_ms": 1_000,
                "distill_strategy": "life_memory_collection_rule_consolidation",
            },
            {
                "semantic_id": "sem-deepseek-failure",
                "summary": "故障经验：DeepSeek 失败；原因 provider timeout；后续优先检查网络和 provider 健康状态。",
                "source_memory_ids": ["life_episode::ep-failure"],
                "confidence": 0.86,
                "tags": ["semantic_kind:failure_lesson", "failure_lesson", "operations"],
                "ts_ms": 900,
                "distill_strategy": "life_episode_rule_consolidation",
            },
        ]
    )

    result = _semantic_service(ledger).recall(
        request_id="req-semantic-failure-profile",
        query="DeepSeek失败原因是什么？",
        query_embedding=[0.1] * 8,
        target_store_ids=["semantic_memory"],
        ts_ms=2_000,
    )

    assert result.success
    top = result.output["candidates"][0]
    boost = top["metadata"]["score_boost"]
    assert top["doc_id"] == "sem-deepseek-failure"
    assert top["metadata"]["semantic_kind"] == "failure_lesson"
    assert boost["query_profile"] == "failure_debug"
    assert boost["semantic_kind_boost"] > 0
    assert boost["lexical_score"] > 0
    assert "semantic_kind:failure_lesson" in boost["matched_tags"]


def test_semantic_recall_prefers_preference_over_recommendation_collection_for_format_query() -> None:
    ledger = _RowsSemanticLedger(
        [
            {
                "semantic_id": "sem-collection",
                "summary": "经历集合：最近的轻小说推荐发过 15 条推荐，去掉重复标题主要是 10 本。",
                "source_memory_ids": ["life_memory_collection::col-1"],
                "confidence": 0.94,
                "tags": ["semantic_kind:experience_collection", "experience_collection", "recommendation_collection"],
                "ts_ms": 1_100,
                "distill_strategy": "life_memory_collection_rule_consolidation",
            },
            {
                "semantic_id": "sem-format-pref",
                "summary": "用户偏好：以后推荐轻小说时，不要拟人化发送；前面的总览完整一段，后面各个卡片各一段；每本简介后应该换行。",
                "source_memory_ids": ["life_episode::ep-format-pref"],
                "confidence": 0.88,
                "tags": ["semantic_kind:user_preference", "user_preference", "preference"],
                "ts_ms": 1_000,
                "distill_strategy": "life_episode_rule_consolidation",
            },
        ]
    )

    result = _semantic_service(ledger).recall(
        request_id="req-semantic-format-preference-profile",
        query="还记得我说推荐格式要怎样吗？",
        query_embedding=[0.1] * 8,
        target_store_ids=["semantic_memory"],
        ts_ms=2_000,
    )

    assert result.success
    top = result.output["candidates"][0]
    assert top["doc_id"] == "sem-format-pref"
    assert top["metadata"]["semantic_kind"] == "user_preference"
    assert top["metadata"]["score_boost"]["query_profile"] == "preference"
    assert top["metadata"]["score_boost"]["semantic_kind_boost"] > 0


def test_semantic_recall_profiles_llm_routing_preference_query() -> None:
    ledger = _RowsSemanticLedger(
        [
            {
                "semantic_id": "sem-format-pref",
                "summary": "用户偏好：以后推荐轻小说时，不要拟人化发送；每本简介后应该换行。",
                "source_memory_ids": ["life_episode::ep-format-pref"],
                "confidence": 0.9,
                "tags": ["semantic_kind:user_preference", "user_preference", "preference"],
                "ts_ms": 1_100,
                "distill_strategy": "life_episode_rule_consolidation",
            },
            {
                "semantic_id": "sem-llm-routing-pref",
                "summary": "用户偏好：Thalamus LLM 用模型池，不要用 Codex；模型池当前优先使用 DeepSeek，DeepSeek 不可用再用 Gemini；不要用 Pro。",
                "source_memory_ids": ["life_episode::ep-llm-routing-pref"],
                "confidence": 0.88,
                "tags": ["semantic_kind:user_preference", "user_preference", "preference"],
                "ts_ms": 1_000,
                "distill_strategy": "life_episode_rule_consolidation",
            },
        ]
    )

    result = _semantic_service(ledger).recall(
        request_id="req-semantic-llm-routing-preference-profile",
        query="Thalamus LLM 应该用什么？",
        query_embedding=[0.1] * 8,
        target_store_ids=["semantic_memory"],
        ts_ms=2_000,
    )

    assert result.success
    top = result.output["candidates"][0]
    assert top["doc_id"] == "sem-llm-routing-pref"
    assert top["metadata"]["score_boost"]["query_profile"] == "llm_routing_preference"


def test_semantic_recall_profiles_self_change_query() -> None:
    ledger = _RowsSemanticLedger(
        [
            {
                "semantic_id": "sem-pref",
                "summary": "用户偏好：以后推荐轻小说时总览一段、每本一卡片。",
                "source_memory_ids": ["life_episode::ep-pref"],
                "confidence": 0.88,
                "tags": ["semantic_kind:user_preference", "user_preference"],
                "ts_ms": 1_100,
                "distill_strategy": "life_episode_rule_consolidation",
            },
            {
                "semantic_id": "sem-memory-system",
                "summary": "自我改变：记忆系统改成以 Episode 为唯一生命经历对象，并通过 Evidence 追溯证据。",
                "source_memory_ids": ["life_episode::ep-self-change"],
                "confidence": 0.84,
                "tags": ["semantic_kind:self_change", "self_change", "memory_family:self_model"],
                "ts_ms": 1_000,
                "distill_strategy": "life_episode_rule_consolidation",
            },
        ]
    )

    result = _semantic_service(ledger).recall(
        request_id="req-semantic-self-change-profile",
        query="记忆系统之前改成什么了？",
        query_embedding=[0.1] * 8,
        target_store_ids=["semantic_memory"],
        ts_ms=2_000,
    )

    assert result.success
    top = result.output["candidates"][0]
    boost = top["metadata"]["score_boost"]
    assert top["doc_id"] == "sem-memory-system"
    assert top["metadata"]["semantic_kind"] == "self_change"
    assert boost["query_profile"] == "self_change"
    assert boost["semantic_kind_boost"] > 0
    assert "semantic_kind:self_change" in boost["matched_tags"]


def test_semantic_recall_prefers_open_commitments_for_open_loop_query() -> None:
    ledger = _RowsSemanticLedger(
        [
            {
                "semantic_id": "sem-closed-loop",
                "summary": "承诺完成：记忆系统的召回修复已经完成，关闭这个待办。",
                "source_memory_ids": ["life_episode::ep-closed"],
                "confidence": 0.9,
                "tags": ["semantic_kind:commitment", "commitment", "open_loop", "open_loop:closed"],
                "ts_ms": 1_100,
                "distill_strategy": "life_episode_rule_consolidation",
            },
            {
                "semantic_id": "sem-open-loop",
                "summary": "承诺和待办：后续要把推荐追问补证链路修好，记成待办。",
                "source_memory_ids": ["life_episode::ep-open"],
                "confidence": 0.82,
                "tags": ["semantic_kind:commitment", "commitment", "open_loop", "open_loop:open"],
                "ts_ms": 1_000,
                "distill_strategy": "life_episode_rule_consolidation",
            },
        ]
    )

    result = _semantic_service(ledger).recall(
        request_id="req-semantic-open-loop-profile",
        query="还有哪些未完成的待办要继续做？",
        query_embedding=[0.1] * 8,
        target_store_ids=["semantic_memory"],
        ts_ms=2_000,
    )

    assert result.success
    top = result.output["candidates"][0]
    assert top["doc_id"] == "sem-open-loop"
    assert top["metadata"]["score_boost"]["query_profile"] == "commitment"
    assert top["metadata"]["score_boost"]["semantic_kind_boost"] > 0


def test_semantic_recall_prefers_closed_commitments_for_done_query() -> None:
    ledger = _RowsSemanticLedger(
        [
            {
                "semantic_id": "sem-open-loop",
                "summary": "承诺和待办：后续要把推荐追问补证链路修好，记成待办。",
                "source_memory_ids": ["life_episode::ep-open"],
                "confidence": 0.86,
                "tags": ["semantic_kind:commitment", "commitment", "open_loop", "open_loop:open"],
                "ts_ms": 1_000,
                "distill_strategy": "life_episode_rule_consolidation",
            },
            {
                "semantic_id": "sem-closed-loop",
                "summary": "承诺完成：记忆系统的召回修复已经完成，关闭这个待办。",
                "source_memory_ids": ["life_episode::ep-closed"],
                "confidence": 0.82,
                "tags": ["semantic_kind:commitment", "commitment", "open_loop", "open_loop:closed"],
                "ts_ms": 900,
                "distill_strategy": "life_episode_rule_consolidation",
            },
        ]
    )

    result = _semantic_service(ledger).recall(
        request_id="req-semantic-closed-loop-profile",
        query="已完成的待办有哪些？",
        query_embedding=[0.1] * 8,
        target_store_ids=["semantic_memory"],
        ts_ms=2_000,
    )

    assert result.success
    top = result.output["candidates"][0]
    assert top["doc_id"] == "sem-closed-loop"
    assert top["metadata"]["score_boost"]["query_profile"] == "commitment_closed"
    assert top["metadata"]["score_boost"]["semantic_kind_boost"] > 0


def test_semantic_recall_profiles_relationship_feedback_query() -> None:
    ledger = _RowsSemanticLedger(
        [
            {
                "semantic_id": "sem-collection",
                "summary": "经历集合：最近的轻小说推荐记录了 18 个条目。",
                "source_memory_ids": ["life_memory_collection::col-1"],
                "confidence": 0.9,
                "tags": ["semantic_kind:experience_collection", "experience_collection"],
                "ts_ms": 1_100,
                "distill_strategy": "life_memory_collection_rule_consolidation",
            },
            {
                "semantic_id": "sem-feedback-natural",
                "summary": "关系反馈：用户强烈反馈：文本质量不能生硬得不像人话；回答要像正常聊天，避免模板腔、台词腔和生硬的证据条目话术。",
                "source_memory_ids": ["life_episode::ep-feedback"],
                "confidence": 0.84,
                "tags": ["semantic_kind:relationship_feedback", "relationship_feedback", "response_quality"],
                "ts_ms": 1_000,
                "distill_strategy": "life_episode_rule_consolidation",
            },
            {
                "semantic_id": "sem-feedback-recall-quality",
                "summary": "关系反馈：用户强烈反馈：记忆召回和判断必须回到证据，不能把没核实的内容说成事实或胡说八道。",
                "source_memory_ids": ["life_episode::ep-feedback-recall"],
                "confidence": 0.96,
                "tags": ["semantic_kind:relationship_feedback", "relationship_feedback", "response_quality"],
                "ts_ms": 1_900,
                "distill_strategy": "life_episode_rule_consolidation",
            },
        ]
    )

    result = _semantic_service(ledger).recall(
        request_id="req-semantic-relationship-feedback-profile",
        query="我之前说文本质量不像人话，是哪里的问题？",
        query_embedding=[0.1] * 8,
        target_store_ids=["semantic_memory"],
        ts_ms=2_000,
    )

    assert result.success
    top = result.output["candidates"][0]
    assert top["doc_id"] == "sem-feedback-natural"
    assert top["metadata"]["semantic_kind"] == "relationship_feedback"
    assert top["metadata"]["score_boost"]["query_profile"] == "relationship_feedback"
    assert top["metadata"]["score_boost"]["semantic_kind_boost"] > 0


def test_semantic_recall_profiles_action_pattern_query() -> None:
    ledger = _RowsSemanticLedger(
        [
            {
                "semantic_id": "sem-collection",
                "summary": "经历集合：最近的轻小说推荐记录了 12 本：恋爱日常 A、皇女皇帝 B。",
                "source_memory_ids": ["life_memory_collection::col-1"],
                "confidence": 0.94,
                "tags": ["semantic_kind:experience_collection", "experience_collection", "recommendation_collection"],
                "ts_ms": 1_500,
                "distill_strategy": "life_memory_collection_rule_consolidation",
            },
            {
                "semantic_id": "sem-action-pattern",
                "summary": "主动行动模式：在轻小说推荐追问里，她会回去打开详情页或正文再比较哪本更值得看；判断时主要看：开篇是不是已经进入故事、对白和人物互动。",
                "source_memory_ids": ["life_episode::ep-deep-review-1", "life_episode::ep-deep-review-2"],
                "confidence": 0.82,
                "tags": ["semantic_kind:action_pattern", "action_pattern", "deep_review", "domain:lightnovel_recommendation"],
                "ts_ms": 1_200,
                "distill_strategy": "life_action_pattern_rule_consolidation",
            },
        ]
    )

    result = _semantic_service(ledger).recall(
        request_id="req-semantic-action-pattern-profile",
        query="你之前会回去翻正文再判断哪本轻小说好看吗？",
        query_embedding=[0.1] * 8,
        target_store_ids=["semantic_memory"],
        ts_ms=2_000,
    )

    assert result.success
    top = result.output["candidates"][0]
    assert top["doc_id"] == "sem-action-pattern"
    assert top["metadata"]["semantic_kind"] == "action_pattern"
    assert top["metadata"]["score_boost"]["query_profile"] == "action_pattern"
    assert top["metadata"]["score_boost"]["semantic_kind_boost"] > 0


def test_life_episode_semantic_distillation_reaches_surface_memory_packet() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        written_episode = store.write_chat_episode(
            request_id="req-semantic-life-pref",
            session_id="qq_dm_user_1",
            person_id="user-1",
            user_message="以后轻小说推荐按总览一段、每本书一卡片分段，简介后要换行。",
            assistant_reply="记住了，之后推荐会按这个格式发。",
            ts_ms=1_800_000_000_000,
            metadata={"user_importance": 0.7},
        )

        distill_result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=12,
            max_records=6,
            ts_ms=1_800_000_000_100,
        )
        assert distill_result["written_count"] == 1

        runtime_config = RecallRuntimeConfig(
            enabled=True,
            default_route="semantic",
            routes={
                "semantic": RecallRouteConfig(
                    route_id="semantic",
                    store_id="semantic_memory",
                    collection="semantic_memory",
                    top_k=3,
                    score_weight=1.0,
                    node_selector="surface_main_chain_memory_recall",
                )
            },
        )
        service = RecallFusionServiceContract(
            ledger=ledger,
            runtime_config=runtime_config,
            node_id="surface_main_chain_memory_recall",
            use_hybrid=False,
        )

        recall_result = service.recall(
            request_id="req-semantic-life-recall",
            query="以后轻小说推荐怎么发",
            query_embedding=[0.2] * 8,
            target_store_ids=["semantic_memory"],
            ts_ms=1_800_000_000_200,
        )
        packet = build_surface_memory_recall_packet(recall_result, max_facts=3)

        assert recall_result.success
        assert recall_result.output["candidates"]
        top = recall_result.output["candidates"][0]
        top_metadata = top["metadata"]
        assert top_metadata["source_memory_ids"] == [
            f"life_episode::{written_episode['episode_id']}"
        ]
        assert top_metadata["semantic_provenance_status"] == "resolved"
        assert top_metadata["evidence_refs_primary"] == "chat://request/req-semantic-life-pref#user"
        assert top["evidence_refs"] == "chat://request/req-semantic-life-pref#user"
        assert "chat://request/req-semantic-life-pref#assistant" in top_metadata["semantic_evidence_refs"]
        assert f"life_episode::{written_episode['episode_id']}" in top_metadata["semantic_source_refs"]
        rendered = "\n".join(packet.facts)
        assert "长期事实：以后轻小说推荐" in rendered
        assert "用户偏好" not in rendered
        assert "总览" in rendered
        assert "卡片" in rendered
        assert "换行" in rendered
        assert packet.evidence_refs[0] == "chat://request/req-semantic-life-pref#user"
        assert packet.source_refs[0].startswith("ledger://semantic_memory/")
        assert "长期事实只补稳定偏好和关系事实，不替代当前用户句子" in packet.style_anchor
        assert dict(packet.trace or {})["semantic_usage"]["mode"] == "answer_memory_lookup"
    finally:
        ledger.close()


def test_entity_semantic_recall_resolves_mentions_to_lived_evidence() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        persist_life_action_event_memory(
            event_source="codex_production",
            action_kind="lightnovel_recommendation_delivery",
            action_id="job-semantic-entity-source",
            actor_id="natsume",
            request_id="req-semantic-entity-source",
            intent_id="agent_lightnovel_recommender",
            person_id="owner",
            session_id="qq_dm_owner",
            status="COMPLETED",
            action_summary="我给 qq_dm_owner 推荐了 1 本轻小说：与神在河岸。",
            ts_ms=1_800_000_000_000,
            evidence_refs=["gateway://message/entity-source/card/1"],
            life_memory_store=store,
            episodic_store=None,
            metadata={
                "platform": "qq",
                "conversation_id": "qq_dm_owner",
                "selected_recommendations": [
                    {
                        "title": "与神在河岸",
                        "selection_reason": "奇幻身份",
                        "intro_snippet": "神明和河岸相遇的奇幻身份线比较明确。",
                        "detail_url": "https://www.lightnovel.fun/cn/detail/entity-source-river",
                    }
                ],
            },
        )
        entity = ledger.life_memory_entity[0]
        collection = ledger.life_memory_collection[0]
        item = ledger.life_memory_collection_item[0]
        mention = ledger.life_memory_entity_mention[0]
        ledger.log_semantic_memory(
            {
                "semantic_id": "sem-entity-river",
                "summary": "实体经历：与神在河岸曾被推荐给用户；题材线索是奇幻身份和神明相遇。",
                "source_memory_ids": [f"life_memory_entity::{entity['entity_id']}"],
                "source_hash": "entity-river-source-hash",
                "distill_strategy": "life_memory_entity_rule_consolidation",
                "confidence": 0.93,
                "tags": [
                    "semantic_kind:experience_entity",
                    "experience_entity",
                    "domain:lightnovel_recommendation",
                ],
                "ts_ms": 1_800_000_000_100,
            }
        )

        recall_result = _semantic_service(ledger).recall(
            request_id="req-semantic-entity-source-recall",
            query="与神在河岸之前是不是推荐过？",
            query_embedding=[0.2] * 8,
            target_store_ids=["semantic_memory"],
            ts_ms=1_800_000_000_200,
        )
        packet = build_surface_memory_recall_packet(recall_result, max_facts=3)

        assert recall_result.success
        top = recall_result.output["candidates"][0]
        metadata = top["metadata"]
        assert metadata["semantic_provenance_status"] == "resolved"
        assert metadata["semantic_entity_ids"] == [entity["entity_id"]]
        assert metadata["semantic_collection_ids"] == [collection["collection_id"]]
        assert metadata["semantic_episode_ids"] == [item["episode_id"]]
        assert metadata["evidence_refs_primary"] == "action://event/job-semantic-entity-source"
        assert top["evidence_refs"] == "action://event/job-semantic-entity-source"
        assert f"life_memory_entity::{entity['entity_id']}" in metadata["semantic_source_refs"]
        assert f"life_memory_collection::{collection['collection_id']}" in metadata["semantic_source_refs"]
        assert f"life_episode::{item['episode_id']}" in metadata["semantic_source_refs"]
        assert mention["source_ref"] in metadata["semantic_source_refs"]
        assert mention["evidence_ref"] in metadata["semantic_evidence_refs"]
        assert packet.evidence_refs[0] == "action://event/job-semantic-entity-source"
        assert "与神在河岸" in "\n".join(packet.facts)
    finally:
        ledger.close()


def test_correction_semantic_recall_resolves_correction_record_provenance() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        wrong = store.write_chat_episode(
            request_id="req-semantic-correction-wrong",
            session_id="qq_dm_owner",
            person_id="owner",
            user_message="刚才那三本里你选哪本？",
            assistant_reply="我选封面有猫的那本。",
            ts_ms=1_800_000_000_000,
        )
        correction = store.correct_episode(
            episode_id=wrong["episode_id"],
            correction_kind="suppression",
            reason_code="USER_CORRECTED_HALLUCINATION",
            detail="用户指出没有封面有猫这本。",
            suppress=True,
            ts_ms=1_800_000_000_100,
        )
        ledger.log_semantic_memory(
            {
                "semantic_id": "sem-correction-cat-cover",
                "summary": "纠错记忆：用户纠正过错误记忆：没有“封面有猫”这本；后续不能把这个说成事实。",
                "source_memory_ids": [f"life_memory_correction::{correction['correction_id']}"],
                "source_hash": "correction-cat-cover-source-hash",
                "distill_strategy": "life_memory_correction_rule_consolidation",
                "confidence": 0.9,
                "tags": ["semantic_kind:correction", "correction", "memory_correction"],
                "ts_ms": 1_800_000_000_200,
            }
        )

        recall_result = _semantic_service(ledger).recall(
            request_id="req-semantic-correction-recall",
            query="有没有纠正过封面有猫这本？",
            query_embedding=[0.2] * 8,
            target_store_ids=["semantic_memory"],
            ts_ms=1_800_000_000_300,
        )

        assert recall_result.success
        top = recall_result.output["candidates"][0]
        metadata = top["metadata"]
        assert top["doc_id"] == "sem-correction-cat-cover"
        assert metadata["semantic_provenance_status"] == "resolved"
        assert metadata["semantic_correction_ids"] == [correction["correction_id"]]
        assert wrong["episode_id"] in metadata["semantic_episode_ids"]
        assert f"life_memory_correction::{correction['correction_id']}" in metadata["semantic_source_refs"]
        assert f"life_memory_correction://{correction['correction_id']}" in metadata["semantic_evidence_refs"]
        assert "chat://request/req-semantic-correction-wrong#user" in metadata["semantic_evidence_refs"]
    finally:
        ledger.close()


def test_collection_semantic_distillation_exposes_theme_facets_to_recall() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        persist_life_action_event_memory(
            event_source="codex_production",
            action_kind="lightnovel_recommendation_delivery",
            action_id="job-semantic-collection-theme",
            actor_id="natsume",
            request_id="req-semantic-collection-theme",
            intent_id="agent_lightnovel_recommender",
            person_id="owner",
            session_id="qq_dm_owner",
            status="COMPLETED",
            action_summary="我给 qq_dm_owner 推荐了 3 本轻小说。",
            ts_ms=1_800_000_000_000,
            evidence_refs=["gateway://message/theme/overview"],
            life_memory_store=store,
            episodic_store=None,
            metadata={
                "platform": "qq",
                "conversation_id": "qq_dm_owner",
                "selected_recommendations": [
                    {
                        "title": "恋爱轻小说 A",
                        "selection_reason": "恋爱日常",
                        "intro_snippet": "两个人的关系展开比较轻快。",
                    },
                    {
                        "title": "奇幻轻小说 B",
                        "selection_reason": "奇幻身份",
                        "intro_snippet": "身份钩子明显。",
                    },
                    {
                        "title": "皇女轻小说 C",
                        "selection_reason": "皇女皇帝线",
                        "intro_snippet": "皇女结婚和成为皇帝的线很直给。",
                    },
                ],
            },
        )
        distill_result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=12,
            max_records=6,
            ts_ms=1_800_000_000_100,
        )
        runtime_config = RecallRuntimeConfig(
            enabled=True,
            default_route="semantic",
            routes={
                "semantic": RecallRouteConfig(
                    route_id="semantic",
                    store_id="semantic_memory",
                    collection="semantic_memory",
                    top_k=3,
                    score_weight=1.0,
                    node_selector="surface_main_chain_memory_recall",
                )
            },
        )
        service = RecallFusionServiceContract(
            ledger=ledger,
            runtime_config=runtime_config,
            node_id="surface_main_chain_memory_recall",
            use_hybrid=False,
        )

        recall_result = service.recall(
            request_id="req-semantic-collection-theme-recall",
            query="最近推荐里皇女皇帝线有哪些",
            query_embedding=[0.2] * 8,
            target_store_ids=["semantic_memory"],
            ts_ms=1_800_000_000_200,
        )

        assert distill_result["written_count"] >= 1
        assert recall_result.success
        assert recall_result.output["candidates"]
        top = recall_result.output["candidates"][0]
        assert top["metadata"]["distill_strategy"] == "life_memory_collection_rule_consolidation"
        assert "题材线索" in top["snippet"]
        assert "皇女皇帝线" in top["snippet"]
        assert "theme:皇女皇帝线" in top["metadata"]["tags"]
        assert any(str(source).startswith("life_memory_collection::") for source in top["metadata"]["source_memory_ids"])
        assert top["metadata"]["semantic_provenance_status"] == "resolved"
        assert top["metadata"]["semantic_evidence_refs"] == ["action://event/job-semantic-collection-theme"]
        assert top["evidence_refs"] == "action://event/job-semantic-collection-theme"
        assert any(str(source).startswith("life_episode::") for source in top["metadata"]["semantic_source_refs"])
    finally:
        ledger.close()
