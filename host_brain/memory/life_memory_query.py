"""Query planning helpers for episode-first life memory recall."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


TEMPORAL_CURRENT = "current"
TEMPORAL_TODAY = "today"
TEMPORAL_YESTERDAY = "yesterday"
TEMPORAL_DAY_BEFORE_YESTERDAY = "day_before_yesterday"
TEMPORAL_RECENT_DAYS = "recent_days"
TEMPORAL_ALL_TIME = "all_time"
TEMPORAL_UNSPECIFIED = "unspecified"

QUERY_FRESH_TOPIC = "fresh_topic"
QUERY_ANAPHORIC_SELECTION = "anaphoric_selection"
QUERY_DATE_LOOKUP = "date_lookup"
QUERY_COLLECTION_SUMMARY = "collection_summary"
QUERY_EVENT_LOOKUP = "event_lookup"
QUERY_EXPLICIT_MEMORY = "explicit_memory"
QUERY_PREFERENCE_LOOKUP = "preference_lookup"
QUERY_OPEN_LOOP = "open_loop"
QUERY_FAILURE_DEBUG = "failure_debug"
QUERY_CORRECTION = "correction"
QUERY_SELF_CHANGE = "self_change"

CONTRACT_NO_RECALL = "no_recall"
CONTRACT_FAILURE_DEBUG = "failure_debug"
CONTRACT_OPEN_LOOP = "open_loop"
CONTRACT_COLLECTION_DATE = "collection_date"
CONTRACT_COLLECTION_RECENT = "collection_recent"
CONTRACT_ANAPHORIC_SELECTION = "anaphoric_selection"
CONTRACT_EVENT_LOOKUP = "event_lookup"
CONTRACT_CORRECTION = "correction"
CONTRACT_EXPLICIT_MEMORY = "explicit_memory"
CONTRACT_PREFERENCE = "preference"
CONTRACT_SELF_CHANGE = "self_change"

DOMAIN_RECOMMENDATION = "recommendation"
DOMAIN_LIGHTNOVEL_RECOMMENDATION = "lightnovel_recommendation"
DOMAIN_TASK = "task"
DOMAIN_FAILURE = "failure"
DOMAIN_PREFERENCE = "preference"
DOMAIN_SELF_CHANGE = "self_change"
DOMAIN_ACTION = "action"


@dataclass(frozen=True)
class RecallIntentContract:
    """Memory-routing contract for one class of recall query."""

    contract_id: str
    primary_memory_layer: str = ""
    expected_memory_layers: tuple[str, ...] = ()
    validation_requirements: tuple[str, ...] = ()
    answer_obligations: tuple[str, ...] = ()


RECALL_INTENT_CONTRACTS: dict[str, RecallIntentContract] = {
    CONTRACT_FAILURE_DEBUG: RecallIntentContract(
        contract_id=CONTRACT_FAILURE_DEBUG,
        primary_memory_layer="life_episode",
        expected_memory_layers=("life_episode", "semantic_memory", "sqlite_ledger", "episodic_vector"),
        validation_requirements=("error_episode_or_semantic_hit", "source_ref_present"),
        answer_obligations=("explain_failure_from_recorded_result", "do_not_invent_root_cause"),
    ),
    CONTRACT_OPEN_LOOP: RecallIntentContract(
        contract_id=CONTRACT_OPEN_LOOP,
        primary_memory_layer="life_episode",
        expected_memory_layers=("life_episode", "semantic_memory", "sqlite_ledger", "episodic_vector", "neo4j_bond"),
        validation_requirements=("trusted_memory_hit", "source_ref_present"),
        answer_obligations=("state_open_loop_status", "separate_done_from_pending"),
    ),
    CONTRACT_COLLECTION_DATE: RecallIntentContract(
        contract_id=CONTRACT_COLLECTION_DATE,
        primary_memory_layer="life_memory_collection",
        expected_memory_layers=("life_memory_collection",),
        validation_requirements=("scope_match", "collection_items_present", "evidence_refs_present"),
        answer_obligations=("keep_requested_date_scope", "use_collection_items_not_chat_echo"),
    ),
    CONTRACT_COLLECTION_RECENT: RecallIntentContract(
        contract_id=CONTRACT_COLLECTION_RECENT,
        primary_memory_layer="life_memory_collection",
        expected_memory_layers=("life_memory_collection",),
        validation_requirements=("scope_match", "collection_items_present", "evidence_refs_present"),
        answer_obligations=("summarize_cross_day_collection", "preserve_item_identity"),
    ),
    CONTRACT_ANAPHORIC_SELECTION: RecallIntentContract(
        contract_id=CONTRACT_ANAPHORIC_SELECTION,
        primary_memory_layer="life_memory_collection",
        expected_memory_layers=("life_memory_collection", "life_episode"),
        validation_requirements=("collection_or_episode_hit", "evidence_refs_present", "no_self_echo"),
        answer_obligations=("ground_selection_in_recalled_items", "do_not_invent_unread_content"),
    ),
    CONTRACT_EVENT_LOOKUP: RecallIntentContract(
        contract_id=CONTRACT_EVENT_LOOKUP,
        primary_memory_layer="life_episode",
        expected_memory_layers=("life_episode", "life_memory_collection", "semantic_memory", "sqlite_ledger", "episodic_vector"),
        validation_requirements=("event_time_scope_match", "event_or_collection_hit", "source_ref_present"),
        answer_obligations=("answer_with_action_result", "keep_action_and_reason_together"),
    ),
    CONTRACT_CORRECTION: RecallIntentContract(
        contract_id=CONTRACT_CORRECTION,
        primary_memory_layer="life_episode",
        expected_memory_layers=("life_episode", "semantic_memory", "sqlite_ledger", "episodic_vector"),
        validation_requirements=("correction_state_checked", "suppressed_not_fact"),
        answer_obligations=("prefer_corrected_version", "do_not_repeat_suppressed_fact"),
    ),
    CONTRACT_EXPLICIT_MEMORY: RecallIntentContract(
        contract_id=CONTRACT_EXPLICIT_MEMORY,
        primary_memory_layer="life_episode",
        expected_memory_layers=("life_episode", "semantic_memory", "sqlite_ledger", "episodic_vector", "neo4j_bond"),
        validation_requirements=("trusted_memory_hit", "source_ref_present"),
        answer_obligations=("answer_from_trusted_memory", "say_when_memory_is_missing"),
    ),
    CONTRACT_PREFERENCE: RecallIntentContract(
        contract_id=CONTRACT_PREFERENCE,
        primary_memory_layer="semantic_memory",
        expected_memory_layers=("semantic_memory", "life_episode", "sqlite_ledger", "episodic_vector", "neo4j_bond"),
        validation_requirements=("trusted_memory_hit", "source_ref_present"),
        answer_obligations=("state_preference_as_user_rule", "keep_source_episode_traceable"),
    ),
    CONTRACT_SELF_CHANGE: RecallIntentContract(
        contract_id=CONTRACT_SELF_CHANGE,
        primary_memory_layer="life_episode",
        expected_memory_layers=("life_episode", "semantic_memory", "sqlite_ledger", "episodic_vector", "neo4j_bond"),
        validation_requirements=("trusted_memory_hit", "source_ref_present"),
        answer_obligations=("state_self_change_and_current_effect", "do_not_hide_correction_history"),
    ),
    CONTRACT_NO_RECALL: RecallIntentContract(contract_id=CONTRACT_NO_RECALL),
}


_ANAPHORIC_SELECTION_RE = re.compile(
    r"(?:哪(?:本|部|个|一(?:本|部|个))|哪个|哪部|哪本|"
    r"(?:喜欢|想看|先看|推荐|选|挑|好看).{0,8}哪|"
    r"这(?:三|几|十几)?(?:本|部)|这些)"
)
_RECENT_COLLECTION_RE = re.compile(r"(?:最近|这几天|这两天|这三天|这周|近几天|这段时间|过去几天|过去一周|十几本|几本)")
_DATE_LOOKUP_RE = re.compile(r"(?:今天|今日|昨天|昨晚|前天|上次|之前|以前|过去|这周)")
_BOOK_DOMAIN_RE = re.compile(r"(?:轻小说|小说|书籍|书|epub|lightnovel)", re.IGNORECASE)
_RECOMMENDATION_RE = re.compile(r"(?:推荐|推送|发给我|发过|选书|书单)")
_ITEM_COUNT_RE = re.compile(r"(?:这|那|昨天|前天|今天|最近|几天)?.{0,4}(?:三本|几本|十几本|这些|那些|几个|几部|三部)")
_EVENT_LOOKUP_RE = re.compile(
    r"(?:"
    r"做(?:了|过)?什么|干(?:了|过)?什么|做过哪些|"
    r"发(?:了|过)?什么|推送(?:了|过)?什么|回复(?:了|过)?什么|"
    r"执行(?:了|过)?什么|执行情况|跑(?:了|过)?什么|调用(?:了|过)?什么|处理(?:了|过)?什么|"
    r"后台任务|调度任务|任务执行|有哪些(?:动作|任务|事情|经历)"
    r")"
)


@dataclass(frozen=True)
class LifeMemoryQueryPlan:
    """Structured answer to: should memory be recalled, and from where."""

    should_recall: bool
    query_kind: str = QUERY_FRESH_TOPIC
    temporal_scope: str = TEMPORAL_UNSPECIFIED
    target_domains: tuple[str, ...] = ()
    target_store_ids: tuple[str, ...] = ()
    time_lookback_days: int | None = None
    max_facts: int = 4
    max_chars_per_fact: int = 160
    reason: str = "no_memory_recall_needed"
    validation_requirements: tuple[str, ...] = ()
    recall_contract_id: str = CONTRACT_NO_RECALL
    primary_memory_layer: str = ""
    expected_memory_layers: tuple[str, ...] = ()
    min_collection_items: int = 0
    answer_obligations: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "should_recall": bool(self.should_recall),
            "query_kind": self.query_kind,
            "temporal_scope": self.temporal_scope,
            "target_domains": list(self.target_domains),
            "target_store_ids": list(self.target_store_ids),
            "time_lookback_days": self.time_lookback_days,
            "max_facts": int(self.max_facts),
            "max_chars_per_fact": int(self.max_chars_per_fact),
            "reason": self.reason,
            "validation_requirements": list(self.validation_requirements),
            "recall_contract_id": self.recall_contract_id,
            "primary_memory_layer": self.primary_memory_layer,
            "expected_memory_layers": list(self.expected_memory_layers),
            "min_collection_items": int(self.min_collection_items or 0),
            "answer_obligations": list(self.answer_obligations),
        }


def plan_life_memory_query(text: Any, *, task_mode: str | None = None) -> LifeMemoryQueryPlan:
    message = _clean_text(text)
    if not message:
        return LifeMemoryQueryPlan(should_recall=False, reason="empty_query")

    temporal_scope = _infer_temporal_scope(message)
    domains = _infer_domains(message)
    anaphoric_selection = bool(_ANAPHORIC_SELECTION_RE.search(message))
    collection_like = _looks_like_collection_query(message, domains=domains, temporal_scope=temporal_scope)
    event_lookup = _looks_like_event_lookup_query(message, temporal_scope=temporal_scope)
    min_collection_items = _minimum_collection_items(message)
    explicit_memory = any(token in message for token in ("还记得", "记得吗", "记不记得", "记了什么", "让你记"))
    failure_debug = any(token in message for token in ("失败原因", "为什么失败", "连不上", "异常原因", "被占用"))
    if not failure_debug and any(token in message for token in ("报错", "异常")):
        failure_debug = temporal_scope != TEMPORAL_UNSPECIFIED or any(token in message for token in ("上次", "之前", "刚才", "原因"))
    correction = any(token in message for token in ("不对", "错了", "纠正", "不是这个意思", "你记错"))
    preference = any(token in message for token in ("我喜欢", "我不喜欢", "偏好", "以后", "不要", "别再", "必须"))
    open_loop = any(token in message for token in ("待办", "承诺", "说好", "继续刚才", "还没做", "完成了吗", "跑完了吗"))
    self_change = any(token in message for token in ("模型路由", "记忆系统", "能力", "策略", "开关", "改过什么"))

    if failure_debug:
        contract = RECALL_INTENT_CONTRACTS[CONTRACT_FAILURE_DEBUG]
        return LifeMemoryQueryPlan(
            should_recall=True,
            query_kind=QUERY_FAILURE_DEBUG,
            temporal_scope=temporal_scope,
            target_domains=tuple(dict.fromkeys([*domains, DOMAIN_FAILURE])),
            target_store_ids=("life_episode", "semantic_memory", "episodic_vector", "sqlite_ledger"),
            time_lookback_days=_lookback_for_scope(temporal_scope, default=14),
            max_facts=4,
            max_chars_per_fact=220,
            reason="failure_debug_recall",
            validation_requirements=contract.validation_requirements,
            recall_contract_id=contract.contract_id,
            primary_memory_layer=contract.primary_memory_layer,
            expected_memory_layers=contract.expected_memory_layers,
            answer_obligations=contract.answer_obligations,
        )

    if open_loop:
        contract = RECALL_INTENT_CONTRACTS[CONTRACT_OPEN_LOOP]
        return LifeMemoryQueryPlan(
            should_recall=True,
            query_kind=QUERY_OPEN_LOOP,
            temporal_scope=temporal_scope,
            target_domains=domains,
            target_store_ids=("life_episode", "semantic_memory", "episodic_vector", "sqlite_ledger", "neo4j_bond"),
            time_lookback_days=_lookback_for_scope(temporal_scope, default=None),
            max_facts=4,
            max_chars_per_fact=180,
            reason="explicit_memory_recall",
            validation_requirements=contract.validation_requirements,
            recall_contract_id=contract.contract_id,
            primary_memory_layer=contract.primary_memory_layer,
            expected_memory_layers=contract.expected_memory_layers,
            answer_obligations=contract.answer_obligations,
        )

    if collection_like and temporal_scope not in {TEMPORAL_CURRENT, TEMPORAL_UNSPECIFIED}:
        scope = temporal_scope
        contract_id = (
            CONTRACT_COLLECTION_DATE
            if temporal_scope in {TEMPORAL_TODAY, TEMPORAL_YESTERDAY, TEMPORAL_DAY_BEFORE_YESTERDAY}
            else CONTRACT_COLLECTION_RECENT
        )
        contract = RECALL_INTENT_CONTRACTS[contract_id]
        return LifeMemoryQueryPlan(
            should_recall=True,
            query_kind=QUERY_DATE_LOOKUP if temporal_scope in {TEMPORAL_TODAY, TEMPORAL_YESTERDAY, TEMPORAL_DAY_BEFORE_YESTERDAY} else QUERY_COLLECTION_SUMMARY,
            temporal_scope=scope,
            target_domains=domains,
            target_store_ids=("life_memory_collection", "life_episode", "semantic_memory", "episodic_vector", "sqlite_ledger"),
            time_lookback_days=_lookback_for_scope(scope, default=7),
            max_facts=6,
            max_chars_per_fact=900,
            reason="collection_memory_recall",
            validation_requirements=_collection_validation_requirements(contract, min_collection_items),
            recall_contract_id=contract.contract_id,
            primary_memory_layer=contract.primary_memory_layer,
            expected_memory_layers=contract.expected_memory_layers,
            min_collection_items=min_collection_items,
            answer_obligations=_answer_obligations_for_query(message, contract=contract),
        )

    if anaphoric_selection:
        contract = RECALL_INTENT_CONTRACTS[CONTRACT_ANAPHORIC_SELECTION]
        return LifeMemoryQueryPlan(
            should_recall=True,
            query_kind=QUERY_ANAPHORIC_SELECTION,
            temporal_scope=temporal_scope if temporal_scope != TEMPORAL_UNSPECIFIED else TEMPORAL_CURRENT,
            target_domains=domains,
            target_store_ids=("life_memory_collection", "life_episode", "episodic_vector", "sqlite_ledger", "semantic_memory"),
            time_lookback_days=_lookback_for_scope(temporal_scope, default=1),
            max_facts=5,
            max_chars_per_fact=760,
            reason="anaphoric_selection_recall",
            validation_requirements=_collection_validation_requirements(contract, min_collection_items),
            recall_contract_id=contract.contract_id,
            primary_memory_layer=contract.primary_memory_layer,
            expected_memory_layers=contract.expected_memory_layers,
            min_collection_items=min_collection_items,
            answer_obligations=_answer_obligations_for_query(message, contract=contract),
        )

    if collection_like:
        scope = temporal_scope if temporal_scope != TEMPORAL_UNSPECIFIED else TEMPORAL_RECENT_DAYS
        contract_id = (
            CONTRACT_COLLECTION_DATE
            if temporal_scope in {TEMPORAL_TODAY, TEMPORAL_YESTERDAY, TEMPORAL_DAY_BEFORE_YESTERDAY}
            else CONTRACT_COLLECTION_RECENT
        )
        contract = RECALL_INTENT_CONTRACTS[contract_id]
        return LifeMemoryQueryPlan(
            should_recall=True,
            query_kind=QUERY_DATE_LOOKUP if temporal_scope in {TEMPORAL_TODAY, TEMPORAL_YESTERDAY, TEMPORAL_DAY_BEFORE_YESTERDAY} else QUERY_COLLECTION_SUMMARY,
            temporal_scope=scope,
            target_domains=domains,
            target_store_ids=("life_memory_collection", "life_episode", "semantic_memory", "episodic_vector", "sqlite_ledger"),
            time_lookback_days=_lookback_for_scope(scope, default=7),
            max_facts=6,
            max_chars_per_fact=900,
            reason="collection_memory_recall",
            validation_requirements=_collection_validation_requirements(contract, min_collection_items),
            recall_contract_id=contract.contract_id,
            primary_memory_layer=contract.primary_memory_layer,
            expected_memory_layers=contract.expected_memory_layers,
            min_collection_items=min_collection_items,
            answer_obligations=_answer_obligations_for_query(message, contract=contract),
        )

    if event_lookup:
        contract = RECALL_INTENT_CONTRACTS[CONTRACT_EVENT_LOOKUP]
        return LifeMemoryQueryPlan(
            should_recall=True,
            query_kind=QUERY_DATE_LOOKUP if temporal_scope in {TEMPORAL_TODAY, TEMPORAL_YESTERDAY, TEMPORAL_DAY_BEFORE_YESTERDAY} else QUERY_EVENT_LOOKUP,
            temporal_scope=temporal_scope if temporal_scope != TEMPORAL_UNSPECIFIED else TEMPORAL_RECENT_DAYS,
            target_domains=tuple(dict.fromkeys([*domains, DOMAIN_ACTION])),
            target_store_ids=("life_episode", "life_memory_collection", "semantic_memory", "sqlite_ledger", "episodic_vector"),
            time_lookback_days=_lookback_for_scope(temporal_scope, default=7),
            max_facts=6,
            max_chars_per_fact=260,
            reason="event_memory_recall",
            validation_requirements=contract.validation_requirements,
            recall_contract_id=contract.contract_id,
            primary_memory_layer=contract.primary_memory_layer,
            expected_memory_layers=contract.expected_memory_layers,
            answer_obligations=contract.answer_obligations,
        )

    if correction:
        contract = RECALL_INTENT_CONTRACTS[CONTRACT_CORRECTION]
        return LifeMemoryQueryPlan(
            should_recall=True,
            query_kind=QUERY_CORRECTION,
            temporal_scope=temporal_scope,
            target_domains=domains,
            target_store_ids=("life_episode", "semantic_memory", "episodic_vector", "sqlite_ledger"),
            time_lookback_days=_lookback_for_scope(temporal_scope, default=30),
            max_facts=4,
            max_chars_per_fact=220,
            reason="correction_recall",
            validation_requirements=contract.validation_requirements,
            recall_contract_id=contract.contract_id,
            primary_memory_layer=contract.primary_memory_layer,
            expected_memory_layers=contract.expected_memory_layers,
            answer_obligations=contract.answer_obligations,
        )

    if explicit_memory or preference or self_change:
        query_kind = (
            QUERY_PREFERENCE_LOOKUP if preference else
            QUERY_SELF_CHANGE if self_change else
            QUERY_EXPLICIT_MEMORY
        )
        contract_id = (
            CONTRACT_PREFERENCE if preference else
            CONTRACT_SELF_CHANGE if self_change else
            CONTRACT_EXPLICIT_MEMORY
        )
        contract = RECALL_INTENT_CONTRACTS[contract_id]
        return LifeMemoryQueryPlan(
            should_recall=True,
            query_kind=query_kind,
            temporal_scope=temporal_scope,
            target_domains=domains,
            target_store_ids=("life_episode", "semantic_memory", "episodic_vector", "sqlite_ledger", "neo4j_bond"),
            time_lookback_days=_lookback_for_scope(temporal_scope, default=None),
            max_facts=4,
            max_chars_per_fact=180,
            reason="explicit_memory_recall",
            validation_requirements=contract.validation_requirements,
            recall_contract_id=contract.contract_id,
            primary_memory_layer=contract.primary_memory_layer,
            expected_memory_layers=contract.expected_memory_layers,
            answer_obligations=contract.answer_obligations,
        )

    return LifeMemoryQueryPlan(
        should_recall=False,
        temporal_scope=temporal_scope,
        target_domains=domains,
        reason="no_memory_recall_needed",
    )


def _looks_like_collection_query(
    text: str,
    *,
    domains: tuple[str, ...],
    temporal_scope: str,
) -> bool:
    if not text:
        return False
    has_collection_time = bool(_RECENT_COLLECTION_RE.search(text)) or temporal_scope in {
        TEMPORAL_TODAY,
        TEMPORAL_YESTERDAY,
        TEMPORAL_DAY_BEFORE_YESTERDAY,
    }
    has_collection_domain = bool(
        set(domains).intersection({DOMAIN_RECOMMENDATION, DOMAIN_LIGHTNOVEL_RECOMMENDATION})
    ) or bool(_RECOMMENDATION_RE.search(text))
    if has_collection_time and has_collection_domain:
        return True
    if has_collection_time and _ITEM_COUNT_RE.search(text):
        return True
    return bool(_RECOMMENDATION_RE.search(text) and _BOOK_DOMAIN_RE.search(text) and ("哪些" in text or "几" in text))


def _looks_like_event_lookup_query(text: str, *, temporal_scope: str) -> bool:
    if not text:
        return False
    if _EVENT_LOOKUP_RE.search(text):
        return True
    if temporal_scope == TEMPORAL_UNSPECIFIED:
        return False
    return any(token in text for token in ("做了", "做过", "发了", "发过", "推送", "执行", "任务", "跑完", "处理"))


def _infer_temporal_scope(text: str) -> str:
    if any(token in text for token in ("刚才", "刚刚", "上一句", "前一句", "这三本", "这几本")):
        return TEMPORAL_CURRENT
    if any(token in text for token in ("今天", "今日")):
        return TEMPORAL_TODAY
    if any(token in text for token in ("昨天", "昨晚")):
        return TEMPORAL_YESTERDAY
    if "前天" in text:
        return TEMPORAL_DAY_BEFORE_YESTERDAY
    if "十几本" in text:
        return TEMPORAL_RECENT_DAYS
    if any(token in text for token in ("最近", "这几天", "这两天", "这三天", "这周", "近几天", "这段时间", "过去几天", "过去一周", "过去一晚上", "过去一晚")):
        return TEMPORAL_RECENT_DAYS
    if any(token in text for token in ("之前", "以前", "一直", "所有", "全部")):
        return TEMPORAL_ALL_TIME
    if _DATE_LOOKUP_RE.search(text):
        return TEMPORAL_RECENT_DAYS
    return TEMPORAL_UNSPECIFIED


def _infer_domains(text: str) -> tuple[str, ...]:
    domains: list[str] = []

    def add(domain: str) -> None:
        if domain and domain not in domains:
            domains.append(domain)

    if _BOOK_DOMAIN_RE.search(text):
        add(DOMAIN_LIGHTNOVEL_RECOMMENDATION)
    if _RECOMMENDATION_RE.search(text):
        add(DOMAIN_RECOMMENDATION)
    if any(token in text for token in ("任务", "后台", "调度", "执行", "跑完")):
        add(DOMAIN_TASK)
    if any(token in text for token in ("失败", "报错", "异常", "连不上", "被占用")):
        add(DOMAIN_FAILURE)
    if any(token in text for token in ("我喜欢", "我不喜欢", "偏好", "不要", "别再")):
        add(DOMAIN_PREFERENCE)
    if any(token in text for token in ("模型路由", "记忆系统", "能力", "策略", "开关")):
        add(DOMAIN_SELF_CHANGE)
    if _EVENT_LOOKUP_RE.search(text):
        add(DOMAIN_ACTION)
    return tuple(domains)


def _lookback_for_scope(scope: str, *, default: int | None) -> int | None:
    if scope == TEMPORAL_CURRENT:
        return 1
    if scope == TEMPORAL_TODAY:
        return 1
    if scope == TEMPORAL_YESTERDAY:
        return 2
    if scope == TEMPORAL_DAY_BEFORE_YESTERDAY:
        return 3
    if scope == TEMPORAL_RECENT_DAYS:
        return 7
    if scope == TEMPORAL_ALL_TIME:
        return None
    return default


def _minimum_collection_items(text: str) -> int:
    if not text:
        return 0
    if "十几" in text:
        return 10
    if re.search(r"(?:三|3)(?:本|部|个)", text):
        return 3
    if re.search(r"(?:两|二|2)(?:本|部|个)", text):
        return 2
    if _ITEM_COUNT_RE.search(text):
        return 1
    return 0


def _collection_validation_requirements(
    contract: RecallIntentContract,
    min_collection_items: int,
) -> tuple[str, ...]:
    requirements = list(contract.validation_requirements)
    if min_collection_items > 1 and "collection_item_min_count" not in requirements:
        requirements.append("collection_item_min_count")
    if min_collection_items in {2, 3} and "collection_item_exact_count" not in requirements:
        requirements.append("collection_item_exact_count")
    return tuple(requirements)


def _answer_obligations_for_query(text: str, *, contract: RecallIntentContract) -> tuple[str, ...]:
    obligations = list(contract.answer_obligations)
    if any(token in text for token in ("哪本", "哪部", "哪个", "喜欢", "好看", "先看", "选", "挑")):
        for obligation in ("compare_recalled_items_before_answering", "read_detail_evidence_before_deep_quality_judgment"):
            if obligation not in obligations:
                obligations.append(obligation)
    if any(token in text for token in ("正文", "epub", "网页", "翻看", "深入")):
        for obligation in ("use_detail_or_body_evidence", "say_if_detail_evidence_is_missing"):
            if obligation not in obligations:
                obligations.append(obligation)
    return tuple(obligations)


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip()
