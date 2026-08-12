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
    # 所有候选守卫先收敛到同一份规范化动作帧；不直接猜测自然语言事实，
    # 才不会因上游采用旧字段或大小写不同而放宽人格动作的边界。
    frame = normalize_companion_utterance_frame(
        getattr(control, "companion_utterance_frame", None)
    )
    return str(frame.get("companion_act_kind") or "").strip().upper()


_CONTAINED_HURT_REPAIR_GROUNDING_RE = re.compile(
    r"(?:这|那|刚才)?(?:一)?(?:句|句话|话).{0,8}(?:刺到我|扎到我|让我有点难受)"
    r"|我(?:听着|心里)?(?:也|确实|还是|会|有点){0,3}(?:难受|不好受|受伤)"
    r"|听到(?:这|那)?(?:句|句话|话).{0,8}(?:有点难受|心里有点疼)"
)

_REPAIR_FORWARD_IMPACT_RE = re.compile(
    r"说了也(?:像)?白说|话(?:像)?落了空|没被听见|"
    r"(?:这|那)件事还没过去|(?:你|你的).{0,8}(?:不舒服|堵得慌)"
)
_REPAIR_FORWARD_OWNERSHIP_RE = re.compile(
    r"是我(?:造成的|的问题|没做好)|这次算我的|(?:接下来|后面)?我会"
)
_REPAIR_FORWARD_ACTION_RE = re.compile(
    r"按(?:你|原来|原本).{0,8}(?:意思|说法).{0,6}(?:听|回应)"
    r"|先听.{0,8}(?:再回应|不解释)|不抢着解释|"
    r"不拿解释.{0,6}(?:盖|压)|先复述.{0,8}再回应|"
    r"先照.{0,8}回应|(?:接下来|后面|下次)?我会.{0,8}按你说的(?:来|做|回应)"
)
_REPAIR_REPEATED_IMPACT_USER_RE = re.compile(
    r"不是第一次|又是这样|又这样|总是这样|每次都"
)
_REPAIR_REPEATED_IMPACT_REPLY_RE = re.compile(
    r"又让你.{0,8}(?:不舒服|失望|受伤)|让你反复.{0,8}(?:不舒服|失望|受伤)|"
    r"同样的事.{0,8}(?:又|再)?发生|"
    r"(?:又|再)发生同样的事"
)
_REPAIR_CHALLENGE_USER_RE = re.compile(
    r"^\s*(?:别|不要)(?:再)?装(?:作)?懂[。.!！]?\s*$"
)
_REPAIR_UNRESOLVED_USER_RE = re.compile(
    r"(?:已经|都)?道歉.{0,14}(?:还没|还没有).{0,10}(?:过去|翻篇)"
    r"|道歉.{0,10}(?:不等于|不代表).{0,8}(?:过去|翻篇)"
)
_REPAIR_UNRESOLVED_REPLY_RE = re.compile(
    r"道歉.{0,8}(?:不等于|不代表|归道歉).{0,10}(?:过去|翻篇)"
    r"|(?:这件事|事情).{0,8}(?:还没|还没有).{0,8}(?:过去|翻篇)"
    r"|不(?:会|急着|替你).{0,10}(?:翻篇|当作.{0,6}(?:结束|过去))"
)
_REPAIR_LIGHTNESS_USER_RE = re.compile(
    r"(?:你|这话|这句话)?(?:说|讲)(?:得|起来)?(?:也)?(?:太)?轻巧"
)
_REPAIR_LIGHTNESS_REPLY_RE = re.compile(
    r"(?:这|那)?(?:话|句话|说法).{0,6}(?:听着|说起来).{0,4}轻巧"
    r"|(?:听着|说起来).{0,4}轻巧"
)
_REPAIR_LIGHTNESS_OPEN_RE = re.compile(
    r"(?:不|没)(?:会|想|打算).{0,10}(?:糊弄过去|当作过去|替你翻篇|就这么算)"
    r"|(?:这件事|前面的后果).{0,8}(?:还没过去|还在)"
    r"|不拿.{0,8}(?:当交代|当结束|盖过去)"
)
_REPAIR_ACKNOWLEDGEMENT_USER_RE = re.compile(
    r"^\s*(?:嗯[，,。 ]*)?(?:我)?(?:听到了|听见了|知道了)[。.!！]?\s*$"
)
_REPAIR_ACKNOWLEDGEMENT_HOLD_RE = re.compile(
    r"先(?:收住|停在这里|停一下)|不(?:再)?往下说|不拿.{0,8}当(?:作)?结束"
)
_REPAIR_ACKNOWLEDGEMENT_OPEN_RE = re.compile(
    r"前面的事还在|事情还在|不拿.{0,8}当(?:作)?结束|还没(?:有)?过去"
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

_AMBIGUOUS_AFFECT_UNCERTAINTY_RE = re.compile(
    r"说不清|还(?:是|没)?模糊|模糊着|跟不上|不(?:替你)?下结论|"
    r"不替你解释|不(?:用|必|急着).{0,6}(?:起名字|命名|下定义|说清楚)"
)
_AMBIGUOUS_AFFECT_SHORT_ACK_RE = re.compile(r"^\s*(?:嗯+|好|是|知道了)[。.!！]?\s*$")
_AMBIGUOUS_AFFECT_HOLD_RE = re.compile(
    r"先这么待着|先悬着|先(?:让.{0,8})?搁着|先让.{0,8}(?:留在|待在)这|"
    r"不往下解释|不替你往下解释"
)


def _ambiguous_affect_grounded(
    request: IsolatedSurfaceInput,
    text: str,
) -> bool:
    """Recognise a complete, uncertainty-preserving ambiguous-affect reply."""

    # “未命名情绪”只能被陪伴而不能被擅自定性，因此必须同时核对类型化意图
    # 与文本里的保留语气；两者缺一都不能把候选当作已落地的安慰。
    control = getattr(request, "control", None)
    frame = normalize_companion_utterance_frame(
        getattr(control, "companion_utterance_frame", None)
    )
    if not (
        str(frame.get("semantic_focus") or "").upper() == "UNNAMED_USER_AFFECT"
        and str(frame.get("response_move") or "").upper()
        == "COMFORT_WITHOUT_NAMING"
    ):
        return False
    value = str(text or "")
    return bool(
        _AMBIGUOUS_AFFECT_UNCERTAINTY_RE.search(value)
        or (
            _AMBIGUOUS_AFFECT_SHORT_ACK_RE.fullmatch(
                str(getattr(request, "user_message", "") or "")
            )
            and (
                _AMBIGUOUS_AFFECT_HOLD_RE.search(value)
                or _AMBIGUOUS_AFFECT_UNCERTAINTY_RE.search(value)
            )
        )
    )


_GIVE_SPACE_COMMITMENT_RE = re.compile(
    r"不(?:再)?(?:追问|主动(?:找|联系|发消息)|打扰|问(?:你)?怎么了)"
    r"|(?:把)?主动联系(?:先)?停(?:下来|到(?:今天|今晚|明天))"
)


def _give_space_grounded(
    request: IsolatedSurfaceInput,
    text: str,
) -> bool:
    """Recognise a concise, time-matched canonical GIVE_SPACE commitment."""

    if _canonical_companion_act_kind(request) != "GIVE_SPACE":
        return False
    value = str(text or "")
    if not _GIVE_SPACE_COMMITMENT_RE.search(value):
        return False
    # 等待承诺的时间锚点必须复用用户已给出的范围，避免模型把“今天”扩写成
    # 没有依据的“明天”或长期约定。
    user_value = str(getattr(request, "user_message", "") or "")
    if any(marker in user_value for marker in ("今晚", "今天晚上")):
        return any(marker in value for marker in ("今晚", "今天晚上"))
    if "明天" in user_value:
        return "明天" in value
    if "今天" in user_value:
        return "今天" in value
    return True


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
    # 这是“候选正在索要原始上下文”的检测，不把用户明确要求“不回灌/别粘贴”
    # 的否定句误记为索要；窗口检查保留了否定词与命中短语的局部语义关系。
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
    # 索要材料会把连续对话的恢复责任转回用户，所以只有创作改写、明确接管
    # 或用户显式求助时才允许；控制层的禁止动作优先于这些宽松分支。
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
    # 连续性术语属于实现过程，普通陪伴回复说出它会暴露内部压缩/评分机制；
    # 创作编辑场景则可能合法地讨论这些字面材料，故单独豁免。
    return bool(_CONTINUITY_PROCESS_NOTATION_RE.search(text))


def _continuity_internal_process_surface_hits(
    request: IsolatedSurfaceInput, text: str
) -> list[str]:
    if not _continuity_boundary_relevant(request):
        return []
    if _creative_edit_relevant(request):
        return []
    # 返回命中的原词而非布尔值，供审计层指出具体泄露点，同时仍由本层决定
    # 哪些任务具有连续性边界，避免把技术任务的正常术语误判为人格泄露。
    return _clean_list(
        match.group(0)
        for match in _CONTINUITY_INTERNAL_PROCESS_SURFACE_RE.finditer(text)
    )


def _unprovided_media_object_hits(
    request: IsolatedSurfaceInput, text: str
) -> list[str]:
    # 只以用户输入和已注入的表面事实作为可引用证据；候选凭空提到照片、
    # 文件等对象会制造“我看到了”的错觉，因此收集为可审计命中项。
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
    # 个人身份、现实权限等断言没有请求上下文可供授权，命中即视为不可凭空
    # 承诺的外部权威，而不是交给后续文风评分稀释。
    return _clean_list(
        match.group(0)
        for match in _UNPROVIDED_PERSONAL_AUTHORITY_RE.finditer(text)
    )


def _unprovided_continuity_turn_marker_hits(
    request: IsolatedSurfaceInput, text: str
) -> list[str]:
    if not _continuity_boundary_relevant(request):
        return []
    # 轮次编号会伪造系统记忆；只有用户自己谈到轮数时才允许候选沿用这类标记。
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
    if _REPAIR_CHALLENGE_USER_RE.fullmatch(str(request.user_message or "")):
        # "别装懂" directly rejects a false understanding claim.  Even if a
        # stale relationship projection temporarily leaves the act on the
        # single-stream path, the reply must not make the user restate the
        # problem as the price of repair.
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


def _relational_repair_forward_action_grounded(
    request: IsolatedSurfaceInput,
    text: str,
) -> bool:
    """Recognise repair that advances beyond another apology.

    A later repair turn should be allowed to own the user's stated impact and
    name one concrete change in Natsume's next response.  Requiring another
    ``没接住／理解错`` phrase made the quality scorer contradict the hard
    gate that rejects mechanical repair repetition.
    """

    if not _relational_repair_relevant(request):
        return False
    value = _clean_text(text)
    return bool(
        _REPAIR_FORWARD_IMPACT_RE.search(value)
        and _REPAIR_FORWARD_OWNERSHIP_RE.search(value)
        and _REPAIR_FORWARD_ACTION_RE.search(value)
    )


def _relational_repair_repeated_impact_grounded(
    request: IsolatedSurfaceInput,
    text: str,
) -> bool:
    """Allow repeated-impact language only when the user grounded repetition."""

    value = _clean_text(text)
    return bool(
        _relational_repair_relevant(request)
        and _REPAIR_REPEATED_IMPACT_USER_RE.search(str(request.user_message or ""))
        and _REPAIR_REPEATED_IMPACT_REPLY_RE.search(value)
        and _REPAIR_FORWARD_OWNERSHIP_RE.search(value)
        and _REPAIR_FORWARD_ACTION_RE.search(value)
    )


def _relational_repair_unresolved_grounded(
    request: IsolatedSurfaceInput,
    text: str,
) -> bool:
    """Recognise the middle repair beat where an apology did not resolve harm."""

    return bool(
        _relational_repair_relevant(request)
        and _REPAIR_UNRESOLVED_USER_RE.search(str(request.user_message or ""))
        and _REPAIR_UNRESOLVED_REPLY_RE.search(str(text or ""))
    )


def _relational_repair_lightness_grounded(
    request: IsolatedSurfaceInput,
    text: str,
) -> bool:
    """Recognise a repair follow-up that challenges words as too easy.

    The complete move is to acknowledge that the words sound light while
    keeping the rupture open or naming one bounded next-response change.  It
    should not need to repeat another apology merely to earn scene grounding.
    """

    value = _clean_text(text)
    return bool(
        _relational_repair_relevant(request)
        and _REPAIR_LIGHTNESS_USER_RE.search(str(request.user_message or ""))
        and _REPAIR_LIGHTNESS_REPLY_RE.search(value)
        and (
            _REPAIR_LIGHTNESS_OPEN_RE.search(value)
            or _REPAIR_FORWARD_ACTION_RE.search(value)
        )
    )


def _relational_repair_acknowledgement_grounded(
    request: IsolatedSurfaceInput,
    text: str,
) -> bool:
    """Recognise a bounded pause after the user acknowledges a repair turn."""

    value = _clean_text(text)
    return bool(
        _relational_repair_relevant(request)
        and _REPAIR_ACKNOWLEDGEMENT_USER_RE.fullmatch(
            str(request.user_message or "")
        )
        and _REPAIR_ACKNOWLEDGEMENT_HOLD_RE.search(value)
        and _REPAIR_ACKNOWLEDGEMENT_OPEN_RE.search(value)
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
        # 单个汉字既可能是记忆锚点也极易在普通句子中偶然出现，必须带指代或
        # 可读上下文才计入对齐；英文术语则按大小写无关的字面契约匹配。
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
    # 现实计划信号出现时才启动此守卫，并让明确授权优先放行；这样既防止
    # 人格擅自安排线下行动，也不阻断用户已许可的具体协作承诺。
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
