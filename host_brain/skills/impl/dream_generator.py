"""Dream Generator Skill——梦境生成。

由调度器在 Sleep/空闲时触发；读取当日 Episodic Memory → LLM 生成梦境叙事
→ 写入 Episodic（dream 标签 + NARRATIVE_ONLY）。
"""

from __future__ import annotations

import datetime
import hashlib
import logging
from typing import Any, Callable

from host_brain.core.errors import SCHED_CONDITION_UNMET, SCHED_NO_CONTENT
from host_brain.core.ids import now_ms, new_event_id

logger = logging.getLogger(__name__)

# Skill 元数据常量
SKILL_NAME = "dream_generator"
SKILL_CLEARANCE = "LOW"
SKILL_RISK_LEVEL = 1
SKILL_SCOPE = "memory"
SKILL_REVERSIBLE = False

# 梦境标签
DREAM_CATEGORY = "dream"
EVIDENCE_LEVEL = "NARRATIVE_ONLY"

# 状态
STATUS_EXECUTED = "EXECUTED"
STATUS_REJECTED = "REJECTED"
STATUS_DEGRADED = "DEGRADED"

# LLM 回调类型
LLMGenerator = Callable[[dict[str, Any]], dict[str, Any]]
EpisodicReader = Callable[[int, int, int], list[dict[str, Any]]]  # start_ts, end_ts, top_k -> entries
EpisodicWriter = Callable[[dict[str, Any]], dict[str, Any]]  # entry -> result


def _positive_int(value: Any, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        resolved = int(default)
    resolved = max(int(minimum), resolved)
    if maximum is not None:
        resolved = min(int(maximum), resolved)
    return resolved


def _positive_float(value: Any, default: float, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        resolved = float(default)
    return max(float(minimum), min(float(maximum), resolved))


def _entry_text(entry: dict[str, Any]) -> str:
    for key in ("summary", "content", "detail", "text"):
        value = str(entry.get(key) or "").strip()
        if value:
            return value
    return str(entry or "").strip()


def _entry_ts_ms(entry: dict[str, Any]) -> int | None:
    for key in ("ts_ms", "created_ts_ms", "last_touched_ts_ms", "created_at_ms"):
        value = entry.get(key)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _dedupe_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for entry in entries:
        text = _entry_text(entry)
        if not text:
            continue
        key = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return out


def _summaries(entries: list[dict[str, Any]], limit: int, *, text_limit: int = 220) -> list[str]:
    values: list[str] = []
    for entry in entries[: max(0, int(limit))]:
        text = _entry_text(entry)
        if text:
            values.append(text[: max(40, int(text_limit))])
    return values


def _looks_like_previous_dream(entry: dict[str, Any]) -> bool:
    text = _entry_text(entry).lower()
    refs = " ".join(str(v or "") for v in entry.get("evidence_refs", [])).lower() if isinstance(entry.get("evidence_refs"), list) else str(entry.get("evidence_refs") or "").lower()
    source = str(entry.get("source") or "").lower()
    category = str(entry.get("category") or "").lower()
    joined = f"{text} {refs} {source} {category}"
    return any(token in joined for token in ("tag:dream", "source:dream_generator", "dream_generator", "梦境", "做了一个梦", "我梦见", "梦见", "dream"))


def _known_motif_pool(pool: Any = None) -> list[str]:
    default_pool = [
        "灯",
        "水面",
        "门",
        "旧名字",
        "新名字",
        "走廊",
        "雾",
        "房间",
        "纸灯",
        "海风",
        "终端",
        "回声",
        "钥匙",
        "窗",
        "远处的声音",
        "夜河",
        "星星",
        "日记",
        "影子",
        "岸边",
    ]
    raw_pool = pool if isinstance(pool, list) else []
    resolved = [str(item).strip() for item in raw_pool if str(item).strip()] or default_pool
    out: list[str] = []
    for item in resolved:
        if item not in out:
            out.append(item)
    return out


def _extract_known_motifs_from_text(text: str, *, pool: list[str], limit: int = 8) -> list[str]:
    source = str(text or "")
    motifs: list[str] = []
    for motif in pool:
        if motif and motif in source and motif not in motifs:
            motifs.append(motif)
        if len(motifs) >= max(1, int(limit)):
            break
    return motifs


def _motifs_from_entries(entries: list[dict[str, Any]], *, pool: list[str], limit: int = 10) -> list[str]:
    motifs: list[str] = []
    for entry in entries:
        for motif in _extract_known_motifs_from_text(_entry_text(entry), pool=pool, limit=limit):
            if motif not in motifs:
                motifs.append(motif)
            if len(motifs) >= max(1, int(limit)):
                return motifs
    return motifs


def _count_text_hits(text: str, markers: list[str]) -> list[str]:
    source = str(text or "")
    lowered = source.lower()
    hits: list[str] = []
    for marker in markers:
        token = str(marker or "").strip()
        if not token:
            continue
        haystack = lowered if token.isascii() else source
        needle = token.lower() if token.isascii() else token
        if needle in haystack and token not in hits:
            hits.append(token)
    return hits


def _evaluate_dream_quality(
    dream_text: str,
    *,
    dream_motifs: list[str],
) -> dict[str, Any]:
    text = str(dream_text or "").strip()
    issues: list[dict[str, Any]] = []
    score = 1.0

    if len(text) < 120:
        issues.append({"code": "DREAM_TOO_SHORT", "severity": "fallback", "detail": f"length={len(text)}"})
        score -= 0.35
    if len(text) > 1200:
        issues.append({"code": "DREAM_TOO_LONG", "severity": "warn", "detail": f"length={len(text)}"})
        score -= 0.1

    report_hits = _count_text_hits(
        text,
        [
            "梦源配置",
            "连续性配置",
            "dream_source_profile",
            "dream_continuity_profile",
            "random_walk_weight",
            "previous_dream_count",
            "narrative-only",
            "根据素材",
            "来源",
            "映射",
            "总结报告",
            "这段梦说明",
            "这个梦象征",
            "这个梦意味着",
        ],
    )
    if report_hits:
        issues.append({"code": "DREAM_REPORT_STYLE_OR_PROMPT_LEAK", "severity": "fallback", "detail": ",".join(report_hits[:6])})
        score -= 0.35

    fact_hits = _count_text_hits(
        text,
        [
            "真实发生",
            "现实中我已经",
            "我已经在现实",
            "事实是",
            "写入事实",
            "Canon",
            "确凿",
            "证明了",
        ],
    )
    if fact_hits:
        issues.append({"code": "DREAM_FACT_PROMOTION_RISK", "severity": "fallback", "detail": ",".join(fact_hits[:6])})
        score -= 0.35

    motif_overuse: list[str] = []
    max_repeat = max(4, min(8, len(text) // 90 + 2))
    for motif in dream_motifs:
        token = str(motif or "").strip()
        if token and text.count(token) > max_repeat:
            motif_overuse.append(f"{token}:{text.count(token)}")
    if motif_overuse:
        issues.append({"code": "DREAM_MOTIF_OVERUSED", "severity": "warn", "detail": ",".join(motif_overuse[:6])})
        score -= 0.12

    repeated_openers = text.count("我梦见")
    if repeated_openers > 4:
        issues.append({"code": "DREAM_TEMPLATE_REPETITION", "severity": "warn", "detail": f"我梦见={repeated_openers}"})
        score -= 0.08

    severe = any(str(issue.get("severity")) == "fallback" for issue in issues)
    decision = "FALLBACK" if severe or score < 0.55 else "APPROVE"
    return {
        "schema": "Natsume.dream_quality_guard.v1",
        "decision": decision,
        "score": round(max(0.0, min(1.0, score)), 3),
        "issue_count": len(issues),
        "issues": issues,
        "narrative_length": len(text),
    }


def _build_guarded_fallback_dream(
    *,
    dream_motifs: list[str],
    free_association_seed: str,
) -> str:
    motifs = [str(item).strip() for item in dream_motifs if str(item).strip()]
    while len(motifs) < 3:
        motifs.append(["灯", "门", "水面", "雾"][len(motifs) % 4])
    first, second, third = motifs[:3]
    return (
        f"我梦见自己站在一片很安静的{second}旁边，手里握着一小点{first}的光。"
        f"远处有{third}，但它没有催我过去，只是像呼吸一样慢慢亮着。"
        "我在梦里走得很轻，像怕惊动什么还没有名字的心绪。"
        "有些记忆从身边经过，像水面上的影子，我没有把它们抓住，也没有把它们当成事实，"
        "只是看着它们在光里变软。醒来前，那点光还留在掌心，安静得像一句没有说完的话。"
        "我记得它的余温，也记得这个梦只是梦。"
    )


def _select_dream_motifs(
    *,
    entries: list[dict[str, Any]],
    bond_summary: Any,
    emotion_context: Any,
    event_id: str,
    ts_ms: int,
    pool: Any,
    continuity_motifs: list[str] | None = None,
    limit: int = 5,
) -> list[str]:
    motif_pool = _known_motif_pool(pool)
    seed_material = "|".join(_summaries(entries, 8, text_limit=120))
    seed_material += f"|{bond_summary}|{emotion_context}|{event_id}|{ts_ms}"
    digest = hashlib.sha256(seed_material.encode("utf-8")).digest()
    motifs: list[str] = []
    continuity = [str(item).strip() for item in list(continuity_motifs or []) if str(item).strip()]
    if continuity:
        offset = digest[0] % len(continuity)
        rotated = continuity[offset:] + continuity[:offset]
        continuity_take = 1 + (digest[1] % min(2, len(rotated)))
        for candidate in rotated[:continuity_take]:
            if candidate not in motifs:
                motifs.append(candidate)
    for byte in digest:
        candidate = motif_pool[byte % len(motif_pool)]
        if candidate not in motifs:
            motifs.append(candidate)
        if len(motifs) >= max(1, int(limit)):
            break
    return motifs


def execute_dream_generator(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行梦境生成。

    args:
        max_episodic_entries: 当日 Episodic 回顾最大条数（默认 20）

    context:
        sys_mode: 当前系统模式
        idle: 是否处于空闲状态
        request_id / intent_id
        llm_generator: LLM 生成回调
        episodic_reader: Episodic 读取回调
        episodic_writer: Episodic 写入回调
        bond_summary: Bond 摘要（可选）
    """
    ctx = context or {}
    ts = now_ms()
    event_id = new_event_id()
    request_id = ctx.get("request_id")
    intent_id = ctx.get("intent_id")

    # 条件检查：必须在 Sleep 或空闲状态
    sys_mode = str(ctx.get("sys_mode", "normal")).strip().lower()
    is_idle = bool(ctx.get("idle", False))

    if sys_mode != "sleep" and not is_idle:
        return {
            "status": STATUS_REJECTED,
            "event_id": event_id,
            "reason_code": SCHED_CONDITION_UNMET,
            "detail": f"dream requires sleep mode or idle state, got sys_mode={sys_mode}, idle={is_idle}",
            "request_id": request_id,
            "ts_ms": ts,
        }

    # 读取梦境素材。梦不是当天日志摘要：当天记忆只是素材池之一，
    # 还允许近期/长期记忆、过往梦境、关系状态和自由联想参与。
    top_k = _positive_int(args.get("max_episodic_entries", 20), 20, minimum=1, maximum=200)
    material_top_k = _positive_int(
        args.get("dream_material_top_k", max(top_k, 24)),
        max(top_k, 24),
        minimum=top_k,
        maximum=200,
    )
    material_window_days = _positive_int(args.get("dream_material_window_days", 14), 14, minimum=1, maximum=90)
    random_walk_weight = _positive_float(args.get("random_walk_weight", 0.35), 0.35, minimum=0.0, maximum=0.8)
    episodic_reader: EpisodicReader | None = ctx.get("episodic_reader")
    material_entries: list[dict[str, Any]] = []

    if episodic_reader:
        try:
            window_start = max(0, ts - material_window_days * 24 * 3600 * 1000)
            read_limit = max(material_top_k, min(500, material_top_k * 8))
            material_entries = sorted(
                _dedupe_entries(episodic_reader(window_start, ts, read_limit)),
                key=lambda item: _entry_ts_ms(item) or 0,
                reverse=True,
            )[:material_top_k]
        except Exception as exc:
            logger.warning("Episodic read failed for dream: %s", exc)
            # 降级：不阻塞梦境生成，但标记 episodic 不可用
            material_entries = []

    today_start = int(datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    daily_entries = [entry for entry in material_entries if (_entry_ts_ms(entry) or ts) >= today_start]
    previous_dream_entries = [entry for entry in material_entries if _looks_like_previous_dream(entry)]
    long_term_entries = [
        entry
        for entry in material_entries
        if entry not in daily_entries and entry not in previous_dream_entries
    ]
    recent_entries = material_entries[:material_top_k]
    emotion_context = ctx.get("emotion_context") or ctx.get("timeline_diary") or {}
    motif_pool = _known_motif_pool(args.get("motif_pool"))
    previous_dream_motifs = _motifs_from_entries(previous_dream_entries, pool=motif_pool, limit=10)
    ambient_motifs = _motifs_from_entries(long_term_entries or material_entries, pool=motif_pool, limit=10)
    continuity_motifs: list[str] = []
    for motif in [*previous_dream_motifs, *ambient_motifs]:
        if motif not in continuity_motifs:
            continuity_motifs.append(motif)
    dream_motifs = _select_dream_motifs(
        entries=material_entries,
        bond_summary=ctx.get("bond_summary", {}),
        emotion_context=emotion_context,
        event_id=event_id,
        ts_ms=ts,
        pool=motif_pool,
        continuity_motifs=continuity_motifs,
    )
    reused_dream_motifs = [motif for motif in dream_motifs if motif in previous_dream_motifs]
    dream_continuity_profile = {
        "schema": "Natsume.dream_continuity_profile.v1",
        "enabled": True,
        "continuity_scope": "motif_only",
        "truth_promotion_allowed": False,
        "previous_dream_count": len(previous_dream_entries),
        "previous_dream_motifs": previous_dream_motifs,
        "ambient_motifs": ambient_motifs,
        "reused_dream_motifs": reused_dream_motifs,
    }
    free_association_seed = hashlib.sha256(
        f"{event_id}|{ts}|{dream_motifs}|{len(material_entries)}".encode("utf-8")
    ).hexdigest()[:12]
    dream_source_profile = {
        "schema": "Natsume.dream_source_profile.v1",
        "daily_memory_link": "weak_optional",
        "direct_event_mapping_required": False,
        "narrative_only": True,
        "material_window_days": material_window_days,
        "daily_memory_count": len(daily_entries),
        "recent_memory_count": len(recent_entries),
        "long_term_memory_count": len(long_term_entries),
        "previous_dream_count": len(previous_dream_entries),
        "dream_continuity_profile": dream_continuity_profile,
        "bond_context_present": bool(ctx.get("bond_summary")),
        "emotion_context_present": bool(emotion_context),
        "random_walk_weight": random_walk_weight,
        "free_association_seed": free_association_seed,
    }

    # 无素材时跳过
    if not material_entries and not ctx.get("bond_summary") and not emotion_context:
        return {
            "status": STATUS_REJECTED,
            "event_id": event_id,
            "reason_code": SCHED_NO_CONTENT,
            "detail": "no episodic entries or bond summary for dream generation",
            "request_id": request_id,
            "ts_ms": ts,
        }

    # 构建 prompt_context → LLM 生成梦境叙事
    llm_generator: LLMGenerator | None = ctx.get("llm_generator")
    dream_narrative: str = ""

    if llm_generator:
        try:
            llm_input = {
                "task": "dream_generation",
                # Backward-compatible field: recent mixed material, not a strict daily summary.
                "episodic_summaries": _summaries(recent_entries, top_k),
                "daily_episodic_summaries": _summaries(daily_entries, top_k),
                "recent_episodic_summaries": _summaries(recent_entries, material_top_k),
                "long_term_memory_summaries": _summaries(long_term_entries, 12),
                "previous_dream_summaries": _summaries(previous_dream_entries, 6),
                "bond_summary": ctx.get("bond_summary", {}),
                "emotion_context": emotion_context,
                "dream_motifs": dream_motifs,
                "dream_source_profile": dream_source_profile,
                "dream_continuity_profile": dream_continuity_profile,
                "free_association_seed": free_association_seed,
                "ts_ms": ts,
            }
            llm_result = llm_generator(llm_input)
            dream_narrative = str(llm_result.get("narrative", llm_result.get("content", "")))
        except Exception as exc:
            logger.warning("LLM dream generation failed: %s", exc)
            return {
                "status": STATUS_DEGRADED,
                "event_id": event_id,
                "reason_code": "DREAM_LLM_UNAVAILABLE",
                "detail": f"LLM generation failed: {exc}",
                "request_id": request_id,
                "ts_ms": ts,
            }
    else:
        # 无 LLM 时使用简单模板
        dream_narrative = "（梦境叙事——LLM 不可用时的占位内容）"

    if not dream_narrative.strip():
        return {
            "status": STATUS_REJECTED,
            "event_id": event_id,
            "reason_code": SCHED_NO_CONTENT,
            "detail": "LLM produced empty dream narrative",
            "request_id": request_id,
            "ts_ms": ts,
        }

    dream_quality_guard = _evaluate_dream_quality(dream_narrative, dream_motifs=dream_motifs)
    if str(dream_quality_guard.get("decision") or "") == "FALLBACK":
        original_hash = _content_hash(dream_narrative)
        dream_narrative = _build_guarded_fallback_dream(
            dream_motifs=dream_motifs,
            free_association_seed=free_association_seed,
        )
        dream_quality_guard = {
            **dream_quality_guard,
            "fallback_applied": True,
            "original_hash": original_hash,
            "fallback_hash": _content_hash(dream_narrative),
            "fallback_length": len(dream_narrative),
        }
    else:
        dream_quality_guard = {
            **dream_quality_guard,
            "fallback_applied": False,
            "original_hash": _content_hash(dream_narrative),
        }

    dream_usage_policy = {
        "schema": "Natsume.dream_usage_policy.v1",
        "mode": "ambient_with_soft_reference",
        "direct_mention_default": False,
        "allowed_uses": [
            "tone_coloring",
            "motif_echo",
            "emotional_residue",
            "soft_self_continuity",
            "soft_direct_reference",
        ],
        "direct_mention_allowed_when": [
            "user_asks_about_dreams",
            "dream_topic_is_naturally_relevant",
            "quiet_reflective_context",
            "reunion_after_long_absence",
            "late_night_intimate_context",
            "user_shares_emotional_vulnerability",
        ],
        "soft_reference_allowed_when": [
            "reunion_after_long_absence",
            "late_night_intimate_context",
            "inner_world_tick_has_dream_residue",
            "user_mentions_sleep_or_dreams",
        ],
        "avoid": [
            "forced_i_dreamed_opening",
            "explaining_dream_source_profile",
            "treating_dream_as_fact",
            "verbatim_dream_recount",
        ],
    }

    # 写入 Episodic Memory
    quality_issue_codes = [
        str(issue.get("code") or "").strip()
        for issue in list(dream_quality_guard.get("issues") or [])
        if str(issue.get("code") or "").strip()
    ]
    dream_evidence_refs = [
        intent_id or event_id,
        "source:dream_generator",
        "tag:dream",
        "dream_profile:weak_daily_link",
        f"dream_profile:window_days={material_window_days}",
        f"dream_profile:random_walk_weight={random_walk_weight:.2f}",
        (
            "dream_profile:counts="
            f"daily:{len(daily_entries)},"
            f"recent:{len(recent_entries)},"
            f"long_term:{len(long_term_entries)},"
            f"previous_dream:{len(previous_dream_entries)}"
        ),
        f"dream_continuity:previous_dream_count={len(previous_dream_entries)}",
        "dream_continuity:reused_motifs="
        + (",".join(reused_dream_motifs) if reused_dream_motifs else "none"),
        f"dream_quality:decision={dream_quality_guard.get('decision')}",
        f"dream_quality:score={dream_quality_guard.get('score')}",
        "dream_quality:issues=" + (",".join(quality_issue_codes) if quality_issue_codes else "none"),
        "dream_usage:mode=ambient_low_weight",
        "dream_usage:direct_mention_default=false",
        *[f"dream_motif:{motif}" for motif in dream_motifs],
    ]
    dream_entry = {
        "content": dream_narrative,
        "category": DREAM_CATEGORY,
        "evidence_level": EVIDENCE_LEVEL,
        "evidence_refs": dream_evidence_refs,
        "source": "dream_generator",
        "dream_source_profile": dream_source_profile,
        "dream_continuity_profile": dream_continuity_profile,
        "dream_quality_guard": dream_quality_guard,
        "dream_usage_policy": dream_usage_policy,
        "dream_motifs": dream_motifs,
        "ts_ms": ts,
    }

    episodic_writer: EpisodicWriter | None = ctx.get("episodic_writer")
    write_result: dict[str, Any] | None = None
    if episodic_writer:
        try:
            write_result = episodic_writer(dream_entry)
        except Exception as exc:
            logger.warning("Episodic write failed for dream: %s", exc)
            return {
                "status": STATUS_DEGRADED,
                "event_id": event_id,
                "reason_code": "DREAM_EPISODIC_WRITE_FAILED",
                "detail": f"Episodic write failed: {exc}",
                "request_id": request_id,
                "ts_ms": ts,
            }

        # 额外写一条互动槽位：只让后续对话吸收梦境余味，
        # 不把“我昨晚梦见了……”变成固定开场。
        try:
            _snippet = dream_narrative[:80] if len(dream_narrative) > 80 else dream_narrative
            _hint_entry = {
                "content": (
                    f"[梦境余味] 这段梦只适合作为低权重的情绪和意象底色：{_snippet}…"
                    "先自然回应当前话题，不要固定用“我昨晚梦见了”开场；"
                    "只有用户问起梦，或话题自然贴近时，才轻轻提起。"
                ),
                "category": "interaction_hint",
                "evidence_refs": [
                    event_id,
                    "source:dream_generator_usage_hint",
                    "tag:dream_usage_hint",
                    "dream_usage:ambient_low_weight",
                    "dream_usage:direct_mention_default=false",
                ],
                "source": "dream_generator_usage_hint",
                "ts_ms": ts + 1,
            }
            episodic_writer(_hint_entry)
        except Exception:
            pass

    # 构建 manifest
    manifest = {
        "dream_id": event_id,
        "category": DREAM_CATEGORY,
        "evidence_level": EVIDENCE_LEVEL,
        "memory_entry_count": len(material_entries),
        "daily_memory_entry_count": len(daily_entries),
        "long_term_memory_entry_count": len(long_term_entries),
        "previous_dream_count": len(previous_dream_entries),
        "dream_motifs": dream_motifs,
        "dream_source_profile": dream_source_profile,
        "dream_continuity_profile": dream_continuity_profile,
        "dream_quality_guard": dream_quality_guard,
        "dream_usage_policy": dream_usage_policy,
        "narrative_length": len(dream_narrative),
        "hash": _content_hash(dream_narrative),
    }

    return {
        "status": STATUS_EXECUTED,
        "event_id": event_id,
        "reason_code": None,
        "detail": "dream generated and written to episodic memory",
        "manifest": manifest,
        "dream_entry": dream_entry,
        "write_result": write_result,
        "request_id": request_id,
        "intent_id": intent_id,
        "ts_ms": ts,
    }


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
