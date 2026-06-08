"""Lightweight reference resolution for conversational intent inference.

This module intentionally does not answer the user. It resolves what short
phrases such as "第三本", "这个", "继续", or "不是这个" most likely refer to, so
the surface model can respond with context instead of guessing from one turn.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Sequence


_CN_NUMBER_MAP: dict[str, int] = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
_ORDINAL_RE = re.compile(r"第\s*(?P<value>[一二两三四五六七八九十]|\d{1,2})\s*(?P<unit>本|个|项|部|步)?")
_REC_PRONOUN_RE = re.compile(r"(这本|这部|那本|那部|刚才推荐|刚刚推荐|上面那本|上面那个|哪本)")
_DEICTIC_RE = re.compile(r"(这个|那个|它|刚才那个|刚刚那个|这件事|这块|这里)")
_CONTINUATION_RE = re.compile(r"^\s*(继续|继续吧|接着|接着做|下一步|然后呢|开始|开始吧|做吧|开工)\s*[。.!！?？]*\s*$")
_REPAIR_RE = re.compile(r"(不是这个|不是这个意思|我说的是|我问的是|理解错|误解|跑偏|没接上|承接不上|牛头不对马嘴)")
_LIFE_PROACTIVE_FOLLOWUP_RE = re.compile(r"^\s*(怎么了|咋了|怎么啦|你怎么了|发生什么了|为什么这么说|为啥这么说|怎么突然这么说)\s*[。.!！?？]*\s*$")


@dataclass(slots=True)
class ResolvedReference:
    reference_text: str
    target_kind: str
    target_id: str
    target_label: str
    confidence: float
    source: str
    evidence: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "reference_text": self.reference_text,
            "target_kind": self.target_kind,
            "target_id": self.target_id,
            "target_label": self.target_label,
            "confidence": round(float(self.confidence), 4),
            "source": self.source,
            "evidence": dict(self.evidence or {}),
        }


def _text(value: Any, *, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        return text[:limit].rstrip() + "..."
    return text


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value or []) if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else []


def parse_ordinal_index(text: str) -> int | None:
    match = _ORDINAL_RE.search(str(text or ""))
    if match is None:
        return None
    value = str(match.group("value") or "").strip()
    if value.isdigit():
        try:
            return int(value)
        except ValueError:
            return None
    return _CN_NUMBER_MAP.get(value)


def _recommendation_items(recent_recommendation_context: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    ctx = _mapping(recent_recommendation_context)
    raw_items = _list(ctx.get("items"))
    if not raw_items:
        raw_items = _list(ctx.get("visible_items"))
    if not raw_items:
        raw_items = _list(ctx.get("recommendations"))
    return [_mapping(item) for item in raw_items if isinstance(item, Mapping)]


def _item_rank(item: Mapping[str, Any]) -> int | None:
    try:
        return int(item.get("rank"))
    except (TypeError, ValueError):
        return None


def _item_key(item: Mapping[str, Any]) -> str:
    for key in ("item_key", "target_id", "series_key", "detail_url", "title"):
        value = _text(item.get(key), limit=220)
        if value:
            return value
    return ""


def _resolve_ranked_recommendation(
    *,
    text: str,
    rank: int,
    recent_recommendation_context: Mapping[str, Any] | None,
) -> ResolvedReference:
    items = _recommendation_items(recent_recommendation_context)
    for item in items:
        if _item_rank(item) == rank:
            title = _text(item.get("title") or item.get("target_label"), limit=180)
            return ResolvedReference(
                reference_text=f"第{rank}本",
                target_kind="recommendation_item",
                target_id=_item_key(item),
                target_label=title or f"最近推荐第 {rank} 项",
                confidence=0.95,
                source="recent_recommendation.rank",
                evidence={
                    "rank": rank,
                    "title": title,
                    "selection_reason": _text(item.get("selection_reason"), limit=160),
                    "intro_snippet": _text(item.get("intro_snippet"), limit=180),
                },
            )
    return ResolvedReference(
        reference_text=f"第{rank}本",
        target_kind="ordinal_reference",
        target_id=f"rank:{rank}",
        target_label=f"第 {rank} 项，但当前没有可绑定的推荐列表",
        confidence=0.42,
        source="ordinal.unresolved",
        evidence={"rank": rank, "text": _text(text, limit=120)},
    )


def _resolve_recent_recommendation_pronoun(
    *,
    text: str,
    recent_recommendation_context: Mapping[str, Any] | None,
) -> ResolvedReference | None:
    if not _REC_PRONOUN_RE.search(text):
        return None
    ctx = _mapping(recent_recommendation_context)
    referenced_item = _mapping(ctx.get("referenced_item"))
    if referenced_item:
        title = _text(referenced_item.get("title") or referenced_item.get("target_label"), limit=180)
        rank = _item_rank(referenced_item)
        return ResolvedReference(
            reference_text="这本/那本",
            target_kind="recommendation_item",
            target_id=_item_key(referenced_item),
            target_label=title or "最近推荐作品",
            confidence=0.9,
            source="recent_recommendation.referenced_item",
            evidence={"rank": rank, "title": title},
        )
    items = _recommendation_items(ctx)
    if len(items) == 1:
        item = items[0]
        title = _text(item.get("title") or item.get("target_label"), limit=180)
        return ResolvedReference(
            reference_text="这本/那本",
            target_kind="recommendation_item",
            target_id=_item_key(item),
            target_label=title or "最近推荐作品",
            confidence=0.82,
            source="recent_recommendation.single_item",
            evidence={"rank": _item_rank(item), "title": title},
        )
    if items:
        return ResolvedReference(
            reference_text="这本/那本",
            target_kind="recommendation_set",
            target_id=_text(ctx.get("delivery_id"), limit=120),
            target_label=f"最近推荐集合({len(items)} 项，未能唯一绑定)",
            confidence=0.55,
            source="recent_recommendation.ambiguous_set",
            evidence={"item_count": len(items), "titles": [_text(item.get("title"), limit=80) for item in items[:5]]},
        )
    return None


def _resolve_life_proactive_followup(
    *,
    text: str,
    recent_turns: Sequence[Mapping[str, Any]] | None,
) -> ResolvedReference | None:
    if not _LIFE_PROACTIVE_FOLLOWUP_RE.match(str(text or "").strip()):
        return None
    for turn in reversed(_list(recent_turns)):
        turn_map = _mapping(turn)
        if str(turn_map.get("role") or "").strip().lower() != "assistant":
            continue
        metadata = _mapping(turn_map.get("metadata"))
        if str(metadata.get("source_kind") or "").strip() != "life_proactive":
            return None
        content = _text(turn_map.get("content"), limit=180)
        if not content:
            continue
        return ResolvedReference(
            reference_text="怎么了",
            target_kind="previous_life_proactive_message",
            target_id=_text(turn_map.get("request_id") or turn_map.get("turn_id"), limit=180),
            target_label=content,
            confidence=0.94,
            source="conversation.life_proactive_followup",
            evidence={
                "motive": _text(metadata.get("motive"), limit=80),
                "scene": _text(metadata.get("scene"), limit=80),
                "life_state_binding": _mapping(metadata.get("life_state_binding")),
            },
        )
    return None


def resolve_references(
    *,
    text: str,
    recent_recommendation_context: Mapping[str, Any] | None = None,
    previous_belief: Mapping[str, Any] | None = None,
    recent_turns: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    normalized = str(text or "").strip()
    if not normalized:
        return []
    refs: list[ResolvedReference] = []

    life_followup_ref = _resolve_life_proactive_followup(text=normalized, recent_turns=recent_turns)
    if life_followup_ref is not None:
        refs.append(life_followup_ref)

    rank = parse_ordinal_index(normalized)
    if rank is not None:
        refs.append(
            _resolve_ranked_recommendation(
                text=normalized,
                rank=rank,
                recent_recommendation_context=recent_recommendation_context,
            )
        )

    pronoun_ref = _resolve_recent_recommendation_pronoun(
        text=normalized,
        recent_recommendation_context=recent_recommendation_context,
    )
    if pronoun_ref is not None:
        refs.append(pronoun_ref)

    belief = _mapping(previous_belief)
    if _DEICTIC_RE.search(normalized) and not refs:
        referenced_object = _mapping(belief.get("referenced_object"))
        active_topic = _text(belief.get("active_topic"), limit=160)
        if referenced_object:
            refs.append(
                ResolvedReference(
                    reference_text="这个/那个/它",
                    target_kind=_text(referenced_object.get("target_kind"), limit=80) or "belief_object",
                    target_id=_text(referenced_object.get("target_id"), limit=160),
                    target_label=_text(referenced_object.get("target_label"), limit=180),
                    confidence=0.72,
                    source="belief.referenced_object",
                    evidence={"active_topic": active_topic},
                )
            )
        elif active_topic:
            refs.append(
                ResolvedReference(
                    reference_text="这个/那个/它",
                    target_kind="active_topic",
                    target_id=active_topic,
                    target_label=active_topic,
                    confidence=0.6,
                    source="belief.active_topic",
                    evidence={},
                )
            )

    if _CONTINUATION_RE.match(normalized):
        active_topic = _text(belief.get("active_topic"), limit=160)
        refs.append(
            ResolvedReference(
                reference_text="继续/下一步",
                target_kind="active_task_or_topic",
                target_id=active_topic,
                target_label=active_topic or "当前进行中的任务或话题",
                confidence=0.68 if active_topic else 0.48,
                source="conversation.continuation",
                evidence={"recent_turn_count": len(_list(recent_turns))},
            )
        )

    if _REPAIR_RE.search(normalized):
        refs.append(
            ResolvedReference(
                reference_text="不是这个/接不上",
                target_kind="previous_inference_or_reply",
                target_id="previous_assistant_turn",
                target_label="上一轮理解或回复",
                confidence=0.88,
                source="conversation.repair",
                evidence={"text": _text(normalized, limit=160)},
            )
        )

    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for ref in refs:
        key = (ref.reference_text, ref.target_kind, ref.target_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(ref.as_dict())
    out.sort(key=lambda item: float(item.get("confidence") or 0.0), reverse=True)
    return out


def best_reference(references: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    refs = [_mapping(item) for item in _list(references) if isinstance(item, Mapping)]
    if not refs:
        return {}
    refs.sort(key=lambda item: float(item.get("confidence") or 0.0), reverse=True)
    return refs[0]


__all__ = ["ResolvedReference", "best_reference", "parse_ordinal_index", "resolve_references"]
