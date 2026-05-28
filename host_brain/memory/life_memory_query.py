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

DOMAIN_RECOMMENDATION = "recommendation"
DOMAIN_LIGHTNOVEL_RECOMMENDATION = "lightnovel_recommendation"
DOMAIN_TASK = "task"
DOMAIN_FAILURE = "failure"
DOMAIN_PREFERENCE = "preference"
DOMAIN_SELF_CHANGE = "self_change"
DOMAIN_ACTION = "action"


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
    explicit_memory = any(token in message for token in ("还记得", "记得吗", "记不记得", "记了什么", "让你记"))
    failure_debug = any(token in message for token in ("失败原因", "为什么失败", "连不上", "异常原因", "被占用"))
    if not failure_debug and any(token in message for token in ("报错", "异常")):
        failure_debug = temporal_scope != TEMPORAL_UNSPECIFIED or any(token in message for token in ("上次", "之前", "刚才", "原因"))
    correction = any(token in message for token in ("不对", "错了", "纠正", "不是这个意思", "你记错"))
    preference = any(token in message for token in ("我喜欢", "我不喜欢", "偏好", "以后", "不要", "别再", "必须"))
    open_loop = any(token in message for token in ("待办", "承诺", "说好", "继续刚才", "还没做", "完成了吗", "跑完了吗"))
    self_change = any(token in message for token in ("模型路由", "记忆系统", "能力", "策略", "开关", "改过什么"))

    if failure_debug:
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
            validation_requirements=("error_episode_or_semantic_hit", "source_ref_present"),
        )

    if open_loop:
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
            validation_requirements=("trusted_memory_hit", "source_ref_present"),
        )

    if collection_like and temporal_scope not in {TEMPORAL_CURRENT, TEMPORAL_UNSPECIFIED}:
        scope = temporal_scope
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
            validation_requirements=("scope_match", "collection_items_present", "evidence_refs_present"),
        )

    if anaphoric_selection:
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
            validation_requirements=("collection_or_episode_hit", "evidence_refs_present", "no_self_echo"),
        )

    if collection_like:
        scope = temporal_scope if temporal_scope != TEMPORAL_UNSPECIFIED else TEMPORAL_RECENT_DAYS
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
            validation_requirements=("scope_match", "collection_items_present", "evidence_refs_present"),
        )

    if event_lookup:
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
            validation_requirements=("event_time_scope_match", "event_or_collection_hit", "source_ref_present"),
        )

    if correction:
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
            validation_requirements=("correction_state_checked", "suppressed_not_fact"),
        )

    if explicit_memory or preference or self_change:
        query_kind = (
            QUERY_PREFERENCE_LOOKUP if preference else
            QUERY_SELF_CHANGE if self_change else
            QUERY_EXPLICIT_MEMORY
        )
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
            validation_requirements=("trusted_memory_hit", "source_ref_present"),
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


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip()
