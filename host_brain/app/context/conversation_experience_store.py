"""Application service for persistent conversation experience.

Reasoning and lesson distillation remain here; all durable reads and writes are
delegated to :class:`ExperienceRepository` as operation-scoped units of work.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any, Mapping

from host_brain.core.ids import now_ms as _now_ms
from host_brain.core.runtime_helpers import _coerce_mapping as _mapping
from host_brain.persistence.experience_repository import ExperienceRepository


_OUTCOME_SUCCESS_RE = re.compile(
    r"^\s*(对了|这次对了|对的|是这个|就是这个|可以了|可以|行了|好了|没问题|这次可以|这次行|这就对了|嗯对|对)\s*[。.!！?？]*\s*$",
    re.IGNORECASE,
)
_OUTCOME_FAILURE_RE = re.compile(
    r"(还是不对|还不对|不对|不是这个|不是这个意思|又错了|还是错|没接上|接不上|承接不上|牛头不对马嘴|跑偏|理解错|误解|不太对|不行)",
    re.IGNORECASE,
)


def _loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _text(value: Any, *, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        return text[:limit].rstrip() + "..."
    return text


def _list(value: Any) -> list[Any]:
    return list(value or []) if isinstance(value, list) else []


def _belief_can_drive_reply(
    belief: Mapping[str, Any], *, now_ms: int | None = None
) -> bool:
    if not belief:
        return False
    flag = belief.get("can_drive_reply")
    if flag is False or str(flag).strip().lower() in {"0", "false", "no"}:
        return False
    try:
        expires_at = int(belief.get("expires_at_ms") or 0)
    except Exception:
        expires_at = 0
    return not (expires_at and expires_at < int(now_ms or _now_ms()))


def classify_outcome_signal(text: str | None) -> str:
    raw = str(text or "").strip()
    if not raw:
        return "unknown"
    if _OUTCOME_FAILURE_RE.search(raw):
        return "failure"
    if _OUTCOME_SUCCESS_RE.search(raw):
        return "success"
    return "unknown"


def _stable_id(prefix: str, *parts: str, fallback_random: bool = False) -> str:
    material = "\0".join(str(part or "") for part in parts)
    if material.replace("\0", ""):
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
        return f"{prefix}_{digest}"
    if fallback_random:
        return f"{prefix}_{uuid.uuid4().hex}"
    return ""


class ConversationExperienceStore:
    def __init__(
        self,
        *,
        ledger: Any | None = None,
        repository: ExperienceRepository | None = None,
    ) -> None:
        if ledger is None and repository is None:
            raise RuntimeError("CONVERSATION_EXPERIENCE_LEDGER_NOT_INJECTED")
        self.ledger = ledger
        self.repository = repository or ExperienceRepository.from_ledger(self.ledger)

    def ensure_schema(self) -> None:
        self.repository.assert_runtime_ready()

    def get_belief(
        self, *, session_id: str | None, person_id: str | None = None
    ) -> dict[str, Any]:
        sid = _text(session_id, limit=160)
        if not sid:
            return {}
        belief = self.repository.get_belief(
            session_id=sid,
            person_id=_text(person_id, limit=80) or "default",
        )
        return belief if _belief_can_drive_reply(belief) else {}

    def record_inference(
        self,
        *,
        request_id: str | None,
        session_id: str | None,
        person_id: str | None,
        user_text: str,
        understanding: Mapping[str, Any],
        belief_before: Mapping[str, Any] | None = None,
        assistant_reply: str | None = None,
        outcome_signal: str = "unknown",
    ) -> dict[str, Any]:
        sid = _text(session_id, limit=160)
        pid = _text(person_id, limit=80) or "default"
        req = _text(request_id, limit=120)
        if not sid:
            return {"ok": False, "reason": "NO_SESSION"}
        now = _now_ms()
        understanding_dict = _mapping(understanding)
        belief_after = _mapping(understanding_dict.get("belief_state"))
        selected_intent = _text(
            understanding_dict.get("selected_intent")
            or belief_after.get("selected_intent"),
            limit=120,
        )
        event_id = (
            _stable_id("convexp", sid, pid, req)
            if req
            else _stable_id("convexp", fallback_random=True)
        )
        correction: dict[str, Any] | None = None
        lessons: list[dict[str, Any]] = []
        correction_result: dict[str, Any] = {}
        if self._looks_like_correction(understanding_dict):
            correction, lessons, correction_result = self._correction_bundle(
                event_id=event_id,
                request_id=req,
                session_id=sid,
                person_id=pid,
                raw_text=user_text,
                understanding=understanding_dict,
                now=now,
            )
        persisted = self.repository.record_inference(
            event={
                "event_id": event_id,
                "request_id": req,
                "session_id": sid,
                "person_id": pid,
                "user_text": _text(user_text, limit=1000),
                "selected_intent": selected_intent,
                "response_policy": self._response_policy_from_understanding(
                    understanding_dict
                ),
                "understanding": understanding_dict,
                "belief_before": _mapping(belief_before),
                "belief_after": belief_after,
                "assistant_reply": _text(assistant_reply, limit=2000),
                "outcome_signal": _text(outcome_signal, limit=80) or "unknown",
                "created_at_ms": now,
                "updated_at_ms": now,
            },
            belief_after=belief_after,
            correction=correction,
            lessons=lessons,
        )
        if correction_result:
            correction_result["replayed"] = bool(persisted.get("replayed"))
        return {
            "ok": True,
            "event_id": event_id,
            "correction": correction_result,
            "replayed": bool(persisted.get("replayed")),
        }

    def update_assistant_reply(
        self,
        *,
        request_id: str | None,
        session_id: str | None = None,
        person_id: str | None = None,
        assistant_reply: str,
        outcome_signal: str = "replied",
    ) -> dict[str, Any]:
        req = _text(request_id, limit=120)
        sid = _text(session_id, limit=160)
        if not req and not sid:
            return {"ok": False, "reason": "NO_KEY"}
        result = self.repository.update_assistant_reply(
            request_id=req,
            session_id=sid,
            person_id=_text(person_id, limit=80) or "default",
            assistant_reply=_text(assistant_reply, limit=2000),
            outcome_signal=_text(outcome_signal, limit=80) or "replied",
            updated_at_ms=_now_ms(),
        )
        updated = int(result.get("updated") or 0)
        return {
            "ok": bool(updated),
            "updated": updated,
            "replayed": bool(result.get("replayed")),
        }

    def recall_lessons(
        self,
        *,
        person_id: str | None = None,
        domain: str | None = None,
        query_text: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        pid = _text(person_id, limit=80) or "default"
        query = _text(query_text, limit=500)
        domain_text = _text(domain, limit=120)
        rows = self.repository.recall_lessons(person_id=pid)
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            item = dict(row)
            score = float(item.get("confidence") or 0.0)
            lesson_domain = _text(item.get("domain"), limit=120)
            pattern = _text(item.get("pattern"), limit=160)
            lesson = _text(item.get("lesson"), limit=500)
            if domain_text and (
                domain_text in lesson_domain or lesson_domain in domain_text
            ):
                score += 0.35
            if query:
                score += min(
                    0.45,
                    self._text_overlap_score(
                        query, " ".join([lesson_domain, pattern, lesson])
                    ),
                )
            failures = int(item.get("failure_count") or 0)
            successes = int(item.get("success_count") or 0)
            if failures > successes and float(item.get("confidence") or 0.0) < 0.85:
                continue
            score += min(0.3, successes * 0.08)
            score -= min(0.18, max(0, failures - successes) * 0.03)
            item["source_event_ids"] = _loads(
                item.pop("source_event_ids_json", "[]"), []
            )
            item["relevance"] = round(score, 4)
            scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[: max(1, int(limit or 5))]]

    def record_outcome_feedback(
        self,
        *,
        session_id: str | None,
        person_id: str | None = None,
        raw_text: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        signal = classify_outcome_signal(raw_text)
        if signal == "unknown":
            return {"ok": False, "reason": "NO_OUTCOME_SIGNAL", "signal": signal}
        sid = _text(session_id, limit=160)
        if not sid:
            return {"ok": False, "reason": "NO_SESSION", "signal": signal}
        return self.repository.record_outcome_feedback(
            session_id=sid,
            person_id=_text(person_id, limit=80) or "default",
            request_id=_text(request_id, limit=120),
            signal=signal,
            updated_at_ms=_now_ms(),
        )

    def record_correction_from_understanding(
        self,
        *,
        event_id: str | None,
        request_id: str | None,
        session_id: str | None,
        person_id: str | None,
        raw_text: str,
        understanding: Mapping[str, Any],
    ) -> dict[str, Any]:
        sid = _text(session_id, limit=160)
        pid = _text(person_id, limit=80) or "default"
        if not sid:
            return {"ok": False, "reason": "NO_SESSION"}
        correction, lessons, public = self._correction_bundle(
            event_id=_text(event_id, limit=120),
            request_id=_text(request_id, limit=120),
            session_id=sid,
            person_id=pid,
            raw_text=raw_text,
            understanding=_mapping(understanding),
            now=_now_ms(),
        )
        result = self.repository.record_correction(
            correction=correction, lessons=lessons
        )
        public["replayed"] = bool(result.get("replayed"))
        return public

    def _correction_bundle(
        self,
        *,
        event_id: str,
        request_id: str,
        session_id: str,
        person_id: str,
        raw_text: str,
        understanding: Mapping[str, Any],
        now: int,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        data = _mapping(understanding)
        refs = [
            _mapping(item)
            for item in _list(data.get("resolved_references"))
            if isinstance(item, Mapping)
        ]
        best_ref = refs[0] if refs else {}
        belief = _mapping(data.get("belief_state"))
        lesson_text = self._repair_lesson(
            selected_intent=_text(belief.get("selected_intent")),
            reference=best_ref,
        )
        correction_id = (
            _stable_id("convcorr", session_id, person_id, event_id or request_id)
            if event_id or request_id
            else _stable_id("convcorr", fallback_random=True)
        )
        source_event_id = event_id or correction_id
        domain = (
            _text(data.get("domain"), limit=120)
            or _text(belief.get("active_topic"), limit=80)
            or "conversation"
        )
        pattern = (
            _text(belief.get("selected_intent"), limit=120)
            or "conversation_repair"
        )
        correction = {
            "correction_id": correction_id,
            "event_id": event_id,
            "request_id": request_id,
            "session_id": session_id,
            "person_id": person_id,
            "raw_text": _text(raw_text, limit=1000),
            "wrong_inference": _mapping(belief.get("previous_wrong_inference")),
            "correct_reference": best_ref,
            "repair_lesson": lesson_text,
            "confidence": float(
                best_ref.get("confidence") or belief.get("confidence") or 0.75
            ),
            "created_at_ms": now,
        }
        lessons = [
            self._lesson_record(
                person_id=person_id,
                domain=domain,
                pattern=pattern,
                lesson=lesson_text,
                source_event_id=source_event_id,
                confidence=0.72,
                now=now,
            )
        ]
        distilled = self._distill_lesson_spec(
            understanding=data, raw_text=raw_text
        )
        if distilled:
            lessons.append(
                self._lesson_record(
                    person_id=person_id,
                    domain=_text(distilled.get("domain"), limit=120)
                    or "conversation",
                    pattern=_text(distilled.get("pattern"), limit=120)
                    or "distilled_repair",
                    lesson=_text(distilled.get("lesson"), limit=600),
                    source_event_id=source_event_id,
                    confidence=float(distilled.get("confidence") or 0.78),
                    now=now,
                )
            )
        return (
            correction,
            lessons,
            {"ok": True, "correction_id": correction_id, "lesson": lesson_text},
        )

    @staticmethod
    def _lesson_record(
        *,
        person_id: str,
        domain: str,
        pattern: str,
        lesson: str,
        source_event_id: str,
        confidence: float,
        now: int,
    ) -> dict[str, Any]:
        lesson_id = (
            f"lesson::{person_id}::{domain}::{pattern}".replace(" ", "_")[:180]
        )
        return {
            "lesson_id": lesson_id,
            "person_id": person_id,
            "domain": domain,
            "pattern": pattern,
            "lesson": lesson,
            "source_event_id": source_event_id,
            "confidence": confidence,
            "updated_at_ms": now,
        }

    def _text_overlap_score(self, query: str, haystack: str) -> float:
        q_tokens = self._tokens(query)
        if not q_tokens:
            return 0.0
        text = str(haystack or "")
        hits = sum(1 for token in q_tokens if token and token in text)
        return hits / max(1, len(q_tokens))

    @staticmethod
    def _tokens(text: str) -> list[str]:
        raw = str(text or "").strip()
        tokens = []
        for marker in (
            "第三本",
            "第3本",
            "推荐",
            "轻小说",
            "小说",
            "偏好",
            "喜欢",
            "不是这个",
            "理解错",
            "接不上",
            "意图",
            "聪明",
            "人类",
            "继续",
            "下一步",
        ):
            if marker in raw:
                tokens.append(marker)
        compact = raw.replace(" ", "")
        for size in (4, 3, 2):
            for index in range(0, max(0, len(compact) - size + 1)):
                chunk = compact[index : index + size]
                if chunk and chunk not in tokens:
                    tokens.append(chunk)
                if len(tokens) >= 18:
                    return tokens
        return tokens

    def _distill_lesson_spec(
        self, *, understanding: Mapping[str, Any], raw_text: str
    ) -> dict[str, Any]:
        data = _mapping(understanding)
        belief = _mapping(data.get("belief_state"))
        refs = [
            _mapping(item)
            for item in _list(data.get("resolved_references"))
            if isinstance(item, Mapping)
        ]
        domain = _text(data.get("domain"), limit=120) or "conversation"
        selected_intent = _text(
            data.get("selected_intent") or belief.get("selected_intent"), limit=120
        )
        raw = str(raw_text or "")
        ref_kinds = {_text(item.get("target_kind"), limit=80) for item in refs}
        has_rank_reference = (
            any("第" in _text(item.get("reference_text"), limit=80) for item in refs)
            or "第三本" in raw
            or "第3本" in raw
        )
        has_recommendation_item = (
            "recommendation_item" in ref_kinds
            or domain == "lightnovel_recommendation"
        )
        is_repair = selected_intent in {
            "conversation_repair",
            "repair_misunderstanding",
        } or bool(belief.get("repair_requested"))
        if has_recommendation_item and (is_repair or has_rank_reference):
            return {
                "domain": "lightnovel_recommendation",
                "pattern": "distilled_recommendation_reference_repair",
                "lesson": (
                    "当用户用“第几本/这本/那本”追问或纠正轻小说推荐时，先绑定最近推荐集合里的具体作品；"
                    "如果上一轮答歪，先承认错位并重绑作品，再给判断。不要泛化成长期偏好或其他媒介。"
                ),
                "confidence": 0.82,
            }
        if is_repair:
            return {
                "domain": domain or "conversation",
                "pattern": "distilled_conversation_repair",
                "lesson": (
                    "用户指出“不对/不是这个/接不上”时，优先把它当作上一轮理解错位；"
                    "先找被纠正的对象和新的绑定目标，再继续回答，不要用空泛道歉糊过去。"
                ),
                "confidence": 0.78,
            }
        return {}

    @staticmethod
    def _response_policy_from_understanding(
        understanding: Mapping[str, Any],
    ) -> str:
        hypotheses = [
            _mapping(item)
            for item in _list(understanding.get("intent_hypotheses"))
            if isinstance(item, Mapping)
        ]
        if hypotheses:
            return _text(hypotheses[0].get("response_policy"), limit=280)
        belief = _mapping(understanding.get("belief_state"))
        return _text(belief.get("user_goal"), limit=280)

    @staticmethod
    def _looks_like_correction(understanding: Mapping[str, Any]) -> bool:
        belief = _mapping(understanding.get("belief_state"))
        selected = _text(
            understanding.get("selected_intent") or belief.get("selected_intent"),
            limit=120,
        )
        return bool(belief.get("repair_requested")) or selected in {
            "repair_misunderstanding",
            "conversation_repair",
            "criticize_reasoning_quality",
            "meta_quality_feedback",
            "challenge_value_or_direction",
        }

    @staticmethod
    def _repair_lesson(
        *, selected_intent: str, reference: Mapping[str, Any]
    ) -> str:
        target = _text(reference.get("target_label"), limit=140)
        if target:
            return (
                f"用户纠正或质疑时，先重新绑定到“{target}”，再回答真实问题；"
                "不要沿用上一轮错误理解。"
            )
        if selected_intent in {"criticize_reasoning_quality", "meta_quality_feedback"}:
            return (
                "用户批评理解方式不够像人时，不能只加分类；要说明推理缺口，"
                "并升级引用、候选意图和信念更新。"
            )
        return "用户发出纠错或质疑时，先识别上一轮错位，再基于当前上下文重建理解。"


def build_conversation_experience_read_pack(
    *,
    ledger: Any,
    session_id: str | None,
    person_id: str | None = None,
    domain: str | None = None,
    query_text: str | None = None,
    lesson_limit: int = 5,
) -> dict[str, Any]:
    """Return the narrow, read-only experience view allowed before admission.

    Intent compilation happens before a HostCommand exists.  It may use
    already persisted belief and lesson projections to form a context pack, but
    it must not receive a write-capable experience store or append feedback and
    inference events.  The returned data is therefore a snapshot, not a
    persistence authority.
    """

    try:
        resolved_lesson_limit = max(1, int(lesson_limit or 5))
    except (TypeError, ValueError):
        resolved_lesson_limit = 5
    store = ConversationExperienceStore(ledger=ledger)
    return {
        "previous_belief": store.get_belief(
            session_id=session_id,
            person_id=person_id,
        ),
        "experience_lessons": store.recall_lessons(
            person_id=person_id,
            domain=domain,
            query_text=query_text,
            limit=resolved_lesson_limit,
        ),
    }


__all__ = [
    "ConversationExperienceStore",
    "build_conversation_experience_read_pack",
    "classify_outcome_signal",
]
