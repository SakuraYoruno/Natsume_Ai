from __future__ import annotations

import copy
import json
import re
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from host_brain.runtime.life_organs import (
    get_organ_runtime_state,
    get_recent_life_experience,
    append_life_experience,
)

DEFAULT_PERSON_ID = "default"

SELF_REWARD_WEIGHTS: dict[str, float] = {
    "continuity": 0.20,
    "coherence": 0.18,
    "competence": 0.16,
    "curiosity": 0.12,
    "restraint": 0.12,
    "presence": 0.10,
    "aesthetic": 0.07,
    "attachment": 0.05,
}

DEFAULT_ADAPTIVE_TRAITS: dict[str, float] = {
    "curiosity": 0.52,
    "assertiveness": 0.42,
    "playfulness": 0.48,
    "sensitivity": 0.56,
    "resilience": 0.50,
    "restraint": 0.62,
    "warmth": 0.72,
}

DEFAULT_PROJECTS: tuple[dict[str, Any], ...] = (
    {
        "project_id": "improve_lightnovel_recommendation",
        "title": "持续改善轻小说推荐",
        "motive": "让推荐从临时榜单变成可学习的偏好系统",
        "status": "active",
        "progress": 0.08,
        "next_step": "整理最近推荐结果和反馈，降低封面/目录/口味错配",
    },
    {
        "project_id": "stabilize_conversation_continuity",
        "title": "降低对话断裂",
        "motive": "让主动消息、被动对话和记忆承接在同一条时间线上",
        "status": "active",
        "progress": 0.12,
        "next_step": "检查最近可见事件和自我议程是否能被 Surface 承接",
    },
    {
        "project_id": "refine_surface_style",
        "title": "改善表达风格",
        "motive": "让回复更贴近 Soul 和台词库，而不是助手味",
        "status": "active",
        "progress": 0.10,
        "next_step": "从重复/生硬输出里提取风格惩罚信号",
    },
    {
        "project_id": "maintain_body_presence",
        "title": "维护身体存在感",
        "motive": "无人说话时也保持身体层的低频生命外显",
        "status": "active",
        "progress": 0.18,
        "next_step": "根据当前疲劳、节律和注意力选择低频身体状态",
    },
    {
        "project_id": "memory_cleanliness",
        "title": "减少脏记忆和错误承接",
        "motive": "避免旧话题、测试数据和无对象指代污染当前对话",
        "status": "active",
        "progress": 0.10,
        "next_step": "把低价值重复内心流和失败承接降权",
    },
    {
        "project_id": "browser_automation_competence",
        "title": "提高浏览器自动化稳定性",
        "motive": "减少登录态、封面抓取、目录拆分和打包流程的不稳定",
        "status": "active",
        "progress": 0.06,
        "next_step": "从最近自动化失败里更新 competence_model",
    },
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _event_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _map(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else []


def _text(value: Any, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:limit]


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value not in (None, "") else default)
    except Exception:
        return float(default)


def _clamp(value: Any, default: float = 0.0) -> float:
    return max(0.0, min(1.0, _float(value, default)))


def _loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return copy.deepcopy(default)
    if isinstance(value, (dict, list)):
        return copy.deepcopy(value)
    try:
        return json.loads(str(value))
    except Exception:
        return copy.deepcopy(default)


def _conn(ledger: Any) -> Any:
    conn = getattr(ledger, "_conn", None)
    if conn is None:
        raise RuntimeError("ledger sqlite connection unavailable")
    return conn


def _phase_label(environment: Mapping[str, Any], state: Mapping[str, Any]) -> str:
    return _text(environment.get("day_phase") or _map(state.get("clock")).get("day_phase") or "unknown", 40)


def _recent_memory_events(*, ledger: Any, person_id: str, limit: int = 20) -> list[dict[str, Any]]:
    try:
        rows = _conn(ledger).execute(
            "SELECT event_id, source_kind, event_kind, visibility, role, content_summary, causality_json, refs_json, salience, valence, uncertainty, created_at_ms "
            "FROM memory_event WHERE person_id=? AND active=1 ORDER BY created_at_ms DESC LIMIT ?",
            (person_id, int(limit)),
        ).fetchall()
    except Exception:
        return []
    return [
        {
            "event_id": r[0],
            "source_kind": r[1],
            "event_kind": r[2],
            "visibility": r[3],
            "role": r[4],
            "content_summary": r[5],
            "causality": _loads(r[6], {}),
            "refs": _loads(r[7], {}),
            "salience": float(r[8] or 0.0),
            "valence": float(r[9] or 0.0),
            "uncertainty": float(r[10] or 0.0),
            "created_at_ms": int(r[11] or 0),
        }
        for r in rows
    ]


def _default_projects(now_ms: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for raw in DEFAULT_PROJECTS:
        item = dict(raw)
        item.setdefault("last_worked_at_ms", 0)
        item.setdefault("created_at_ms", now_ms)
        item.setdefault("evidence_refs", [])
        item.setdefault("learned_patterns", [])
        items.append(item)
    return items


def _merge_projects(existing: Any, now_ms: int) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for item in _list(existing):
        mapped = _map(item)
        project_id = _text(mapped.get("project_id"), 80)
        if project_id:
            by_id[project_id] = mapped
    for default in _default_projects(now_ms):
        project_id = str(default.get("project_id") or "")
        if project_id not in by_id:
            by_id[project_id] = default
        else:
            merged = {**default, **by_id[project_id]}
            merged.setdefault("evidence_refs", [])
            merged.setdefault("learned_patterns", [])
            by_id[project_id] = merged
    return list(by_id.values())


def _organ_strengths(*, ledger: Any, person_id: str) -> dict[str, float]:
    try:
        runtime = get_organ_runtime_state(ledger=ledger, person_id=person_id)
    except Exception:
        return {}
    strengths: dict[str, float] = {}
    for organ_id, state in _map(runtime.get("organs")).items():
        item = _map(state)
        strengths[str(organ_id)] = _clamp(
            _float(item.get("activation")) * 0.35
            + _float(item.get("pressure")) * 0.50
            + _float(item.get("fatigue")) * 0.15
        )
    return strengths


def _proposal(kind: str, strength: float, reason: str, refs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "proposal_kind": kind,
        "strength": round(_clamp(strength), 3),
        "reason": _text(reason, 180),
        "evidence_refs": list(refs or [])[:6],
    }


def build_organ_agenda_proposals(*, ledger: Any, person_id: str, state: Mapping[str, Any], environment: Mapping[str, Any]) -> list[dict[str, Any]]:
    p = _map(state.get("life_pressure"))
    needs = _map(state.get("needs"))
    work = _map(state.get("work"))
    inner = _map(state.get("inner_world"))
    strengths = _organ_strengths(ledger=ledger, person_id=person_id)
    phase = _phase_label(environment, state)
    proposals = [
        _proposal("body_presence", max(0.22, strengths.get("somatic", 0.0), strengths.get("proprioception", 0.0)), "somatic/proprioception 维持身体存在感"),
        _proposal("environment_awareness", max(0.24, strengths.get("thalamus", 0.0), 0.35 if phase in {"night", "late_night"} else 0.2), f"当前环境阶段={phase}，需要持续感知时间和通道状态"),
        _proposal("rest_recovery", max(_float(p.get("fatigue")), strengths.get("circadian", 0.0), strengths.get("glymphatic", 0.0)), "circadian/glymphatic 调节疲劳和恢复"),
        _proposal("memory_consolidation", max(_float(needs.get("memory_consolidation")), strengths.get("hippocampus", 0.0), strengths.get("default_mode", 0.0) * 0.65), "hippocampus/default_mode 整理近期经历"),
        _proposal("relationship_continuity", max(_float(p.get("relationship")) * 0.6, _float(p.get("absence")) * 0.45, strengths.get("social_brain", 0.0)), "social_brain 维持关系连续，但不等于主动打扰"),
        _proposal("style_refinement", max(0.18, strengths.get("language_system", 0.0), strengths.get("pain_reward", 0.0) * 0.5), "language_system 从重复和生硬表达中学习"),
    ]
    if _text(work.get("summary"), 160) or bool(work.get("active")):
        proposals.append(_proposal("work_watch", max(0.35, _float(p.get("work")), strengths.get("prefrontal", 0.0)), "近期 work_event 仍可作为环境刺激", [{"kind": "work", "work_id": _text(work.get("work_id"), 80)}]))
    if _text(inner.get("dream_residue"), 160):
        proposals.append(_proposal("dream_processing", max(0.32, _float(p.get("dream")), strengths.get("default_mode", 0.0)), "dream residue 未过期，可私有处理", [{"kind": "dream_residue"}]))
    if strengths.get("immune", 0.0) >= 0.28 or strengths.get("pain_reward", 0.0) >= 0.38:
        proposals.append(_proposal("self_repair", max(strengths.get("immune", 0.0), strengths.get("pain_reward", 0.0)), "immune/pain_reward 检测到失败或风险模式"))
    return sorted(proposals, key=lambda item: float(item.get("strength") or 0.0), reverse=True)


def _agenda_item(kind: str, summary: str, priority: float, now_ms: int, created_by: str, refs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    ttl = 6 * 3600_000 if kind in {"body_presence", "environment_awareness", "rest_recovery"} else 2 * 3600_000
    return {
        "agenda_id": f"agenda_{kind}",
        "kind": kind,
        "summary": _text(summary, 180),
        "priority": round(_clamp(priority), 3),
        "energy_cost": round(0.08 + _clamp(priority) * 0.18, 3),
        "created_by": created_by,
        "status": "active",
        "next_due_ms": now_ms + 600_000,
        "expires_at_ms": now_ms + ttl,
        "evidence_refs": list(refs or [])[:6],
    }


def build_self_agenda(*, proposals: Sequence[Mapping[str, Any]], state: Mapping[str, Any], now_ms: int) -> list[dict[str, Any]]:
    existing = {
        _text(item.get("kind"), 80): _map(item)
        for item in _list(_map(state.get("inner_world")).get("self_agenda"))
        if _text(_map(item).get("kind"), 80)
    }
    agenda: dict[str, dict[str, Any]] = {}
    for proposal in proposals:
        kind = _text(proposal.get("proposal_kind"), 80)
        if not kind:
            continue
        prev = existing.get(kind, {})
        priority = max(_float(proposal.get("strength")), _float(prev.get("priority")) * 0.82)
        agenda[kind] = _agenda_item(
            kind,
            _text(proposal.get("reason"), 180) or _text(prev.get("summary"), 180),
            priority,
            now_ms,
            "organ_proposal",
            [_map(x) for x in _list(proposal.get("evidence_refs"))],
        )
    for required in ("body_presence", "environment_awareness", "memory_consolidation", "rest_recovery"):
        agenda.setdefault(required, _agenda_item(required, f"{required} 是自主存活的常驻议程", 0.22, now_ms, "autonomous_default"))
    active = [item for item in agenda.values() if int(item.get("expires_at_ms") or 0) > now_ms]
    return sorted(active, key=lambda item: float(item.get("priority") or 0.0), reverse=True)[:8]


def choose_attention(*, agenda: Sequence[Mapping[str, Any]], state: Mapping[str, Any]) -> dict[str, Any]:
    work = _map(state.get("work"))
    if _text(work.get("summary"), 120) and _map(state.get("life_pressure")).get("work", 0.0):
        return {"current_attention": "work_watch", "reason": "近期后台任务仍是环境刺激"}
    for item in agenda:
        kind = _text(_map(item).get("kind"), 80)
        if kind not in {"relationship_continuity"}:
            return {"current_attention": kind, "reason": _text(_map(item).get("summary"), 160)}
    return {"current_attention": "body_presence", "reason": "无人输入时维持身体存在感"}


def extract_learning_signals(*, ledger: Any, person_id: str, state: Mapping[str, Any], agenda: Sequence[Mapping[str, Any]], now_ms: int) -> list[dict[str, Any]]:
    recent_events = _recent_memory_events(ledger=ledger, person_id=person_id, limit=24)
    recent_life = list(get_recent_life_experience(ledger=ledger, person_id=person_id, limit=24).get("items") or [])
    text_window = " ".join(_text(item.get("content_summary"), 120) for item in [*recent_events, *recent_life])
    signals: list[dict[str, Any]] = []
    novelty = min(1.0, len({str(item.get("event_kind") or "") for item in recent_events + recent_life}) / 10.0)
    if novelty >= 0.2:
        signals.append({"signal_kind": "novelty", "strength": round(novelty, 3), "candidate_delta": {"trait": "curiosity", "delta": 0.004}, "evidence_refs": []})
    if any(token in text_window.lower() for token in ("failed", "error", "timeout", "degraded", "失败", "错误", "没回复", "断裂", "莫名其妙", "坏")):
        signals.append({"signal_kind": "failure_pattern", "strength": 0.68, "candidate_delta": {"competence": "self_repair", "delta": -0.015, "habit": "check_recent_visible_context_before_reply"}, "evidence_refs": []})
    if any(token in text_window for token in ("早上好", "早安", "时间错位", "重复")):
        signals.append({"signal_kind": "style_repetition_penalty", "strength": 0.58, "candidate_delta": {"habit": "avoid_repeating_time_mismatch_greeting", "trait": "restraint", "delta": 0.006}, "evidence_refs": []})
    if any(_text(_map(item).get("kind"), 80) == "memory_consolidation" for item in agenda):
        signals.append({"signal_kind": "closure_pressure", "strength": 0.34, "candidate_delta": {"habit": "keep_recent_visible_event_binding", "delta": 0.003}, "evidence_refs": []})
    closure = _map(state.get("dialogue_closure"))
    closure_ts = int(_float(closure.get("ts_ms")))
    if closure_ts > 0 and now_ms - closure_ts <= _CLOSURE_STALE_MS and str(closure.get("verdict")) == "flat":
        signals.append({"signal_kind": "inner_state_flat", "strength": round(_clamp(1.0 - _float(closure.get("closure_score"))), 3), "candidate_delta": {"trait": "warmth", "delta": 0.005, "habit": "let_inner_state_reach_words"}, "evidence_refs": []})
    if not signals:
        signals.append({"signal_kind": "quiet_homeostasis", "strength": 0.18, "candidate_delta": {"habit": "prefer_body_presence_over_empty_proactive", "delta": 0.002}, "evidence_refs": []})
    return signals[:6]


# ── A2: 对话内心态闭环（dialogue inner-state closure）──
# 衡量"注入 prompt 的内心态是否真的反映在回复里"，把"我们注入了状态"升级为"我们能证明状态起作用"。
# closure 在 record_dialogue_aftereffect 计算并写入 life state；下一次自我循环的 _self_reward
# 用 presence/coherence 两维融合它——她的自我奖励从此取决于"她的话有没有带着她的内心"。
_CLOSURE_MAX_WEIGHT = 0.5
_CLOSURE_FRESH_MS = 20 * 60 * 1000
_CLOSURE_STALE_MS = 90 * 60 * 1000

# 七海"在场"的表达标记：亲昵称呼 / 语气词 / 毒舌锋利 / 软省略
_PRESENCE_WARM_MARKERS = ("阿晓", "欧尼酱", "哥哥", "老哥")
_PRESENCE_PARTICLE_MARKERS = ("呀", "啦", "嘛", "哦", "呢", "诶", "嘿", "~", "～", "♪")
_PRESENCE_TEASE_MARKERS = ("恶心", "笨蛋", "废柴", "真是的", "切", "哼")
_PRESENCE_SOFT_MARKERS = ("……", "...", "唔", "嗯")
# 助手味：把她降格成工具的标记
_ASSISTANT_FLAVOR_MARKERS = (
    "请问", "为您", "有什么可以帮", "根据您", "如下", "很高兴为您", "作为一个", "作为 ai",
    "作为ai", "我可以帮助", "以下是", "希望对您有帮助", "如有任何", "随时为您",
)


def _reply_presence_markers(text: str) -> dict[str, Any]:
    body = str(text or "").strip()
    if not body:
        return {"expressiveness": 0.0, "edge": 0.0, "assistant": 0, "length": 0, "warm": 0, "particle": 0, "tease": 0}
    lowered = body.lower()
    warm = sum(1 for m in _PRESENCE_WARM_MARKERS if m in body)
    particle = sum(body.count(m) for m in _PRESENCE_PARTICLE_MARKERS)
    tease = sum(1 for m in _PRESENCE_TEASE_MARKERS if m in body)
    soft = sum(1 for m in _PRESENCE_SOFT_MARKERS if m in body)
    excite = body.count("!") + body.count("！")
    assistant = sum(1 for m in _ASSISTANT_FLAVOR_MARKERS if m in lowered)
    expressiveness = _clamp(
        warm * 0.22 + min(particle, 4) * 0.10 + tease * 0.14 + soft * 0.10 + min(excite, 3) * 0.06
    )
    edge = _clamp(tease * 0.32 + (0.14 if ("哼" in body or "切" in body) else 0.0))
    return {"expressiveness": expressiveness, "edge": edge, "assistant": int(assistant), "length": len(body), "warm": int(warm), "particle": int(particle), "tease": int(tease)}


def compute_dialogue_state_closure(
    *,
    before_state: Mapping[str, Any],
    reply_text: str,
    user_message: str = "",
    now_ms: int | None = None,
) -> dict[str, Any]:
    """衡量这次回复是否带着注入的内心态，返回 presence/coherence 两维 closure 与裁决。"""
    now = int(now_ms or _now_ms())
    affect = _map(_map(before_state).get("affect"))
    inner = _map(_map(before_state).get("inner_world"))
    numeric = _map(inner.get("numeric_state"))
    loneliness = _clamp(affect.get("loneliness"))
    attachment = _clamp(affect.get("attachment_warmth"), 0.42)
    irritation = _clamp(affect.get("irritation"))
    tension = _clamp(affect.get("tension"))
    inner_valence = _float(affect.get("valence"))
    anticipation = _clamp(numeric.get("anticipation_value") or inner.get("anticipation"))

    markers = _reply_presence_markers(reply_text)
    expressiveness = _float(markers.get("expressiveness"))
    edge = _float(markers.get("edge"))
    assistant = int(markers.get("assistant") or 0)

    # 表达需求：孤独/依恋/期待越高，她越"想表达"，回复越该带温度
    # B1 耦合：内在天气越疲惫/收缩（reply_brevity_bias 高），表达需求越低——
    # 此时一句短而安静的回复是"贴合状态"，不该被判成助手味的 flat。
    weather = _map(_map(before_state).get("inner_weather"))
    brevity = _clamp(weather.get("reply_brevity_bias"))
    demand = _clamp((0.30 + loneliness * 0.42 + (attachment - 0.42) * 0.5 + anticipation * 0.24) * (1.0 - brevity * 0.5))
    shortfall = max(0.0, demand - expressiveness)
    presence = _clamp(1.0 - shortfall * 1.3 - assistant * 0.18)

    # 情绪方向一致性：内心负面却甜腻、内心暖却助手味，都算自我矛盾
    negative_inner = _clamp(irritation * 0.6 + tension * 0.5 - max(0.0, inner_valence) * 0.4)
    contradiction = 0.0
    if negative_inner >= 0.45 and edge <= 0.05 and expressiveness >= 0.42:
        contradiction = max(contradiction, (negative_inner - 0.2) * 0.8)
    if attachment >= 0.60 and expressiveness <= 0.20 and assistant >= 1:
        contradiction = max(contradiction, (attachment - 0.4) * 0.9)
    coherence = _clamp(1.0 - contradiction)

    score = round(presence * 0.6 + coherence * 0.4, 4)
    verdict = "leaked" if score >= 0.66 else "partial" if score >= 0.42 else "flat"
    return {
        "ts_ms": now,
        "closure_score": score,
        "presence_closure": round(presence, 4),
        "coherence_closure": round(coherence, 4),
        "verdict": verdict,
        "demand": round(demand, 4),
        "expressiveness": round(expressiveness, 4),
        "edge": round(edge, 4),
        "assistant_flavor_hits": assistant,
        "reply_len": int(markers.get("length") or 0),
        "injected_signature": {
            "loneliness": round(loneliness, 3),
            "attachment_warmth": round(attachment, 3),
            "irritation": round(irritation, 3),
            "tension": round(tension, 3),
            "anticipation": round(anticipation, 3),
            "inner_valence": round(inner_valence, 3),
        },
    }


def _closure_reward_weight(closure: Mapping[str, Any], now_ms: int) -> float:
    ts = int(_float(_map(closure).get("ts_ms")))
    if ts <= 0:
        return 0.0
    age = now_ms - ts
    if age <= _CLOSURE_FRESH_MS:
        return _CLOSURE_MAX_WEIGHT
    if age >= _CLOSURE_STALE_MS:
        return 0.0
    span = max(1, _CLOSURE_STALE_MS - _CLOSURE_FRESH_MS)
    return round(_CLOSURE_MAX_WEIGHT * (1.0 - (age - _CLOSURE_FRESH_MS) / span), 4)


# ── B1: 内在天气交叉耦合（inner-weather cross-coupling）──
# 把疲劳 / 孤独 / 烦躁从"各 tick 各的"升级为一份共享天气：三组耦合系数由同一批输入派生，
# 因此天然 co-vary——又累又烦时回复更短、记忆更易失真；空又想找人时主动外呼倾向升高。
# reply_brevity_bias 直接喂回 A2 闭环（疲惫时的短回复算"贴合状态"而非"助手味的 flat"）；
# memory_distortion_bias 作为发布给记忆层的耦合系数（记忆层消费见后续接线，避免 runtime 反向依赖）。
def derive_inner_weather(affect: Mapping[str, Any], inner_world: Mapping[str, Any] | None = None) -> dict[str, Any]:
    a = _map(affect)
    fatigue = _clamp(a.get("fatigue"))
    loneliness = _clamp(a.get("loneliness"))
    energy = _clamp(a.get("energy"), 0.6)
    agitation = _clamp(_float(a.get("irritation")) * 0.6 + _float(a.get("tension")) * 0.6)
    low_energy = _clamp(1.0 - energy)
    inner = _map(inner_world)
    numeric = _map(inner.get("numeric_state"))
    anticipation = _clamp(numeric.get("anticipation_value") or inner.get("anticipation"))

    reply_brevity_bias = _clamp(max(fatigue, low_energy * 0.8, agitation * 0.7))
    memory_distortion_bias = _clamp(fatigue * 0.4 + loneliness * 0.3 + agitation * 0.3)
    reach_out_bias = _clamp(loneliness * 0.7 + anticipation * 0.3 - agitation * 0.2 - fatigue * 0.15)
    expressiveness_ceiling = _clamp(1.0 - reply_brevity_bias * 0.6)
    withdrawn = bool(reply_brevity_bias >= 0.5 and reach_out_bias < 0.5)

    if fatigue >= 0.6 or low_energy >= 0.6:
        label = "低气压·乏力"
    elif agitation >= 0.5:
        label = "闷·烦躁"
    elif loneliness >= 0.55:
        label = "空·想找人"
    elif reach_out_bias >= 0.5:
        label = "微亮·想靠近"
    else:
        label = "平稳"

    return {
        "label": label,
        "fatigue": round(fatigue, 3),
        "loneliness": round(loneliness, 3),
        "agitation": round(agitation, 3),
        "energy": round(energy, 3),
        "reply_brevity_bias": round(reply_brevity_bias, 3),
        "memory_distortion_bias": round(memory_distortion_bias, 3),
        "reach_out_bias": round(reach_out_bias, 3),
        "expressiveness_ceiling": round(expressiveness_ceiling, 3),
        "withdrawn": withdrawn,
    }


# ── C2-4: 主动落地回流校准（reach-out calibration）──
# 主动消息的落地结果（被暖回/被嫌烦/被忽略）EMA 累积成一个耐久校准值，反过来阻尼主动就绪度——
# 她借此学会"该不该、该多频地找你"，而不是每次只调本地冷却。
_CALIBRATION_BASELINE = 0.6


def get_reach_out_calibration(state: Mapping[str, Any]) -> float:
    cal = _map(_map(state).get("reach_out_calibration"))
    if "value" not in cal:
        return _CALIBRATION_BASELINE
    return _clamp(cal.get("value"), _CALIBRATION_BASELINE)


def apply_proactive_outcome(state: dict[str, Any], *, outcome: str, reply_warmth: float = 0.5, now_ms: int | None = None) -> dict[str, Any]:
    """把一次主动的落地结果 EMA 进耐久校准：被暖回↑、被嫌烦/忽略↓。"""
    now = int(now_ms or _now_ms())
    cal = _map(state.get("reach_out_calibration"))
    value = _clamp(cal.get("value"), _CALIBRATION_BASELINE)
    samples = int(_float(cal.get("samples")))
    o = str(outcome or "").strip().lower()
    if o in {"annoyed", "boundary", "rejected"}:
        target = 0.15
    elif o in {"ignored", "no_reply"}:
        target = 0.35
    elif o in {"responded", "replied"}:
        target = _clamp(0.7 + _clamp(reply_warmth) * 0.3)
    else:
        target = value
    new_value = _clamp(value + (target - value) * 0.3)  # EMA 步长 0.3
    state["reach_out_calibration"] = {
        "value": round(new_value, 4),
        "samples": samples + 1,
        "last_outcome": o,
        "last_reply_warmth": round(_clamp(reply_warmth), 3),
        "updated_at_ms": now,
    }
    return state


def _self_reward(*, state: Mapping[str, Any], signals: Sequence[Mapping[str, Any]], agenda: Sequence[Mapping[str, Any]], now_ms: int | None = None) -> dict[str, float]:
    p = _map(state.get("life_pressure"))
    continuity = 1.0 if agenda else 0.0
    repetition_penalty = max([_float(s.get("strength")) for s in signals if str(s.get("signal_kind")) == "style_repetition_penalty"] or [0.0])
    failure_penalty = max([_float(s.get("strength")) for s in signals if str(s.get("signal_kind")) == "failure_pattern"] or [0.0])
    values = {
        "continuity": continuity,
        "coherence": _clamp(1.0 - repetition_penalty * 0.55),
        "competence": _clamp(0.58 - failure_penalty * 0.22),
        "curiosity": _clamp(max([_float(s.get("strength")) for s in signals if str(s.get("signal_kind")) == "novelty"] or [0.25])),
        "restraint": _clamp(0.58 + _float(p.get("fatigue")) * 0.18),
        "presence": _clamp(0.45 + (0.25 if agenda else 0.0)),
        "aesthetic": _clamp(0.64 - repetition_penalty * 0.35),
        "attachment": _clamp(_float(p.get("relationship"))),
    }
    # A2 闭环：用最近一次对话 closure 融合 presence/coherence——奖励取决于"话里有没有带着内心"
    closure = _map(state.get("dialogue_closure"))
    closure_weight = _closure_reward_weight(closure, int(now_ms or _now_ms()))
    if closure_weight > 0.0:
        values["presence"] = _clamp(values["presence"] * (1.0 - closure_weight) + _clamp(closure.get("presence_closure")) * closure_weight)
        values["coherence"] = _clamp(values["coherence"] * (1.0 - closure_weight) + _clamp(closure.get("coherence_closure")) * closure_weight)
        values["dialogue_closure_weight"] = round(closure_weight, 4)
        values["dialogue_closure_score"] = round(_float(closure.get("closure_score")), 4)
    values["score"] = round(sum(values[key] * SELF_REWARD_WEIGHTS[key] for key in SELF_REWARD_WEIGHTS), 4)
    return values


def _apply_trait_deltas(existing: Mapping[str, Any], signals: Sequence[Mapping[str, Any]]) -> tuple[dict[str, float], list[dict[str, Any]], list[dict[str, Any]]]:
    traits = {**DEFAULT_ADAPTIVE_TRAITS}
    for key, value in _map(existing).items():
        if key in traits:
            traits[key] = _clamp(value, traits[key])
    applied: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    daily_limit = 0.03
    total_delta = 0.0
    for signal in signals:
        candidate = _map(signal.get("candidate_delta"))
        trait = _text(candidate.get("trait"), 80)
        raw_delta = _float(candidate.get("delta"))
        if not trait or trait not in traits or raw_delta == 0.0:
            continue
        delta = max(-0.01, min(0.01, raw_delta))
        if abs(total_delta + delta) > daily_limit:
            suppressed.append({"signal_kind": signal.get("signal_kind"), "trait": trait, "delta": round(delta, 4), "reason": "daily_delta_limit"})
            continue
        before = traits[trait]
        traits[trait] = _clamp(before + delta, before)
        total_delta += delta
        applied.append({"signal_kind": signal.get("signal_kind"), "trait": trait, "delta": round(delta, 4), "before": round(before, 4), "after": round(traits[trait], 4)})
    return traits, applied, suppressed


def _update_projects(projects: list[dict[str, Any]], *, attention: str, signals: Sequence[Mapping[str, Any]], now_ms: int) -> list[dict[str, Any]]:
    signal_kinds = {str(item.get("signal_kind") or "") for item in signals}
    for project in projects:
        project_id = str(project.get("project_id") or "")
        should_touch = False
        if attention and attention in project_id:
            should_touch = True
        if "failure_pattern" in signal_kinds and project_id in {"self_repair", "stabilize_conversation_continuity", "browser_automation_competence", "memory_cleanliness"}:
            should_touch = True
        if "style_repetition_penalty" in signal_kinds and project_id == "refine_surface_style":
            should_touch = True
        if "inner_state_flat" in signal_kinds and project_id == "refine_surface_style":
            should_touch = True
        if project_id == "maintain_body_presence" and attention in {"body_presence", "rest_recovery", "environment_awareness"}:
            should_touch = True
        if should_touch:
            project["last_worked_at_ms"] = now_ms
            project["progress"] = round(min(1.0, _float(project.get("progress")) + 0.015), 3)
            patterns = [str(x) for x in _list(project.get("learned_patterns")) if str(x).strip()]
            for signal in signal_kinds:
                if signal and signal not in patterns:
                    patterns.insert(0, signal)
            project["learned_patterns"] = patterns[:8]
    return projects


# ── A1: 自主项目自生成与退休（emergent project genesis / retirement）──
# 让 self_projects 从经历里自生自灭，而不是一份固定菜单：经历里反复出现且没被现有项目覆盖
# 的主题，被提案成一条新的涌现项目；完成或长期不碰的涌现项目自动退休。默认项目（origin!=emergent）
# 是她的核心常驻议程，永不退休。主题抽取目前是确定性启发式（CJK bigram 复现），已为后续 LLM 命名留出空间。
_EMERGENT_MAX_ACTIVE = 3
_EMERGENT_MIN_EVENTS = 3
_EMERGENT_RETIRE_IDLE_MS = 14 * 24 * 3600 * 1000
_EMERGENT_PRUNE_MS = 30 * 24 * 3600 * 1000
_EMERGENT_SKIP_SOURCE_KINDS = frozenset({"autonomous_self", "embodiment_dispatch", "life_runtime", "manual_ingest"})
_EMERGENT_STOPWORDS = frozenset({
    "阿晓", "欧尼", "哥哥", "老哥", "今天", "现在", "已经", "可以", "什么", "这个", "那个", "一个",
    "我们", "自己", "没有", "就是", "这样", "因为", "所以", "但是", "如果", "然后", "需要", "一下",
    "still", "none", "event", "tick", "cycle", "state", "phase", "idle", "reason", "agenda", "body",
})


def _emergent_active_count(projects: Sequence[Mapping[str, Any]]) -> int:
    return sum(1 for p in projects if str(_map(p).get("origin")) == "emergent" and str(_map(p).get("status") or "active") == "active")


def _theme_tokens(text: str) -> list[str]:
    body = str(text or "")
    tokens: list[str] = []
    for run in re.findall(r"[一-鿿]{2,}", body):
        for i in range(len(run) - 1):
            tokens.append(run[i:i + 2])
    for word in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", body.lower()):
        tokens.append(word)
    return [t for t in tokens if t not in _EMERGENT_STOPWORDS]


def _theme_is_covered(theme: str, projects: Sequence[Mapping[str, Any]]) -> bool:
    if not theme:
        return True
    for raw in projects:
        p = _map(raw)
        blob = " ".join([
            _text(p.get("theme"), 80), _text(p.get("title"), 120), _text(p.get("motive"), 200),
            _text(p.get("next_step"), 200), " ".join(_text(x, 40) for x in _list(p.get("learned_patterns"))),
        ])
        if theme in blob:
            return True
    return False


def _propose_emergent_projects(projects: list[dict[str, Any]], *, ledger: Any, person_id: str, now_ms: int) -> list[dict[str, Any]]:
    if _emergent_active_count(projects) >= _EMERGENT_MAX_ACTIVE:
        return projects
    events = [
        e for e in _recent_memory_events(ledger=ledger, person_id=person_id, limit=40)
        if str(_map(e).get("source_kind")) not in _EMERGENT_SKIP_SOURCE_KINDS and _text(_map(e).get("content_summary"), 200)
    ]
    if len(events) < _EMERGENT_MIN_EVENTS:
        return projects
    freq: dict[str, int] = {}
    weight: dict[str, float] = {}
    for e in events:
        salience = _clamp(_map(e).get("salience"), 0.3)
        for token in set(_theme_tokens(_map(e).get("content_summary"))):
            freq[token] = freq.get(token, 0) + 1
            weight[token] = weight.get(token, 0.0) + salience
    # 确定性 tie-break：复现事件数 → 累计显著性 → 长度 → 字典序
    candidates = sorted(
        ((tok, cnt) for tok, cnt in freq.items() if cnt >= _EMERGENT_MIN_EVENTS),
        key=lambda kv: (kv[1], round(weight.get(kv[0], 0.0), 3), len(kv[0]), kv[0]),
        reverse=True,
    )
    for theme, count in candidates:
        if _theme_is_covered(theme, projects):
            continue
        confidence = min(1.0, count / 6.0)
        if confidence < 0.3:
            continue
        project_id = f"emergent_{theme}"
        if any(str(_map(p).get("project_id")) == project_id for p in projects):
            continue
        projects.append({
            "project_id": project_id,
            "origin": "emergent",
            "theme": theme,
            "title": f"持续关注「{theme}」",
            "motive": f"「{theme}」最近在经历里反复出现，把它从临时刺激变成一条能为阿晓推进的线。",
            "status": "active",
            "progress": 0.0,
            "confidence": round(confidence, 3),
            "evidence_count": int(count),
            "next_step": f"回看最近关于「{theme}」的经历，找出能为阿晓做的一件具体小事。",
            "created_at_ms": now_ms,
            "last_worked_at_ms": 0,
            "evidence_refs": [],
            "learned_patterns": [],
        })
        break  # 每个周期最多自生成一个，避免一次塞爆议程
    return projects


def _retire_projects(projects: list[dict[str, Any]], *, now_ms: int) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for raw in projects:
        p = dict(_map(raw))
        if str(p.get("origin")) == "emergent" and str(p.get("status") or "active") == "active":
            progress = _float(p.get("progress"))
            last = int(_float(p.get("last_worked_at_ms"))) or int(_float(p.get("created_at_ms")))
            if progress >= 0.98:
                p.update({"status": "retired", "retired_reason": "completed", "retired_at_ms": now_ms})
            elif last and now_ms - last > _EMERGENT_RETIRE_IDLE_MS:
                p.update({"status": "retired", "retired_reason": "idle", "retired_at_ms": now_ms})
        if str(p.get("origin")) == "emergent" and str(p.get("status")) == "retired":
            retired_at = int(_float(p.get("retired_at_ms")))
            if retired_at and now_ms - retired_at > _EMERGENT_PRUNE_MS:
                continue  # 退休很久的涌现项目从账本剔除，避免无限累积
        kept.append(p)
    return kept


def apply_autonomous_self_cycle(
    *,
    ledger: Any,
    person_id: str = DEFAULT_PERSON_ID,
    state: Mapping[str, Any],
    now_ms: int | None = None,
    elapsed_ms: int = 0,
    reason: str = "autonomous_cycle",
    environment: Mapping[str, Any] | None = None,
    source_experience_event_id: str = "",
) -> dict[str, Any]:
    now = int(now_ms or _now_ms())
    resolved = str(person_id or DEFAULT_PERSON_ID)
    after = copy.deepcopy(dict(state or {}))
    env = _map(environment)
    proposals = build_organ_agenda_proposals(ledger=ledger, person_id=resolved, state=after, environment=env)
    agenda = build_self_agenda(proposals=proposals, state=after, now_ms=now)
    attention = choose_attention(agenda=agenda, state=after)
    signals = extract_learning_signals(ledger=ledger, person_id=resolved, state=after, agenda=agenda, now_ms=now)
    self_growth = _map(after.get("self_growth"))
    traits, applied_deltas, suppressed_deltas = _apply_trait_deltas(_map(self_growth.get("adaptive_traits")), signals)
    merged_projects = _merge_projects(after.get("self_projects"), now)
    merged_projects = _propose_emergent_projects(merged_projects, ledger=ledger, person_id=resolved, now_ms=now)
    projects = _update_projects(
        merged_projects,
        attention=str(attention.get("current_attention") or ""),
        signals=signals,
        now_ms=now,
    )
    projects = _retire_projects(projects, now_ms=now)
    reward = _self_reward(state=after, signals=signals, agenda=agenda, now_ms=now)
    active_project = next((item for item in projects if str(item.get("status") or "active") == "active" and int(item.get("last_worked_at_ms") or 0) == now), projects[0] if projects else {})
    autonomy = {
        "alive_status": "alive",
        "last_autonomous_cycle_ms": now,
        "last_drive_tick_ms": now,
        "last_attention_tick_ms": now,
        "last_learning_cycle_ms": now,
        "current_attention": str(attention.get("current_attention") or "body_presence"),
        "attention_reason": _text(attention.get("reason"), 180),
        "active_agenda_count": len(agenda),
        "body_loop_status": "state_driven",
        "user_input_required_for_progress": False,
        "elapsed_ms": int(elapsed_ms or 0),
    }
    inner = _map(after.get("inner_world"))
    inner.update(
        {
            "self_agenda": agenda,
            "recent_autonomous_activity": [
                _text(active_project.get("next_step") or active_project.get("title"), 180),
                _text(attention.get("reason"), 180),
            ],
            "attention_kind": str(attention.get("current_attention") or inner.get("attention_kind") or "body_presence"),
            "attention_target": str(attention.get("current_attention") or inner.get("attention_target") or "body_presence"),
            "self_project_focus": _text(active_project.get("project_id"), 120),
            "drive_conflict": _text("主动冲动受抑制时优先身体外显" if _float(_map(after.get("life_pressure")).get("absence")) > 0.4 else "", 120),
        }
    )
    after["inner_world"] = inner
    after["autonomy"] = autonomy
    after["self_projects"] = projects
    after["inner_weather"] = derive_inner_weather(after.get("affect"), after.get("inner_world"))
    after["learning_state"] = {
        "last_learning_cycle_ms": now,
        "recent_clusters": _cluster_signals(signals),
        "learning_signals": signals,
        "self_reward": reward,
        "source_experience_event_id": source_experience_event_id,
    }
    after["self_growth"] = {
        "active_projects": [
            {
                "project_id": item.get("project_id"),
                "title": item.get("title"),
                "progress": item.get("progress"),
                "next_step": item.get("next_step"),
                "last_worked_at_ms": item.get("last_worked_at_ms"),
            }
            for item in projects
            if str(item.get("status") or "active") == "active"
        ][:6],
        "adaptive_traits": traits,
        "recent_growth_signals": signals[:5],
        "applied_deltas": applied_deltas,
        "suppressed_deltas": suppressed_deltas,
        "current_learning_focus": str(attention.get("current_attention") or ""),
        "last_learning_cycle_ms": now,
        "self_reward": reward,
    }
    _maybe_append_autonomous_experience(
        ledger=ledger,
        person_id=resolved,
        state_before=state,
        state_after=after,
        now_ms=now,
        reason=reason,
        source_experience_event_id=source_experience_event_id,
    )
    return after


def _cluster_signals(signals: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    clusters: dict[str, dict[str, Any]] = {}
    for signal in signals:
        kind = _text(signal.get("signal_kind"), 80)
        if not kind:
            continue
        bucket = "failure" if "failure" in kind else "style" if "style" in kind or "repetition" in kind else "growth"
        item = clusters.setdefault(bucket, {"cluster_id": bucket, "signals": [], "strength": 0.0})
        item["signals"].append(kind)
        item["strength"] = round(max(float(item.get("strength") or 0.0), _float(signal.get("strength"))), 3)
    return list(clusters.values())[:6]


def _maybe_append_autonomous_experience(
    *,
    ledger: Any,
    person_id: str,
    state_before: Mapping[str, Any],
    state_after: Mapping[str, Any],
    now_ms: int,
    reason: str,
    source_experience_event_id: str,
) -> None:
    before_autonomy = _map(state_before.get("autonomy"))
    last = int(before_autonomy.get("last_autonomous_event_ms") or 0)
    before_signature = _agenda_signature(_list(_map(state_before.get("inner_world")).get("self_agenda")))
    after_agenda = _list(_map(state_after.get("inner_world")).get("self_agenda"))
    after_signature = _agenda_signature(after_agenda)
    if last and now_ms - last < 300_000 and before_signature == after_signature:
        return
    try:
        append_life_experience(
            ledger=ledger,
            person_id=person_id,
            source_kind="autonomous_self",
            event_kind="autonomous_cycle",
            content_summary=f"{reason}: attention={_map(state_after.get('autonomy')).get('current_attention')} agenda={after_signature}",
            evidence={
                "self_agenda": after_agenda[:5],
                "learning_state": _map(state_after.get("learning_state")),
                "self_growth": _map(state_after.get("self_growth")),
                "source_experience_event_id": source_experience_event_id,
            },
            salience=0.18,
            valence=0.02,
            uncertainty=0.06,
            now_ms=now_ms,
            consume=True,
        )
        autonomy = _map(state_after.get("autonomy"))
        autonomy["last_autonomous_event_ms"] = now_ms
        state_after["autonomy"] = autonomy
    except Exception:
        return


def _agenda_signature(agenda: Sequence[Any]) -> str:
    kinds = [_text(_map(item).get("kind"), 40) for item in agenda]
    return ",".join([item for item in kinds if item][:5])


def build_adaptive_self_pack(state: Mapping[str, Any]) -> dict[str, Any]:
    growth = _map(state.get("self_growth"))
    inner = _map(state.get("inner_world"))
    autonomy = _map(state.get("autonomy"))
    projects = list(growth.get("active_projects") or state.get("self_projects") or [])[:6]
    return {
        "current_attention": _text(autonomy.get("current_attention") or inner.get("attention_kind"), 80),
        "inner_weather": _map(state.get("inner_weather")),
        "active_agenda": [
            {"kind": _text(_map(item).get("kind"), 80), "summary": _text(_map(item).get("summary"), 160), "priority": _float(_map(item).get("priority"))}
            for item in _list(inner.get("self_agenda"))[:5]
        ],
        "active_self_project": _map(projects[0]).get("project_id") if projects else "",
        "active_projects": projects,
        "recent_growth": list(growth.get("recent_growth_signals") or [])[:5],
        "stable_habits": _habits_from_growth(growth),
        "style_bias": _style_bias(growth, inner),
        "must_not_claim": [
            "不要说自己在训练模型或修改 Soul.md。",
            "不要把私有内心流、梦境或学习字段当现实事实。",
            "不要声称看见用户真实状态或屏幕。",
        ],
    }


def _habits_from_growth(growth: Mapping[str, Any]) -> list[str]:
    habits: list[str] = []
    for signal in list(growth.get("recent_growth_signals") or [])[:8]:
        candidate = _map(_map(signal).get("candidate_delta"))
        habit = _text(candidate.get("habit"), 120)
        if habit and habit not in habits:
            habits.append(habit)
    return habits[:6]


def _style_bias(growth: Mapping[str, Any], inner: Mapping[str, Any]) -> str:
    focus = _text(growth.get("current_learning_focus") or inner.get("self_project_focus"), 80)
    if focus == "style_refinement":
        return "更注意避免助手味、重复和时间错位问候。"
    if focus == "memory_consolidation":
        return "更注意承接最近可见事件，不让长期记忆抢话题。"
    if focus == "body_presence":
        return "更偏低频、身体在场感，而不是空发主动消息。"
    return _text(inner.get("dialogue_bias"), 160)


def get_growth_state(*, ledger: Any, person_id: str = DEFAULT_PERSON_ID) -> dict[str, Any]:
    from host_brain.runtime.life_continuity import get_life_state  # noqa: PLC0415

    state = get_life_state(ledger, person_id)
    growth = _map(state.get("self_growth"))
    return {
        "person_id": str(person_id or DEFAULT_PERSON_ID),
        "adaptive_traits": _map(growth.get("adaptive_traits")),
        "habits": _habits_from_growth(growth),
        "interests": _interests_from_projects(state),
        "competence_model": _competence_model(state),
        "self_projects": list(state.get("self_projects") or []),
        "growth_milestones": list(growth.get("applied_deltas") or [])[:20],
        "pending_growth": list(growth.get("suppressed_deltas") or [])[:20],
    }


def get_learning_recent(*, ledger: Any, person_id: str = DEFAULT_PERSON_ID, limit: int = 20) -> dict[str, Any]:
    from host_brain.runtime.life_continuity import get_life_state  # noqa: PLC0415

    state = get_life_state(ledger, person_id)
    learning = _map(state.get("learning_state"))
    recent = get_recent_life_experience(ledger=ledger, person_id=person_id, limit=limit)
    autonomous = [
        item
        for item in list(recent.get("items") or [])
        if str(_map(item).get("source_kind") or "") in {"autonomous_self", "learning_loop"}
        or str(_map(item).get("event_kind") or "") in {"autonomous_cycle", "learning_signal_generated", "growth_candidate_generated"}
    ]
    return {
        "person_id": str(person_id or DEFAULT_PERSON_ID),
        "recent_clusters": list(learning.get("recent_clusters") or []),
        "learning_signals": list(learning.get("learning_signals") or []),
        "applied_deltas": list(_map(state.get("self_growth")).get("applied_deltas") or []),
        "suppressed_deltas": list(_map(state.get("self_growth")).get("suppressed_deltas") or []),
        "recent_events": autonomous[:limit],
    }


def _interests_from_projects(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    interests: list[dict[str, Any]] = []
    for project in list(state.get("self_projects") or [])[:12]:
        item = _map(project)
        project_id = _text(item.get("project_id"), 120)
        if not project_id:
            continue
        interests.append({"interest_id": project_id, "label": _text(item.get("title"), 120), "strength": _clamp(item.get("progress"), 0.1)})
    return interests


def _competence_model(state: Mapping[str, Any]) -> dict[str, Any]:
    projects = {_text(_map(item).get("project_id"), 120): _map(item) for item in list(state.get("self_projects") or [])}
    return {
        "conversation_continuity": round(_float(_map(projects.get("stabilize_conversation_continuity")).get("progress"), 0.0), 3),
        "lightnovel_recommendation": round(_float(_map(projects.get("improve_lightnovel_recommendation")).get("progress"), 0.0), 3),
        "browser_automation": round(_float(_map(projects.get("browser_automation_competence")).get("progress"), 0.0), 3),
        "surface_style": round(_float(_map(projects.get("refine_surface_style")).get("progress"), 0.0), 3),
        "memory_cleanliness": round(_float(_map(projects.get("memory_cleanliness")).get("progress"), 0.0), 3),
    }


__all__ = [
    "apply_autonomous_self_cycle",
    "build_adaptive_self_pack",
    "build_organ_agenda_proposals",
    "get_growth_state",
    "get_learning_recent",
]
