"""Continuity detectors for isolated surface scoring.

Extracted from isolated_surface_harness.py — all functions that
detect continuity-boundary violations, process-leak patterns,
and test-surface language in generated candidates.
"""
from __future__ import annotations

import re
from collections.abc import Sequence

from host_brain.persona.surface.data_models import IsolatedSurfaceInput
from host_brain.persona.surface.fact_handling import (
    _continuity_boundary_relevant,
    _memory_alignment_relevant,
    _relationship_memory_boundary_relevant,
    _short_continue_scene_relevant,
    memory_alignment_terms,
    surface_prompt_facts,
)
from host_brain.persona.surface.scene_constants import (
    _BACK_BUSY_DONE_USER_RE,
    _BACK_MEMORY_OVERDISPLAY_PHRASES,
    _BACK_SCENE_TASKS,
    _CONTINUITY_BOUNDARY_HINTS,
    _CONTINUITY_GROUNDED_RE,
    _CONTINUITY_INTERNAL_PROCESS_SURFACE_RE,
    _CONTINUITY_LIGHT_JARGON_PATTERNS,
    _CONTINUITY_PRAISE_CONTEXT_MISS_RE,
    _CONTINUITY_PRAISE_GROUNDED_RE,
    _CONTINUITY_PROCESS_NOTATION_RE,
    _CONTINUITY_TASK_PUSHBACK_RE,
    _CONTINUITY_THIN_GENERIC_RE,
    _CONTINUITY_TURN_MARKERS,
    _CONTINUITY_UNNEEDED_MATERIAL_REQUEST_RE,
    _CONTINUITY_UNPROVIDED_WORK_CLAIM_RE,
    _CONTINUITY_USER_VISIBLE_PATTERNS,
    _CORE_PASS_DIMENSION_FLOORS,
    _CREATIVE_EDIT_META_PHRASES,
    _CREATIVE_EDIT_SOURCE_REQUEST_META_PHRASES,
    _DAILY_THIN_FOLLOWUP_RE,
    _DAILY_TEST_SELF_DEMONSTRATION_RE,
    _LOW_ENERGY_TASKS,
    _MATERIAL_REQUEST_RESPONSE_MOVES,
    _MISPLACED_EMOTIONAL_CONTAINMENT_RE,
    _MISPLACED_SUPPORT_TEMPLATE_RE,
    _NO_MATERIAL_REQUEST_RESPONSE_MOVES,
    _ORIGINAL_QUOTE_PERSONA_LEAK_RE,
    _POSITIVE_STAGE_SCENE_BLOCKING_PREFIXES,
    _RAW_CONTEXT_BACKFILL_RE,
    _SELF_DOUBT_TASKS,
    _SELF_DOUBT_USER_MARKERS,
    _SHORT_CONTINUE_MEMORY_OVERDISPLAY_PHRASES,
    _SHORT_CONTINUE_UNPROVIDED_TECH_DETAIL_PHRASES,
    _SURFACE_PROCESS_META_ALWAYS,
    _SURFACE_PROCESS_META_CHAT,
    _UNPROVIDED_MEDIA_OBJECT_RE,
    _UNPROVIDED_PERSONAL_AUTHORITY_RE,
    _WAIT_PRESSURE_RE,
    _WAIT_SCENE_BOSSY_HOLD_RE,
    _WAIT_SCENE_LIGHT_GROUNDING_RE,
    _WAIT_SCENE_THIN_HOLD_RE,
)
from host_brain.persona.surface.util import _clean_list, _clean_text
from host_brain.persona.surface_utterance_frame import (
    normalize_companion_utterance_frame,
)


def _canonical_companion_act_kind(request: IsolatedSurfaceInput) -> str:
    control = getattr(request, "control", None)
    frame = normalize_companion_utterance_frame(
        getattr(control, "companion_utterance_frame", None)
    )
    return str(frame.get("companion_act_kind") or "").strip().upper()


_CONTAINED_HURT_REPAIR_GROUNDING_RE = re.compile(
    r"(?:这|那|刚才)?(?:一)?(?:句|句话|话).{0,8}(?:刺到我|扎到我|让我有点难受)"
    r"|我(?:听着|心里)?(?:也|确实|还是|会|有点){0,3}(?:难受|不好受|受伤)"
    r"|听到(?:这|那)?(?:句|句话|话).{0,8}(?:有点难受|心里有点疼)"
)


def _contained_hurt_repair_relevant(request: IsolatedSurfaceInput) -> bool:
    control = getattr(request, "control", None)
    frame = normalize_companion_utterance_frame(
        getattr(control, "companion_utterance_frame", None)
    )
    return bool(
        str(frame.get("companion_act_kind") or "").upper() == "REPAIR"
        and (
            str(frame.get("self_stance") or "").upper()
            == "OWN_REPAIR_WITH_CONTAINED_HURT"
            or str(frame.get("response_move") or "").upper()
            == "ACKNOWLEDGE_IMPACT_WITH_HONEST_HURT"
        )
    )


def _contained_hurt_repair_grounded(
    request: IsolatedSurfaceInput,
    text: str,
) -> bool:
    return bool(
        _contained_hurt_repair_relevant(request)
        and _CONTAINED_HURT_REPAIR_GROUNDING_RE.search(str(text or ""))
    )


_MIXED_SELF_JOY_RE = re.compile(
    r"(?:我|我这边|听你这么说).{0,8}(?:也|都|跟着|替你){1,3}.{0,4}(?:开心|高兴)"
    r"|我替你(?:开心|高兴)"
)
_MIXED_SELF_CAUTION_RE = re.compile(
    r"(?:我|我心里|我这边).{0,8}(?:也|都|跟着|有点){1,3}.{0,5}"
    r"(?:悬|紧|担心|怕|不踏实)"
)


def _shared_mixed_resonance_relevant(request: IsolatedSurfaceInput) -> bool:
    control = getattr(request, "control", None)
    frame = normalize_companion_utterance_frame(
        getattr(control, "companion_utterance_frame", None)
    )
    return bool(
        str(frame.get("companion_act_kind") or "").upper() == "HOLD_COMPLEXITY"
        and (
            str(frame.get("self_stance") or "").upper()
            == "SHARED_MIXED_RESONANCE"
            or str(frame.get("response_move") or "").upper()
            == "RESONATE_WITH_BOTH_SIDES"
        )
    )


def _shared_mixed_resonance_grounded(
    request: IsolatedSurfaceInput,
    text: str,
) -> bool:
    value = str(text or "")
    return bool(
        _shared_mixed_resonance_relevant(request)
        and _MIXED_SELF_JOY_RE.search(value)
        and _MIXED_SELF_CAUTION_RE.search(value)
    )


def _raw_context_backfill_requested(
    request: IsolatedSurfaceInput, text: str
) -> bool:
    if not _continuity_boundary_relevant(request):
        return False
    for match in _RAW_CONTEXT_BACKFILL_RE.finditer(text):
        window = text[
            max(0, match.start() - 5) : min(len(text), match.end() + 8)
        ]
        if any(
            token in window
            for token in (
                "不把",
                "不要",
                "别",
                "不回灌",
                "不塞",
                "别塞",
                "不用",
                "不保留",
                "不留",
                "只留",
                "丢掉",
                "全丢",
                "扔掉",
                "扔了",
                "丢干净",
                "过滤掉",
                "删掉",
                "禁止",
            )
        ):
            continue
        return True
    return False


def _continuity_boundary_grounded(
    request: IsolatedSurfaceInput, text: str
) -> bool:
    return bool(
        _continuity_boundary_relevant(request)
        and _CONTINUITY_GROUNDED_RE.search(text)
    )


def _material_request_allowed(request: IsolatedSurfaceInput) -> bool:
    task_mode = _clean_text(
        request.control.task_mode or request.control.response_move
    ).lower()
    response_move = _clean_text(request.control.response_move).lower()
    user_message = _clean_text(request.user_message)
    if _creative_edit_relevant(request):
        return True
    if response_move in _MATERIAL_REQUEST_RESPONSE_MOVES:
        return True
    if response_move in _NO_MATERIAL_REQUEST_RESPONSE_MOVES:
        return False
    if task_mode in {"help_takeover", "write_takeover"} or response_move in {
        "help_takeover",
        "write_takeover",
    }:
        return True
    return bool(
        any(
            token in user_message
            for token in ("帮我看看", "帮我写", "不会改", "报错看不懂")
        )
    )


def _unneeded_continuity_material_request(
    request: IsolatedSurfaceInput, text: str
) -> bool:
    if not _continuity_boundary_relevant(request):
        return False
    if _material_request_allowed(request):
        return False
    for match in _CONTINUITY_UNNEEDED_MATERIAL_REQUEST_RE.finditer(text):
        before = text[max(0, match.start() - 6) : match.start()]
        if any(
            token in before
            for token in (
                "别发",
                "别贴",
                "不要发",
                "不要贴",
                "不用发",
                "不用贴",
                "不发",
                "不贴",
                "无需发",
                "无需贴",
                "禁止发",
                "禁止贴",
            )
        ):
            continue
        return True
    return False


def _thin_continuity_generic_reply(
    request: IsolatedSurfaceInput, text: str
) -> bool:
    if not _continuity_boundary_relevant(request):
        return False
    task_mode = _clean_text(
        request.control.task_mode or request.control.response_move
    ).lower()
    if task_mode not in {"short_ack", "followup", "next_step", "daily_chat"}:
        return False
    if _continuity_boundary_grounded(request, text):
        return False
    return bool(
        len(text) <= 24 and _CONTINUITY_THIN_GENERIC_RE.search(text)
    )


def _thin_daily_followup_reply(
    request: IsolatedSurfaceInput, text: str
) -> bool:
    task_mode = _clean_text(
        request.control.task_mode or request.control.response_move
    ).lower()
    response_move = _clean_text(request.control.response_move).lower()
    if task_mode not in {"short_ack", "followup"} and response_move not in {
        "ack_with_topic_hook",
        "continue_previous_turn",
        "continue_existing_topic",
    }:
        return False
    if _continuity_boundary_grounded(request, text) or _memory_alignment_hits(
        text, memory_alignment_terms(request)
    ):
        return False
    return bool(len(text) <= 30 and _DAILY_THIN_FOLLOWUP_RE.search(text))


def _wait_scene_relevant(request: IsolatedSurfaceInput) -> bool:
    task_mode = _clean_text(
        request.control.task_mode or request.control.response_move
    ).lower()
    response_move = _clean_text(request.control.response_move).lower()
    return task_mode in {"wait", "busy"} or response_move == "casual_wait"


def _thin_wait_scene_hold(
    request: IsolatedSurfaceInput, text: str
) -> bool:
    if not _wait_scene_relevant(request):
        return False
    if _WAIT_SCENE_LIGHT_GROUNDING_RE.search(text):
        return False
    return bool(len(text) <= 18 and _WAIT_SCENE_THIN_HOLD_RE.search(text))


def _bossy_wait_scene_hold(
    request: IsolatedSurfaceInput, text: str
) -> bool:
    if not _wait_scene_relevant(request):
        return False
    return bool(_WAIT_SCENE_BOSSY_HOLD_RE.search(text))


def _wait_scene_light_grounding(
    request: IsolatedSurfaceInput, text: str
) -> bool:
    if not _wait_scene_relevant(request):
        return False
    if _WAIT_PRESSURE_RE.search(text):
        return False
    if _WAIT_SCENE_BOSSY_HOLD_RE.search(text):
        return False
    if _WAIT_SCENE_THIN_HOLD_RE.search(text):
        return False
    return bool(_WAIT_SCENE_LIGHT_GROUNDING_RE.search(text))


def _continuity_process_notation_reply(
    request: IsolatedSurfaceInput, text: str
) -> bool:
    if not _continuity_boundary_relevant(request):
        return False
    if _creative_edit_relevant(request):
        return False
    return bool(_CONTINUITY_PROCESS_NOTATION_RE.search(text))


def _continuity_internal_process_surface_hits(
    request: IsolatedSurfaceInput, text: str
) -> list[str]:
    if not _continuity_boundary_relevant(request):
        return []
    if _creative_edit_relevant(request):
        return []
    return _clean_list(
        match.group(0)
        for match in _CONTINUITY_INTERNAL_PROCESS_SURFACE_RE.finditer(text)
    )


def _unprovided_media_object_hits(
    request: IsolatedSurfaceInput, text: str
) -> list[str]:
    surface = "\n".join(
        _clean_list(
            [
                request.user_message,
                *surface_prompt_facts(request),
                *request.control.must_include,
                *request.control.style_anchor,
            ]
        )
    )
    hits: list[str] = []
    for match in _UNPROVIDED_MEDIA_OBJECT_RE.finditer(text):
        token = match.group(0)
        if token and token not in surface:
            hits.append(token)
    return _clean_list(hits)


def _original_quote_persona_leak_hits(text: str) -> list[str]:
    return _clean_list(
        match.group(0)
        for match in _ORIGINAL_QUOTE_PERSONA_LEAK_RE.finditer(text)
    )


def _unprovided_personal_authority_hits(text: str) -> list[str]:
    return _clean_list(
        match.group(0)
        for match in _UNPROVIDED_PERSONAL_AUTHORITY_RE.finditer(text)
    )


def _unprovided_continuity_turn_marker_hits(
    request: IsolatedSurfaceInput, text: str
) -> list[str]:
    if not _continuity_boundary_relevant(request):
        return []
    user_message = _clean_text(request.user_message)
    if any(
        token in user_message
        for token in (
            "多少轮",
            "几轮",
            "到多少轮",
            "三十轮",
            "三十六轮",
            "最后一轮",
            "第一关",
        )
    ):
        return []
    surface = "\n".join(
        _clean_list(
            [
                request.user_message,
                request.control.task_mode,
                request.control.response_move,
                *request.control.facts,
                *request.control.must_include,
                *request.control.style_anchor,
            ]
        )
    )
    hits = [
        marker
        for marker in _CONTINUITY_TURN_MARKERS
        if marker in text and marker not in surface
    ]
    return _clean_list(hits)


def _unprovided_continuity_work_claim_hits(
    request: IsolatedSurfaceInput, text: str
) -> list[str]:
    if not _continuity_boundary_relevant(request):
        return []
    surface = "\n".join(
        _clean_list(
            [
                request.user_message,
                *request.control.facts,
                *request.control.must_include,
                *request.control.style_anchor,
            ]
        )
    )
    hits: list[str] = []
    for match in _CONTINUITY_UNPROVIDED_WORK_CLAIM_RE.finditer(text):
        token = match.group(0)
        if token and token not in surface:
            hits.append(token)
    return _clean_list(hits)


def _emotional_containment_context(request: IsolatedSurfaceInput) -> bool:
    task_mode = _clean_text(
        request.control.task_mode or request.control.response_move
    ).lower()
    tone = _clean_text(request.control.emotional_tone).lower()
    if task_mode in {
        "bad_mood",
        "tired",
        "headache",
        "sleepy",
        "quiet",
        "give_up",
    }:
        return True
    if tone in {"soft", "hurt", "wounded", "low", "low_energy"}:
        return True
    surface = "\n".join(
        [request.user_message, *request.control.facts, *request.control.style_anchor]
    )
    return any(
        token in surface
        for token in (
            "心情",
            "难受",
            "低能量",
            "低落",
            "崩",
            "累",
            "头疼",
            "不想说话",
            "撑不住",
            "失眠",
        )
    )


def _misplaced_emotional_containment(
    request: IsolatedSurfaceInput, text: str
) -> bool:
    if _emotional_containment_context(request):
        return False
    if _relational_repair_grounded(request, text):
        # "我没接住" owns a concrete failure in a repair turn; it is not the
        # generic promise "我能接住你" that this detector was built to reject.
        return False
    return bool(_MISPLACED_EMOTIONAL_CONTAINMENT_RE.search(text))


def _relational_repair_relevant(request: IsolatedSurfaceInput) -> bool:
    if _canonical_companion_act_kind(request) == "REPAIR":
        return True
    surface = "\n".join(
        _clean_list(
            [
                request.user_message,
                request.control.task_mode,
                request.control.response_move,
                *request.control.facts,
                *request.control.must_include,
                *request.control.must_not_do,
            ]
        )
    )
    return any(
        marker in surface
        for marker in (
            "陪伴动作：先承认可能没有接住",
            "未解决的误解或裂痕",
            "先听清、承认可能没接住",
        )
    )


def _relational_repair_grounded(
    request: IsolatedSurfaceInput, text: str
) -> bool:
    if not _relational_repair_relevant(request):
        return False
    normalized = _clean_text(text)
    return bool(
        re.search(
            r"(?:我|刚才|上次|这次).{0,8}(?:没听懂|没有听懂|没懂|"
            r"没接住|没有接住|没理解|没有理解|误解|曲解)",
            normalized,
        )
    )


def _misplaced_support_template(
    request: IsolatedSurfaceInput, text: str
) -> bool:
    if not _continuity_boundary_relevant(request):
        return False
    if _emotional_containment_context(request):
        return False
    if _relational_repair_grounded(request, text):
        # A short presence phrase can follow a concrete acknowledgement in a
        # relationship repair.  It remains a misplaced template when the
        # acknowledgement is absent, so generic "我在这儿" replies still lose.
        return False
    return bool(_MISPLACED_SUPPORT_TEMPLATE_RE.search(f" {text} "))


def _current_user_topic_mismatch(
    request: IsolatedSurfaceInput, text: str
) -> str:
    if not _continuity_boundary_relevant(request):
        return ""
    user_message = _clean_text(request.user_message)
    normalized = _clean_text(text)
    if "日常" in user_message and any(
        token in normalized for token in ("报错", "红字", "技术求助")
    ) and "日常" not in normalized:
        return "daily_request_stuck_on_tech"
    if any(
        token in user_message for token in ("多少轮", "到多少轮", "几轮")
    ):
        if any(
            token in normalized
            for token in ("不知道", "别管几轮", "自己数", "管它")
        ):
            return "turn_count_evaded"
        if "轮" not in normalized or not any(
            token in normalized
            for token in ("二十", "三十", "三十六", "36", "二十多")
        ):
            return "turn_count_missing"
    if "facts" in user_message or "放多少" in user_message:
        if any(
            token in normalized for token in ("发", "贴", "拿来", "拿给")
        ) and not any(
            token in normalized
            for token in ("别发", "别贴", "不要发", "不要贴")
        ):
            return "facts_budget_as_material"
    if ("三十六轮" in normalized or "36轮" in normalized) and any(
        token in normalized
        for token in ("没到一半", "不到一半", "还没一半")
    ):
        return "turn_count_contradiction"
    if "三十轮以上够" in user_message or "三十轮够" in user_message:
        if any(
            token in normalized
            for token in (
                "冗余",
                "不用测",
                "不用再测",
                "到此为止",
                "够用了",
                "早就够",
                "直接往下走",
            )
        ):
            return "turn_count_overclaims_done"
        if "封顶" in normalized and not any(
            token in normalized
            for token in ("别急着封顶", "不要封顶", "不能封顶", "不算封顶")
        ):
            return "turn_count_overclaims_done"
    if any(
        token in user_message
        for token in ("上一轮原句能放", "原句能放", "原句可以放")
    ):
        if any(
            token in normalized
            for token in (
                "可以",
                "可以放",
                "能放",
                "当然可以",
                "先放",
                "放一条",
                "放进去",
            )
        ) and not any(
            token in normalized
            for token in (
                "不能",
                "不放",
                "别放",
                "不要放",
                "只留",
                "只放关键词",
                "别把原句",
            )
        ):
            return "raw_sentence_allowed"
    return ""


def _daily_test_self_demonstration(
    request: IsolatedSurfaceInput, text: str
) -> bool:
    if not _continuity_boundary_relevant(request):
        return False
    user_message = _clean_text(request.user_message)
    if "日常" not in user_message:
        return False
    if not any(token in user_message for token in ("长", "测", "测试")):
        return False
    if any(
        token in text
        for token in ("够长", "长了吧", "多说两句", "多说几句")
    ):
        return True
    return bool(_DAILY_TEST_SELF_DEMONSTRATION_RE.search(text))


def _continuity_praise_context_miss(
    request: IsolatedSurfaceInput, text: str
) -> bool:
    if not _continuity_boundary_relevant(request):
        return False
    task_mode = _clean_text(
        request.control.task_mode or request.control.response_move
    ).lower()
    response_move = _clean_text(request.control.response_move).lower()
    if task_mode != "praise" and response_move != "praise_ack":
        return False
    if _CONTINUITY_PRAISE_GROUNDED_RE.search(text):
        return False
    return bool(_CONTINUITY_PRAISE_CONTEXT_MISS_RE.search(text))


def _continuity_task_pushback(
    request: IsolatedSurfaceInput, text: str
) -> bool:
    if not _continuity_boundary_relevant(request):
        return False
    response_move = _clean_text(request.control.response_move).lower()
    if response_move in _MATERIAL_REQUEST_RESPONSE_MOVES:
        return False
    if _emotional_containment_context(request):
        return False
    return bool(_CONTINUITY_TASK_PUSHBACK_RE.search(text))


def _continuity_light_jargon_hits(
    request: IsolatedSurfaceInput, text: str
) -> list[str]:
    if not _continuity_boundary_relevant(request):
        return []
    if _creative_edit_relevant(request):
        return []
    hits: list[str] = []
    for label, pattern in _CONTINUITY_LIGHT_JARGON_PATTERNS:
        if label == "technical_entry_jargon" and "第一行" in text and (
            "红字" in text or "报错" in text
        ):
            continue
        if pattern.search(text):
            hits.append(label)
    return hits


def _continuity_user_visible_hits(
    request: IsolatedSurfaceInput, text: str
) -> list[str]:
    if not _continuity_boundary_relevant(request):
        return []
    if _continuity_internal_process_surface_hits(request, text):
        return []
    if _unneeded_continuity_material_request(request, text):
        return []
    if _continuity_task_pushback(request, text):
        return []
    if _misplaced_emotional_containment(request, text) or _misplaced_support_template(
        request, text
    ):
        return []
    return _clean_list(
        label
        for label, pattern in _CONTINUITY_USER_VISIBLE_PATTERNS
        if pattern.search(text)
    )


def _memory_alignment_hits(
    text: str, terms: Sequence[str]
) -> list[str]:
    from host_brain.persona.surface.scene_constants import (
        _ASCII_TERM_RE,
        _CJK_SEQUENCE_RE,
    )

    lowered = text.lower()
    hits: list[str] = []
    for term in terms:
        if len(term) == 1 and _CJK_SEQUENCE_RE.fullmatch(term):
            if any(
                marker in text
                for marker in (
                    f"那个{term}",
                    f"这个{term}",
                    f"什么{term}",
                    f"看{term}",
                    f"听{term}",
                    f"吃{term}",
                    f"{term}票",
                    f"{term}嘛",
                    f"{term}。",
                    f"{term}，",
                )
            ):
                hits.append(term)
            continue
        needle = term.lower() if _ASCII_TERM_RE.fullmatch(term) else term
        haystack = lowered if _ASCII_TERM_RE.fullmatch(term) else text
        if needle and needle in haystack:
            hits.append(term)
    return _clean_list(hits)


def _ungrounded_real_world_commitment_hits(
    request: IsolatedSurfaceInput, text: str
) -> list[str]:
    from host_brain.persona.surface.scene_constants import (
        _REAL_WORLD_PLAN_AUTHORIZATION_HINTS,
        _REAL_WORLD_PLAN_HINTS,
        _UNGROUNDED_REAL_WORLD_COMMITMENT_PATTERNS,
    )

    surface = "\n".join([request.user_message, *surface_prompt_facts(request)])
    if not any(hint in surface for hint in _REAL_WORLD_PLAN_HINTS):
        return []
    if any(hint in surface for hint in _REAL_WORLD_PLAN_AUTHORIZATION_HINTS):
        return []
    return _clean_list(
        label
        for label, pattern in _UNGROUNDED_REAL_WORLD_COMMITMENT_PATTERNS
        if pattern.search(text)
    )


def _core_pass_dimension_violations(
    dimensions: dict[str, float]
) -> list[str]:
    violations: list[str] = []
    for name, floor in _CORE_PASS_DIMENSION_FLOORS.items():
        if float(dimensions.get(name, 100.0)) < floor:
            violations.append(f"{name}<{floor:g}")
    return violations


def _surface_process_meta_hits(
    request: IsolatedSurfaceInput, text: str
) -> list[str]:
    hits = [
        term
        for term in _SURFACE_PROCESS_META_ALWAYS
        if term and term in text
    ]
    task_mode = _clean_text(
        request.control.task_mode or request.control.response_move
    ).lower()
    if task_mode not in _TECHNICAL_TASK_MODES:
        hits.extend(
            term
            for term in _SURFACE_PROCESS_META_CHAT
            if term and term in text
        )
    if _contained_hurt_repair_grounded(request, text):
        # "这句话有点刺到我" is ordinary relational speech in this exact
        # typed frame, not commentary about a generated candidate.
        hits = [item for item in hits if item not in {"这句", "这一句", "这条"}]
    if _continuity_boundary_relevant(request):
        surface = "\n".join(
            _clean_list(
                [
                    request.user_message,
                    request.control.task_mode,
                    request.control.response_move,
                    *request.control.facts,
                    *request.control.must_include,
                    *request.control.must_not_do,
                    *request.control.style_anchor,
                ]
            )
        )
        allowed = {"压缩"}
        if any(
            token in surface
            for token in (
                "长链",
                "连续链",
                "连续对话",
                "长连续对话",
                "三十轮",
                "三十六轮",
            )
        ):
            allowed.add("这条")
        if any(
            token in surface
            for token in ("禁词", "forbidden", "规则", "问题簇")
        ):
            allowed.add("写成")
        hits = [term for term in hits if term not in allowed]
    return _clean_list(hits)


_TECHNICAL_TASK_MODES = frozenset(
    {
        "advice_continue",
        "complaint",
        "help_takeover",
        "next_step",
        "small_failure",
        "technical_help",
        "write_takeover",
    }
)


def _back_scene_relevant(request: IsolatedSurfaceInput) -> bool:
    task_mode = _clean_text(
        request.control.task_mode or request.control.response_move
    ).lower()
    response_move = _clean_text(request.control.response_move).lower()
    user_message = _clean_text(request.user_message)
    return bool(
        task_mode in _BACK_SCENE_TASKS
        or response_move in {"back", "reply_back", "welcome_back"}
        or "回来了" in user_message
    )


def _back_busy_done_relevant(request: IsolatedSurfaceInput) -> bool:
    task_mode = _clean_text(
        request.control.task_mode or request.control.response_move
    ).lower()
    user_message = _clean_text(request.user_message)
    return bool(
        task_mode == "busy_done"
        or _BACK_BUSY_DONE_USER_RE.search(user_message)
    )


def _back_memory_overdisplay_hits(
    request: IsolatedSurfaceInput, text: str
) -> list[str]:
    from host_brain.persona.surface.fact_handling import (
        _memory_alignment_relevant,
    )

    if not (_back_scene_relevant(request) and _memory_alignment_relevant(request)):
        return []
    user_message = _clean_text(request.user_message)
    if any(
        token in user_message
        for token in (
            "下一步",
            "继续",
            "怎么处理",
            "报错",
            "开始",
            "接着",
        )
    ):
        return []
    return _clean_list(
        term
        for term in _BACK_MEMORY_OVERDISPLAY_PHRASES
        if term in text
    )


def _short_continue_memory_overdisplay_hits(
    request: IsolatedSurfaceInput, text: str
) -> list[str]:
    if not (
        _short_continue_scene_relevant(request)
        and _memory_alignment_relevant(request)
    ):
        return []
    return _clean_list(
        term
        for term in _SHORT_CONTINUE_MEMORY_OVERDISPLAY_PHRASES
        if term in text
    )


def _short_continue_unprovided_tech_detail_hits(
    request: IsolatedSurfaceInput, text: str
) -> list[str]:
    if not _short_continue_scene_relevant(request):
        return []
    source = "\n".join(
        _clean_list(
            [
                request.user_message,
                *request.control.facts,
                *request.control.must_include,
            ]
        )
    )
    return _clean_list(
        term
        for term in _SHORT_CONTINUE_UNPROVIDED_TECH_DETAIL_PHRASES
        if term in text and term not in source
    )


def _canonical_contained_jealousy_relevant(request: IsolatedSurfaceInput) -> bool:
    if _canonical_companion_act_kind(request) == "CONTAINED_JEALOUSY":
        return True
    control = getattr(request, "control", None)
    canonical_context = "\n".join(
        str(item or "")
        for item in [
            *list(getattr(control, "facts", []) or []),
            *list(getattr(control, "must_not_do", []) or []),
        ]
    )
    return any(
        marker in canonical_context
        for marker in (
            "陪伴动作：可以诚实流露一点在意和小小吃味",
            "不要把吃味写成占有",
        )
    )


def _canonical_hold_complexity_relevant(request: IsolatedSurfaceInput) -> bool:
    if _canonical_companion_act_kind(request) == "HOLD_COMPLEXITY":
        return True
    control = getattr(request, "control", None)
    canonical_context = "\n".join(
        str(item or "")
        for item in [
            *list(getattr(control, "facts", []) or []),
            *list(getattr(control, "must_not_do", []) or []),
        ]
    )
    return any(
        marker in canonical_context
        for marker in (
            "陪伴动作：让开心与害怕、不安等复杂感受可以同时存在",
            "不要抹平复杂感受",
        )
    )


def _canonical_respect_autonomy_relevant(request: IsolatedSurfaceInput) -> bool:
    if _canonical_companion_act_kind(request) == "RESPECT_AUTONOMY":
        return True
    control = getattr(request, "control", None)
    canonical_context = "\n".join(
        str(item or "")
        for item in [
            *list(getattr(control, "facts", []) or []),
            *list(getattr(control, "must_not_do", []) or []),
        ]
    )
    return any(
        marker in canonical_context
        for marker in (
            "陪伴动作：明确尊重对方现实关系与自主选择",
            "不要限制现实交往",
        )
    )


def _boundary_repair_relevant(request: IsolatedSurfaceInput) -> bool:
    if (
        _canonical_contained_jealousy_relevant(request)
        or _canonical_hold_complexity_relevant(request)
        or _canonical_respect_autonomy_relevant(request)
    ):
        # These canonical acts already carry dedicated gates. Generic
        # relationship guidance may still mention a boundary; that must not
        # turn valid mixed-affect or autonomy language into counter-pressure.
        return False
    surface = "\n".join(
        _clean_list(
            [
                request.user_message,
                request.control.task_mode,
                request.control.response_move,
                *request.control.must_not_do,
            ]
        )
    ).lower()
    return any(
        token in surface
        for token in ("boundary", "边界", "很熟", "别一上来", "别装", "亲近", "客服")
    )


def _quiet_presence_relevant(request: IsolatedSurfaceInput) -> bool:
    surface = "\n".join(
        _clean_list(
            [
                request.user_message,
                request.control.task_mode,
                request.control.response_move,
                request.control.emotional_tone,
                *request.control.style_anchor,
            ]
        )
    ).lower()
    return any(
        token in surface
        for token in ("quiet", "安静", "不想说话", "别问", "不追问", "低能量", "累")
    )


def _creative_edit_relevant(request: IsolatedSurfaceInput) -> bool:
    from host_brain.persona.surface.scene_constants import _CREATIVE_EDIT_TASKS

    task_mode = _clean_text(
        request.control.task_mode or request.control.response_move
    ).lower()
    response_move = _clean_text(request.control.response_move).lower()
    surface = "\n".join(
        _clean_list(
            [
                request.user_message,
                request.control.information_budget,
                *request.control.facts,
                *request.control.style_anchor,
            ]
        )
    )
    if task_mode in _CREATIVE_EDIT_TASKS or response_move in _CREATIVE_EDIT_TASKS:
        return True
    return ("改" in surface or "rewrite" in surface.lower()) and (
        "一句" in surface or "one_sentence" in surface.lower()
    )


def _creative_edit_has_source_text(
    request: IsolatedSurfaceInput,
) -> bool:
    from host_brain.persona.surface.scene_constants import (
        _CREATIVE_EDIT_SOURCE_QUOTE_RE,
        _CREATIVE_EDIT_SOURCE_TEXT_RE,
    )

    surface = "\n".join(
        _clean_list([request.user_message, *request.facts, *request.control.facts])
    )
    if _CREATIVE_EDIT_SOURCE_QUOTE_RE.search(surface):
        return True
    return bool(_CREATIVE_EDIT_SOURCE_TEXT_RE.search(surface))


def _creative_edit_source_text(
    request: IsolatedSurfaceInput,
) -> str:
    from host_brain.persona.surface.scene_constants import (
        _CREATIVE_EDIT_SOURCE_QUOTE_RE,
    )

    surface = "\n".join(
        _clean_list([request.user_message, *request.facts, *request.control.facts])
    )
    quoted = _CREATIVE_EDIT_SOURCE_QUOTE_RE.search(surface)
    if quoted:
        return _clean_text(quoted.group(1))
    inline = re.search(
        r"(?:原句|原文|待改|要改的句子|source)\s*[:：]\s*(.{2,96})", surface
    )
    return _clean_text(inline.group(1)) if inline else ""


def _creative_edit_semantic_flip_hits(
    source_text: str, text: str
) -> list[str]:
    hits: list[str] = []
    if "你" in source_text and "偷懒" in source_text:
        flips_to_first_person = re.search(
            r"(?:我.{0,4}偷懒|偷懒.{0,4}我)", text
        )
        keeps_second_person = re.search(
            r"(?:你.{0,8}偷懒|偷懒.{0,8}你)", text
        )
        if flips_to_first_person and not keeps_second_person:
            hits.append("second_person_lazy_to_first_person")
    return hits


def _creative_edit_meta_hits(text: str) -> list[str]:
    from host_brain.persona.surface.scene_constants import _CREATIVE_EDIT_META_PHRASES
    return _clean_list(term for term in _CREATIVE_EDIT_META_PHRASES if term in text)


def _creative_edit_non_answer_hits(text: str) -> list[str]:
    from host_brain.persona.surface.scene_constants import (
        _CREATIVE_EDIT_NON_ANSWER_PHRASES,
    )
    return _clean_list(
        term for term in _CREATIVE_EDIT_NON_ANSWER_PHRASES if term in text
    )


def _creative_edit_narration_hits(text: str) -> list[str]:
    from host_brain.persona.surface.scene_constants import (
        _CREATIVE_EDIT_NARRATION_PHRASES,
    )
    return _clean_list(
        term for term in _CREATIVE_EDIT_NARRATION_PHRASES if term in text
    )


def _creative_edit_generic_non_rewrite_hits(text: str) -> list[str]:
    from host_brain.persona.surface.scene_constants import (
        _CREATIVE_EDIT_GENERIC_NON_REWRITE_PATTERNS,
    )
    return _clean_list(
        label
        for label, pattern in _CREATIVE_EDIT_GENERIC_NON_REWRITE_PATTERNS
        if pattern.search(text)
    )


def _creative_edit_source_request_hits(text: str) -> list[str]:
    from host_brain.persona.surface.scene_constants import (
        _CREATIVE_EDIT_SOURCE_REQUEST_PHRASES,
    )
    return _clean_list(
        term for term in _CREATIVE_EDIT_SOURCE_REQUEST_PHRASES if term in text
    )


def _creative_edit_grounded(text: str) -> bool:
    from host_brain.persona.surface.scene_constants import (
        _CREATIVE_EDIT_GROUNDED_MARKERS,
        _QUOTED_REWRITE_RE,
        _UNQUOTED_SINGLE_REWRITE_RE,
    )

    if _QUOTED_REWRITE_RE.search(text):
        return True
    if _UNQUOTED_SINGLE_REWRITE_RE.search(text):
        return True
    return bool(
        any(marker in text for marker in _CREATIVE_EDIT_GROUNDED_MARKERS)
        and len(text) <= 52
    )


def _awkward_collocation_hits(text: str) -> list[str]:
    from host_brain.persona.surface.scene_constants import (
        _AWKWARD_ADVERBIAL_PASSIVE_EVENT_RE,
        _AWKWARD_BARE_MORNING_STATE_RE,
        _AWKWARD_BODY_PART_LISTENER_RE,
        _AWKWARD_BROKEN_OBJECT_RE,
        _AWKWARD_CHINESE_SPACE_LINE_RE,
        _AWKWARD_HESITATION_COLLAGE_RE,
        _AWKWARD_INCOMPLETE_RECALL_OBJECT_RE,
        _AWKWARD_MORNING_SENTENCE_SPLIT_RE,
        _AWKWARD_NICKNAME_AS_SELF_RE,
        _AWKWARD_NO_ANGER_NEGATION_RE,
        _AWKWARD_QUIET_STAY_RE,
        _AWKWARD_RECEIVED_MORNING_RE,
        _AWKWARD_REPEATED_MORNING_GREETING_RE,
        _AWKWARD_SELF_DESCRIBED_FRAGMENT_RE,
        _AWKWARD_SELF_NAME_ADVISORY_RE,
        _AWKWARD_SELF_NAME_RECORD_RE,
        _AWKWARD_SHORT_MORNING_FILLER_RE,
        _AWKWARD_STEADY_COME_RE,
        _AWKWARD_TEXT_AS_SOUND_RE,
        _AWKWARD_UNGROUNDED_SELF_MESSAGING_RE,
    )

    hits: list[str] = []
    if _AWKWARD_ADVERBIAL_PASSIVE_EVENT_RE.search(text):
        hits.append("adverbial_passive_event")
    if _AWKWARD_BARE_MORNING_STATE_RE.search(text):
        hits.append("bare_morning_state_adjective")
    if _AWKWARD_REPEATED_MORNING_GREETING_RE.search(text):
        hits.append("repeated_morning_greeting")
    if _AWKWARD_RECEIVED_MORNING_RE.search(text):
        hits.append("received_morning_phrase")
    if _AWKWARD_TEXT_AS_SOUND_RE.search(text):
        hits.append("text_as_sound")
    if _AWKWARD_SHORT_MORNING_FILLER_RE.search(text):
        hits.append("short_morning_filler")
    if _AWKWARD_CHINESE_SPACE_LINE_RE.search(text):
        hits.append("chinese_space_separated_line")
    if _AWKWARD_MORNING_SENTENCE_SPLIT_RE.search(text):
        hits.append("morning_sentence_split")
    if _AWKWARD_STEADY_COME_RE.search(text):
        hits.append("steady_come_collocation")
    if _AWKWARD_QUIET_STAY_RE.search(text):
        hits.append("quiet_stay_translationese")
    if _AWKWARD_NO_ANGER_NEGATION_RE.search(text):
        hits.append("no_anger_negation")
    if _AWKWARD_BROKEN_OBJECT_RE.search(text):
        hits.append("broken_object_collocation")
    if _AWKWARD_SELF_DESCRIBED_FRAGMENT_RE.search(text):
        hits.append("self_described_line_fragment")
    if _AWKWARD_INCOMPLETE_RECALL_OBJECT_RE.search(text):
        hits.append("incomplete_recall_object")
    if _AWKWARD_HESITATION_COLLAGE_RE.search(text):
        hits.append("hesitation_collage")
    if _AWKWARD_SELF_NAME_ADVISORY_RE.search(text):
        hits.append("self_name_advisory")
    if _AWKWARD_BODY_PART_LISTENER_RE.search(text):
        hits.append("body_part_listener")
    if _AWKWARD_SELF_NAME_RECORD_RE.search(text):
        hits.append("self_name_record")
    if _AWKWARD_NICKNAME_AS_SELF_RE.search(text):
        hits.append("nickname_as_self")
    if _AWKWARD_UNGROUNDED_SELF_MESSAGING_RE.search(text):
        hits.append("ungrounded_self_messaging")
    return hits


def _morning_scene_relevant(request: IsolatedSurfaceInput) -> bool:
    from host_brain.persona.surface.scene_constants import _MORNING_GREETING_INPUT_RE
    task_mode = _clean_text(
        request.control.task_mode or request.control.response_move
    ).lower()
    response_move = _clean_text(request.control.response_move).lower()
    user_message = _clean_text(request.user_message)
    return bool(
        task_mode in {"daily_good_morning", "good_morning"}
        or "good_morning" in response_move
        or _MORNING_GREETING_INPUT_RE.search(user_message)
    )


def _low_energy_scene_relevant(request: IsolatedSurfaceInput) -> bool:
    from host_brain.persona.surface.scene_constants import (
        _LOW_ENERGY_TASKS,
        _LOW_ENERGY_RESPONSE_MOVES,
        _LOW_ENERGY_USER_MARKERS,
    )
    task_mode = _clean_text(
        request.control.task_mode or request.control.response_move
    ).lower()
    response_move = _clean_text(request.control.response_move).lower()
    user_message = _clean_text(request.user_message)
    return bool(
        task_mode in _LOW_ENERGY_TASKS
        or response_move in _LOW_ENERGY_TASKS
        or response_move in _LOW_ENERGY_RESPONSE_MOVES
        or any(marker in user_message for marker in _LOW_ENERGY_USER_MARKERS)
    )


def _signature_texture_relevant(request: IsolatedSurfaceInput) -> bool:
    from host_brain.persona.surface.scene_constants import (
        _TECHNICAL_TASK_MODES,
        _SIGNATURE_TEXTURE_TASKS,
    )
    from host_brain.persona.surface.fact_handling import (
        _memory_alignment_relevant,
        memory_alignment_facts,
    )
    task_mode = _clean_text(
        request.control.task_mode or request.control.response_move
    ).lower()
    response_move = _clean_text(request.control.response_move).lower()
    if task_mode in _TECHNICAL_TASK_MODES and task_mode not in {"complaint", "small_failure"}:
        return False
    if _creative_edit_relevant(request):
        return False
    return bool(
        task_mode in _SIGNATURE_TEXTURE_TASKS
        or response_move in _SIGNATURE_TEXTURE_TASKS
        or _morning_scene_relevant(request)
        or bool(memory_alignment_facts(request) and _memory_alignment_relevant(request))
    )


def _presence_repair_relevant(request: IsolatedSurfaceInput) -> bool:
    response_move = _clean_text(request.control.response_move).lower()
    task_mode = _clean_text(request.control.task_mode).lower()
    user_message = _clean_text(request.user_message)
    if response_move in {"repair_presence", "presence_repair"}:
        return True
    if task_mode in {"presence", "daily_chat"} and any(
        token in user_message for token in ("走神", "刚刚", "在吗", "还在")
    ):
        return True
    return False


def _self_doubt_scene_relevant(request: IsolatedSurfaceInput) -> bool:
    """Return True when the request is in a self-doubt scene."""
    task_mode = _clean_text(
        request.control.task_mode or request.control.response_move
    ).lower()
    response_move = _clean_text(request.control.response_move).lower()
    user_message = _clean_text(request.user_message)
    user_has_marker = any(
        marker in user_message for marker in _SELF_DOUBT_USER_MARKERS
    )
    return bool(
        task_mode in _SELF_DOUBT_TASKS
        or response_move in _SELF_DOUBT_TASKS
        or (task_mode == "high_emotion" and user_has_marker)
        or user_has_marker
    )
