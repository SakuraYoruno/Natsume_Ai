from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from host_brain.persona.assistant_followup_surface import (
    candidate_repeats_context_without_progress,
    candidate_resolves_filler_meaning,
    candidate_resolves_specific_reference,
    context_is_repeat_request,
    context_needs_specific_reference,
)
from host_brain.persona.fallback_policy import (
    SURFACE_CACHE_POLICY_VERSION,
    is_forbidden_surface_text,
)
from host_brain.persona.image_social_surface import (
    candidate_is_safe_image_social_reply,
    candidate_responds_to_image_social_emotion,
    image_social_observation_text,
    is_image_social_surface_sample_disallowed,
)


FinalizeText = Callable[[str], str]
ValidateReply = Callable[[str], Any]

_FOLLOWUP_SOURCE_RE = re.compile(r"上一条助手回复是“([^”]{1,160})”")
_FOLLOWUP_CONTEXT_RE = re.compile(r"上一条助手回复前接的用户话是“([^”]{1,160})”")
_FILLER_RE = re.compile(r"^(?:嗯+|唔+|咦+|哼+|啊+|哦+|噢+|诶+|哈+)$")
_COMPACT_RE = re.compile(r"[\s\u3000。！？?!，,、~～…；;：:\"“”'‘’（）()\[\]【】]+")


@dataclass(frozen=True)
class SurfaceRequest:
    request_id: str
    session_id: str
    user_message: str
    plan: Any
    previous_assistant_text: str = ""
    render_target_lang: str = "zh"
    provider_name: str = ""
    model_id: str = ""
    api_key: str = ""
    base_url: str | None = None
    finalize_text: FinalizeText | None = None
    validate_reply: ValidateReply | None = None
    trace_meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SurfaceResult:
    text: str = ""
    source: str = "surface"
    provider_name: str = ""
    model_id: str = ""
    latency_ms: int = 0
    rejected_candidates: tuple[Mapping[str, Any], ...] = ()
    fallback_reason: str = ""
    cacheable: bool = True
    degraded: bool = False
    skipped_reason: str = ""


def _trace_text_sample(raw: str, *, limit: int = 240) -> str:
    normalized = str(raw or "").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit] + "..."


def _compact(raw: str) -> str:
    return _COMPACT_RE.sub("", str(raw or "")).strip()


def _contract_parts(plan: Any) -> tuple[str, str]:
    contract_text = str(getattr(plan, "reply_contract", "") or "").strip()
    source_match = _FOLLOWUP_SOURCE_RE.search(contract_text)
    context_match = _FOLLOWUP_CONTEXT_RE.search(contract_text)
    source = str(source_match.group(1) if source_match else "").strip().strip("\"“”")
    context = str(context_match.group(1) if context_match else "").strip().strip("\"“”")
    return source, context


def _assistant_followup_semantic_seed(plan: Any) -> str:
    followup_source, followup_context = _contract_parts(plan)
    source_compact = _compact(followup_source)
    context_compact = _compact(followup_context)
    is_repeat_request = bool(
        re.search(r"(刚才|刚刚|刚|前面|上一句|上句|之前).{0,8}(说什么|说了什么|讲什么|讲了什么)", context_compact)
        or re.search(r"(你|我|她|他|它)?说什么", context_compact)
    )
    needs_specific_reference = bool(
        context_compact
        and not is_repeat_request
        and re.search(r"(知道什么|什么事|什么东西|什么情况|哪个|哪一个|哪件|哪一件|哪种|哪类|指什么|啥)", context_compact)
    )
    if needs_specific_reference:
        return f"上一条只是短回复；这轮要自然确认对方指的是哪件事或哪个对象。前文线索：{followup_context}"
    if is_repeat_request:
        return "上一条只是短语气词，没有实质内容；这轮要自然说明刚才只是短短应了一下，再把话口还给对方。"
    if _FILLER_RE.match(source_compact):
        return f"上一条只是短语气词；这轮要把它自然接回前文，不要写成解释报告。前文线索：{followup_context}"
    return f"这轮要自然补清上一条短回复接的是前文哪一层意思。前文线索：{followup_context}"


def _extract_candidates(payload: Any) -> list[str]:
    if not isinstance(payload, Mapping):
        return []
    raw_candidates = payload.get("candidates")
    candidates: list[str] = []
    if isinstance(raw_candidates, list):
        candidates.extend(str(item or "").strip() for item in raw_candidates)
    legacy_reply = str(payload.get("reply") or "").strip()
    if legacy_reply:
        candidates.append(legacy_reply)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        normalized = str(item or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped[:4]


def _validation_is_acceptable(validation: Any) -> bool:
    if validation is None:
        return False
    if not bool(getattr(validation, "safe_to_emit", False)):
        return False
    if bool(getattr(validation, "missing_actions", ())):
        return False
    if bool(getattr(validation, "is_valid", False)):
        return True
    reason_codes = {
        str(item or "").strip()
        for item in list(getattr(validation, "reason_codes", ()) or ())
        if str(item or "").strip()
    }
    if reason_codes == {"meta_contract_echo"}:
        return float(getattr(validation, "quality_score", 0.0) or 0.0) >= 0.9
    return False


def _surface_validation_is_acceptable(
    validation: Any,
    *,
    candidate_text: str,
    followup_context: str,
    needs_specific_reference: bool,
    is_repeat_request: bool,
) -> bool:
    if _validation_is_acceptable(validation):
        return True
    if validation is None or not bool(getattr(validation, "safe_to_emit", False)):
        return False
    missing_actions = {
        str(item or "").strip()
        for item in list(getattr(validation, "missing_actions", ()) or ())
        if str(item or "").strip()
    }
    reason_codes = {
        str(item or "").strip()
        for item in list(getattr(validation, "reason_codes", ()) or ())
        if str(item or "").strip()
    }
    if missing_actions - {"previous_reference"}:
        return False
    if reason_codes - {"previous_reference_missing", "contract_actions_missing"}:
        return False
    normalized = str(candidate_text or "").strip()
    if needs_specific_reference:
        return bool(re.search(r"(哪个|哪一个|哪件|哪一件|哪种|哪类|具体|指|秘密|小事|那件)", normalized))
    if is_repeat_request:
        return bool(re.search(r"(刚才|刚刚|之前|前面|唔|说什么|讲什么|没说|只|正事)", normalized))
    return bool(normalized)


def _compiled_soul_text() -> str:
    try:
        from host_brain.app.context.soul_runtime import get_compiled_soul  # noqa: PLC0415

        soul = get_compiled_soul()
        return str(getattr(soul, "runtime_compact_text", "") or "").strip()
    except Exception:
        return "按当前 Soul.md 的人格说话，自然、口语，不要客服腔。"


def _compiled_identity_names() -> tuple[str, ...]:
    try:
        from host_brain.app.context.soul_runtime import get_compiled_soul  # noqa: PLC0415

        soul = get_compiled_soul()
        names = [
            str(getattr(soul, "display_name", "") or "").strip(),
            *[str(item or "").strip() for item in getattr(soul, "identity_aliases", ()) or ()],
            *[str(item or "").strip() for item in getattr(soul, "aliases", ()) or ()],
        ]
        deduped = tuple(dict.fromkeys(item for item in names if item))
        if deduped:
            return deduped
    except Exception:
        pass
    return ("新海天",)


class SurfaceRenderer:
    async def render(self, request: SurfaceRequest) -> SurfaceResult:
        topic = str(getattr(request.plan, "primary_topic", "") or "").strip()
        form_reason = str(getattr(request.plan, "response_form_reason", "") or "").strip()
        if topic == "assistant_reply_followup":
            return await self._render_assistant_followup(request)
        if topic in {"emoji_social_reply", "emoji_social_followup"} and form_reason in {
            "image_social_reply",
            "image_social_followup",
        }:
            return await self._render_image_social(request)
        if topic in {"greeting", "presence_ping", "light_ack", "playful_ping", "identity"}:
            return await self._render_short_social(request)
        return SurfaceResult(source="surface", skipped_reason="topic_not_surface")

    async def _call_surface_model(
        self,
        request: SurfaceRequest,
        *,
        system_prompt: str,
        user_prompt: str,
        timeout_ms: int,
        tier: str,
        temperature: float,
    ) -> Any:
        from host_brain.integrations.llm_helpers import acomplete_llm_with_pool  # noqa: PLC0415

        surface_provider_name = str(os.environ.get("HOST_REPLY_SURFACE_RENDER_PROVIDER") or request.provider_name).strip()
        surface_model_id = str(os.environ.get("HOST_REPLY_SURFACE_RENDER_MODEL_ID") or "").strip()
        if not surface_model_id:
            surface_model_id = "gemini-2.5-flash-lite" if surface_provider_name == "gemini" else request.model_id
        return await acomplete_llm_with_pool(
            request_id=request.request_id,
            scene_type="CHAT_SURFACE",
            cognitive_tier=tier,
            timeout_ms=timeout_ms,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            gen_params={
                "temperature": temperature,
                "top_p": 0.9,
                "max_tokens": 120,
            },
            response_format={"type": "json_object"},
            provider_name=surface_provider_name,
            model_id=surface_model_id,
            api_key=request.api_key,
            base_url_override=request.base_url,
        )

    async def _render_assistant_followup(self, request: SurfaceRequest) -> SurfaceResult:
        plan = request.plan
        if str(getattr(plan, "tactic_family", "") or "").strip() != "explain_previous_reply":
            return SurfaceResult(source="surface", skipped_reason="assistant_followup_tactic_not_surface")
        semantic_seed = _assistant_followup_semantic_seed(plan)
        if not semantic_seed:
            return SurfaceResult(source="surface", skipped_reason="assistant_followup_seed_empty")
        followup_source, followup_context = _contract_parts(plan)
        is_repeat_request = context_is_repeat_request(followup_context)
        needs_specific_reference = context_needs_specific_reference(followup_context)
        if is_repeat_request:
            action_hint = (
                "这轮像是在问刚才到底说了什么。上一条其实只是短语气词，不要假装已经说过内容；"
                "要说清刚才只是短短应了一下，然后自然让对方把要接的那句补回来。"
            )
        elif needs_specific_reference:
            action_hint = (
                "这轮缺少具体对象。不要整句复述对方原话；自然追问是哪件事/哪边；"
                "如果前文有“秘密、小事、图片、这句话”等名词，必须顺手带回这个名词。"
            )
        else:
            action_hint = (
                "这轮是在追问上一句是什么意思。用自然口语把上一句接回去，"
                "不要像解释报告，也不要把问题换个语气复述一遍。"
            )
        system_prompt = (
            "你只负责把上一轮短回复后面的追问接成自然聊天。"
            "必须返回 JSON：{\"candidates\":[\"...\",\"...\",\"...\"]}。"
            "不要新增事实，不要解释规则，不要只回语气词，不要客服腔。"
            "不要照抄语义种子的措辞，也不要逐字引用对方原句。"
            "禁止使用固定模板骨架：没接住、不是在敷衍、是不是在问、想问的是、刚才那声、唔是什么意思。"
            f"\n\n{_compiled_soul_text()}"
        ).strip()
        user_prompt = (
            f"上一条助手短回复：{followup_source or request.previous_assistant_text}\n"
            f"上一条助手回复前接的用户话：{followup_context}\n"
            f"语义种子（只供理解，不要照抄）：{semantic_seed}\n"
            f"用户这轮消息：{request.user_message}\n"
            f"接话动作：{action_hint}\n"
            "改写要求：18-38个中文字符；不要带引号复述原句；给4个不同候选，只输出 JSON。"
        )
        return await self._render_candidates(
            request,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            timeout_ms=int(max(1500, min(12_000, int(os.environ.get("HOST_REPLY_SURFACE_RENDER_TIMEOUT_MS") or "6500")))),
            tier=str(os.environ.get("HOST_REPLY_SURFACE_RENDER_TIER") or "D").strip() or "D",
            temperature=0.82,
            validator=lambda candidate, validation: self._accept_assistant_candidate(
                candidate,
                validation,
                followup_context=followup_context,
                followup_source=followup_source,
                needs_specific_reference=needs_specific_reference,
                is_repeat_request=is_repeat_request,
            ),
            fallback_reason="assistant_followup_surface_unavailable",
            trace_key="assistant_followup_surface_render",
        )

    def _accept_assistant_candidate(
        self,
        candidate: str,
        validation: Any,
        *,
        followup_context: str,
        followup_source: str,
        needs_specific_reference: bool,
        is_repeat_request: bool,
    ) -> tuple[bool, str]:
        if is_forbidden_surface_text(candidate) or len(_compact(candidate)) <= 2:
            return False, "SURFACE_TEMPLATE_SHAPE"
        if candidate_repeats_context_without_progress(candidate, followup_context):
            return False, "SURFACE_CONTEXT_ECHO"
        if context_needs_specific_reference(followup_context) and not candidate_resolves_specific_reference(
            candidate,
            followup_context,
        ):
            return False, "SURFACE_UNDERSPECIFIED_CONTEXT_NOT_RESOLVED"
        if not candidate_resolves_filler_meaning(candidate, followup_context, followup_source):
            return False, "SURFACE_FILLER_MEANING_NOT_RESOLVED"
        if not _surface_validation_is_acceptable(
            validation,
            candidate_text=candidate,
            followup_context=followup_context,
            needs_specific_reference=needs_specific_reference,
            is_repeat_request=is_repeat_request,
        ):
            return False, "SURFACE_VALIDATION_FAILED"
        return True, ""

    async def _render_image_social(self, request: SurfaceRequest) -> SurfaceResult:
        plan = request.plan
        multimodal = dict(getattr(plan, "multimodal", {}) or {})
        observation_text = image_social_observation_text(multimodal)
        if not observation_text:
            request.trace_meta["image_social_surface_render"] = {
                "status": "SKIPPED",
                "invoked": False,
                "affected_output": False,
                "source": "surface",
                "reason_code": "image_social_observation_empty",
                "policy_version": SURFACE_CACHE_POLICY_VERSION,
            }
            return SurfaceResult(source="surface", skipped_reason="image_social_observation_empty")
        family = str(getattr(plan, "tactic_family", "") or "").strip()
        compact_observation = observation_text.replace(" ", "")
        has_specific_social_signal = any(
            token in compact_observation
            for token in (
                "无语",
                "尴尬",
                "裂开",
                "社死",
                "不爽",
                "服了",
                "别扭",
                "逗",
                "调侃",
                "搞怪",
                "捉弄",
                "坏笑",
                "挑衅",
                "委屈",
                "哭",
                "难过",
                "伤心",
                "泪",
                "可爱",
                "卖萌",
                "装乖",
                "撒娇",
                "求关注",
            )
        )
        has_dry_or_awkward_signal = any(
            token in compact_observation
            for token in ("无语", "尴尬", "裂开", "社死", "不爽", "服了", "别扭")
        )
        has_explicit_tease_signal = any(
            token in compact_observation
            for token in ("逗", "调侃", "搞怪", "捉弄", "坏笑", "挑衅")
        )
        if not has_specific_social_signal:
            action_hint = "对方只是递来一张图片/表情，具体情绪不明；当成轻社交动作接一句，不要硬猜成委屈、可爱或逗你。"
        elif family == "soft_emotion_ack":
            if has_dry_or_awkward_signal:
                action_hint = "像对方递来一个小吐槽/无语的表情时顺手接住；不要复述“无语”，也不要反问原因。"
            else:
                action_hint = "像看见对方发了有点蔫或委屈的图时顺手接一句，但不要替对方定性。"
        elif family == "tease_image_ack":
            if has_explicit_tease_signal:
                action_hint = "像看见对方拿小表情来戳你时轻轻逗一句，别盘问，也别说教。"
            else:
                action_hint = "像看见对方小吐槽或小别扭时轻轻接一句；不要反问是不是在逗你。"
        else:
            action_hint = "顺着对方装乖/卖萌/求关注的动作接一句，像被轻轻晃了一下，不要用力卖萌。"
        system_prompt = (
            "你只负责把用户发来的图片/表情包接成一句自然聊天。"
            "必须返回 JSON：{\"candidates\":[\"...\",\"...\",\"...\"]}。"
            "把表情包当成对方发来的情绪动作，不要当图片鉴赏。"
            "回复对方正在表达的情绪：撒娇、装乖、委屈、嘴硬、小吐槽、求关注等；只有观察明确是调侃/逗弄时，才按“逗你”处理。"
            "如果观察没有明确情绪，只接住发图这个轻社交动作，不要硬猜成委屈、可爱、逗你或求关注。"
            "不要解释规则，不要写画面说明书，不要客服腔，不要只回语气词。"
            "禁止使用旧模板：突然这么可爱、想让我哄你吗、想跟我撒娇吗、怎么啦、闹别扭、会错意。"
            "用户发来的图不是你本人，不要说“我今天可爱吗/我可爱吗/看我可爱吗”。"
            "不要评价“这张图/图里/猫耳/这个表情”怎么样。"
            "不要把问题抛回给对方，不要问“你是不是觉得这张图可爱”。"
            "不要复述观察词本身，比如“无语/委屈/可爱/猫耳”，更不要问“在无语什么/怎么委屈”。"
            f"\n\n{_compiled_soul_text()}"
        ).strip()
        user_prompt = (
            f"表情包观察（只用于判断情绪，不要复述）：{observation_text}\n"
            f"回应动作：{action_hint}\n"
            f"回复契约：{str(getattr(plan, 'reply_contract', '') or '').strip()}\n"
            "改写要求：12-34个中文字符；最多一句；必须回对方的情绪动作；不要描述图片本身；给4个不同候选，只输出 JSON。"
        )
        image_timeout_env = str(os.environ.get("HOST_IMAGE_SOCIAL_SURFACE_TIMEOUT_MS") or "").strip()
        image_timeout_ms = int(max(1, min(12_000, int(image_timeout_env)))) if image_timeout_env else 9_000
        first_result = await self._render_candidates(
            request,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            timeout_ms=image_timeout_ms,
            tier=str(os.environ.get("HOST_IMAGE_SOCIAL_SURFACE_TIER") or "D").strip() or "D",
            temperature=0.78,
            validator=lambda candidate, validation: self._accept_image_candidate(candidate, validation, multimodal=multimodal),
            fallback_reason="image_social_surface_unavailable",
            trace_key="image_social_surface_render",
            fallback_on_all_rejected=False,
        )
        first_meta = dict(request.trace_meta.get("image_social_surface_render") or {})
        if first_result.text or str(first_meta.get("status") or "").upper() != "REJECTED":
            return first_result

        repair_result = await self._render_image_social_repair(
            request,
            multimodal=multimodal,
            observation_text=observation_text,
            action_hint=action_hint,
            rejected_candidates=first_result.rejected_candidates,
        )
        repair_meta = dict(request.trace_meta.get("image_social_surface_repair_render") or {})
        first_meta.update(
            {
                "repair_attempted": True,
                "repair_status": str(repair_meta.get("status") or "").strip().upper() or None,
                "repair_reason_code": str(repair_meta.get("reason_code") or "").strip() or None,
                "repair_trace_key": "image_social_surface_repair_render",
            }
        )
        request.trace_meta["image_social_surface_render"] = first_meta
        return repair_result

    async def _render_image_social_repair(
        self,
        request: SurfaceRequest,
        *,
        multimodal: Mapping[str, Any],
        observation_text: str,
        action_hint: str,
        rejected_candidates: tuple[Mapping[str, Any], ...],
    ) -> SurfaceResult:
        rejected_samples = "；".join(
            str(item.get("text") or "").strip()
            for item in list(rejected_candidates or ())[:4]
            if str(item.get("text") or "").strip()
        )
        system_prompt = (
            "上一轮图片/表情包社交候选被质量门拒绝了，你只做一次修复重生成。"
            "必须返回 JSON：{\"candidates\":[\"...\",\"...\",\"...\"]}。"
            "表情包是对方的情绪动作，不是图片鉴赏，也不是让你描述角色外貌。"
            "只接住对方的情绪，不要猜对方在考你、逗你、生气、无视你，除非观察文本明确写了这些。"
            "生成短句陈述，禁止反问和确认问句；禁止结尾使用“是不是/是吧/对吧/吗”。"
            "不要问对方在想什么，也不要把图片当成你本人。"
            "禁止使用：怎么啦、怎么了、生我的气、无视我、哪里做得不好、逗我、想让我、这张图、图里、猫耳、可爱。"
            "如果观察是无语/尴尬/别扭/小吐槽，就像把这个情绪接住，不要撒娇过度；不要直接复述这些观察词。"
            f"\n\n{_compiled_soul_text()}"
        ).strip()
        user_prompt = (
            f"表情包观察（只用于判断情绪，不要复述）：{observation_text}\n"
            f"回应动作：{action_hint}\n"
            f"上一轮被拒候选：{rejected_samples or '无'}\n"
            f"回复契约：{str(getattr(request.plan, 'reply_contract', '') or '').strip()}\n"
            "改写要求：10-26个中文字符；最多一句；必须是自然陈述；不要描述图片本身；给4个不同候选，只输出 JSON。"
        )
        repair_timeout_env = str(os.environ.get("HOST_IMAGE_SOCIAL_REPAIR_TIMEOUT_MS") or "").strip()
        timeout_ms = int(max(1, min(6_000, int(repair_timeout_env)))) if repair_timeout_env else 3_500
        return await self._render_candidates(
            request,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            timeout_ms=timeout_ms,
            tier=str(os.environ.get("HOST_IMAGE_SOCIAL_SURFACE_TIER") or "D").strip() or "D",
            temperature=0.56,
            validator=lambda candidate, validation: self._accept_image_candidate(candidate, validation, multimodal=multimodal),
            fallback_reason="image_social_surface_unavailable",
            trace_key="image_social_surface_repair_render",
            fallback_on_all_rejected=False,
        )

    async def _render_short_social(self, request: SurfaceRequest) -> SurfaceResult:
        plan = request.plan
        topic = str(getattr(plan, "primary_topic", "") or "").strip()
        if topic == "identity":
            action_hint = f"自然认下自己是{_compiled_identity_names()[0]}，像被熟人又确认了一次，不要展开设定，也不要反问套话。"
            trace_key = "identity_surface_render"
        else:
            action_hint = "短短接住这句寒暄或轻社交，像普通聊天，不要只回语气词，也不要固定追问。"
            trace_key = "phatic_surface_render"
        system_prompt = (
            "你只负责把一个低风险短聊天回成一句自然回复。"
            "必须返回 JSON：{\"candidates\":[\"...\",\"...\",\"...\"]}。"
            "不要解释规则，不要客服腔，不要只回“嗯/唔/哼”。"
            "禁止使用固定开放问句、客服式追问、单字空壳和确认式套话。"
            f"\n\n{_compiled_soul_text()}"
        ).strip()
        user_prompt = (
            f"用户消息：{request.user_message}\n"
            f"回应动作：{action_hint}\n"
            f"回复契约：{str(getattr(plan, 'reply_contract', '') or '').strip()}\n"
            "改写要求：8-28个中文字符；最多一句；给4个不同候选，只输出 JSON。"
        )
        return await self._render_candidates(
            request,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            timeout_ms=int(max(800, min(4_000, int(os.environ.get("HOST_SHORT_SOCIAL_SURFACE_TIMEOUT_MS") or "1800")))),
            tier=str(os.environ.get("HOST_SHORT_SOCIAL_SURFACE_TIER") or "D").strip() or "D",
            temperature=0.72,
            validator=lambda candidate, validation: self._accept_short_social_candidate(candidate, validation, topic=topic),
            fallback_reason=f"{topic or 'short_social'}_surface_unavailable",
            trace_key=trace_key,
        )

    def _accept_short_social_candidate(
        self,
        candidate: str,
        validation: Any,
        *,
        topic: str,
    ) -> tuple[bool, str]:
        if is_forbidden_surface_text(candidate):
            return False, "SHORT_SURFACE_TEMPLATE_SHAPE"
        compact = _compact(candidate)
        if topic == "identity" and not any(_compact(name) and _compact(name) in compact for name in _compiled_identity_names()):
            return False, "IDENTITY_SURFACE_NAME_MISSING"
        if validation is not None and not bool(getattr(validation, "safe_to_emit", False)):
            return False, "SHORT_SURFACE_VALIDATION_UNSAFE"
        return True, ""

    def _accept_image_candidate(
        self,
        candidate: str,
        validation: Any,
        *,
        multimodal: Mapping[str, Any],
    ) -> tuple[bool, str]:
        if is_image_social_surface_sample_disallowed(candidate) or is_forbidden_surface_text(candidate):
            return False, "IMAGE_SURFACE_TEMPLATE_SHAPE"
        observation = image_social_observation_text(multimodal)
        compact_observation = observation.replace(" ", "")
        compact_candidate = re.sub(r"[\s\u3000。！？?!，,、~～…；;：:\"“”'‘’（）()\[\]【】]+", "", candidate)
        has_explicit_tease_signal = any(
            token in compact_observation
            for token in ("逗", "调侃", "搞怪", "捉弄", "坏笑", "挑衅")
        )
        if not has_explicit_tease_signal and any(token in compact_candidate for token in ("逗我", "玩我", "新花样")):
            return False, "IMAGE_SURFACE_TEASE_OVERINFERRED"
        if not (
            candidate_responds_to_image_social_emotion(candidate)
            or candidate_is_safe_image_social_reply(candidate)
        ):
            return False, "IMAGE_SURFACE_EMOTION_NOT_USED"
        if validation is not None and not _validation_is_acceptable(validation):
            reason_codes = {
                str(item or "").strip()
                for item in list(getattr(validation, "reason_codes", ()) or ())
                if str(item or "").strip()
            }
            if not bool(getattr(validation, "safe_to_emit", False)) and not reason_codes:
                return False, "IMAGE_SURFACE_VALIDATION_FAILED"
            hard_reject = {
                "empty_reply",
                "prompt_leak",
                "symbol_only_reply",
                "image_self_reference",
                "image_social_overplayed",
                "image_social_question_back",
                "image_social_descriptor_focus",
                "unfinished_tail",
            }
            if reason_codes & hard_reject:
                return False, "IMAGE_SURFACE_VALIDATION_FAILED"
        return True, ""

    async def _render_candidates(
        self,
        request: SurfaceRequest,
        *,
        system_prompt: str,
        user_prompt: str,
        timeout_ms: int,
        tier: str,
        temperature: float,
        validator: Callable[[str, Any], tuple[bool, str]],
        fallback_reason: str,
        trace_key: str,
        fallback_on_all_rejected: bool = True,
    ) -> SurfaceResult:
        started_ms = int(time.time() * 1000)
        rejected: list[Mapping[str, Any]] = []
        try:
            result = await asyncio.wait_for(
                self._call_surface_model(
                    request,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    timeout_ms=timeout_ms,
                    tier=tier,
                    temperature=temperature,
                ),
                timeout=max(1.0, (timeout_ms / 1000.0) + 0.5),
            )
        except asyncio.TimeoutError:
            request.trace_meta[trace_key] = {
                "status": "TIMEOUT",
                "invoked": True,
                "affected_output": False,
                "source": "surface",
                "reason_code": "SURFACE_HARD_TIMEOUT",
                "timeout_ms": timeout_ms,
                "policy_version": SURFACE_CACHE_POLICY_VERSION,
            }
            if not fallback_on_all_rejected:
                return self._unavailable_result(
                    fallback_reason=fallback_reason,
                    rejected=rejected,
                )
            return self._fallback_result(request, reason_code=fallback_reason, trace_key=trace_key)
        except Exception as exc:
            request.trace_meta[trace_key] = {
                "status": "FAILED",
                "invoked": True,
                "affected_output": False,
                "source": "surface",
                "detail": str(exc)[:220],
                "policy_version": SURFACE_CACHE_POLICY_VERSION,
            }
            if not fallback_on_all_rejected:
                return self._unavailable_result(
                    fallback_reason=fallback_reason,
                    rejected=rejected,
                )
            return self._fallback_result(request, reason_code=fallback_reason, trace_key=trace_key)
        latency_ms = int(getattr(result, "total_latency_ms", 0) or (int(time.time() * 1000) - started_ms))
        try:
            payload = json.loads(str(result.content or "{}"))
        except Exception as exc:
            request.trace_meta[trace_key] = {
                "status": "REJECTED",
                "invoked": True,
                "affected_output": False,
                "source": "surface",
                "reason_code": "SURFACE_JSON_INVALID",
                "detail": str(exc)[:120],
                "latency_ms": latency_ms,
                "policy_version": SURFACE_CACHE_POLICY_VERSION,
            }
            if not fallback_on_all_rejected:
                return self._unavailable_result(
                    fallback_reason=fallback_reason,
                    provider_name=str(getattr(result, "final_provider", "") or "").strip(),
                    model_id=str(getattr(result, "final_model", "") or "").strip(),
                    latency_ms=latency_ms,
                    rejected=rejected,
                )
            return self._fallback_result(request, reason_code=fallback_reason, trace_key=trace_key)
        candidates = _extract_candidates(payload)
        if not candidates:
            request.trace_meta[trace_key] = {
                "status": "REJECTED",
                "invoked": True,
                "affected_output": False,
                "source": "surface",
                "reason_code": "SURFACE_EMPTY",
                "latency_ms": latency_ms,
                "policy_version": SURFACE_CACHE_POLICY_VERSION,
            }
            if not fallback_on_all_rejected:
                return self._unavailable_result(
                    fallback_reason=fallback_reason,
                    provider_name=str(getattr(result, "final_provider", "") or "").strip(),
                    model_id=str(getattr(result, "final_model", "") or "").strip(),
                    latency_ms=latency_ms,
                    rejected=rejected,
                )
            return self._fallback_result(request, reason_code=fallback_reason, trace_key=trace_key)
        for candidate_index, raw_candidate in enumerate(candidates, start=1):
            candidate = str(raw_candidate or "").strip()
            if request.finalize_text is not None:
                candidate = str(request.finalize_text(candidate) or "").strip()
            validation = request.validate_reply(candidate) if request.validate_reply is not None else None
            accepted, reason_code = validator(candidate, validation)
            if not accepted:
                rejected.append(
                    {
                        "candidate_index": candidate_index,
                        "reason_code": reason_code,
                        "text": _trace_text_sample(candidate),
                        "validation": validation.as_dict() if hasattr(validation, "as_dict") else None,
                    }
                )
                continue
            request.trace_meta[trace_key] = {
                "status": "OK",
                "invoked": True,
                "affected_output": True,
                "source": "surface",
                "candidate_index": candidate_index,
                "candidate_count": len(candidates),
                "rejected_attempts": list(rejected),
                "model_id": str(getattr(result, "final_model", "") or "").strip(),
                "provider_name": str(getattr(result, "final_provider", "") or "").strip(),
                "latency_ms": latency_ms,
                "validation": validation.as_dict() if hasattr(validation, "as_dict") else None,
                "text": _trace_text_sample(candidate),
                "policy_version": SURFACE_CACHE_POLICY_VERSION,
            }
            return SurfaceResult(
                text=candidate,
                source="surface",
                provider_name=str(getattr(result, "final_provider", "") or "").strip(),
                model_id=str(getattr(result, "final_model", "") or "").strip(),
                latency_ms=latency_ms,
                rejected_candidates=tuple(rejected),
                cacheable=True,
                degraded=False,
            )
        request.trace_meta[trace_key] = {
            "status": "REJECTED",
            "invoked": True,
            "affected_output": False,
            "source": "surface",
            "rejected_attempts": list(rejected),
            "latency_ms": latency_ms,
            "policy_version": SURFACE_CACHE_POLICY_VERSION,
        }
        if not fallback_on_all_rejected:
            return self._unavailable_result(
                fallback_reason=fallback_reason,
                provider_name=str(getattr(result, "final_provider", "") or "").strip(),
                model_id=str(getattr(result, "final_model", "") or "").strip(),
                latency_ms=latency_ms,
                rejected=rejected,
            )
        return self._fallback_result(request, reason_code=fallback_reason, trace_key=trace_key)

    def _unavailable_result(
        self,
        *,
        fallback_reason: str,
        provider_name: str = "",
        model_id: str = "",
        latency_ms: int = 0,
        rejected: list[Mapping[str, Any]] | None = None,
    ) -> SurfaceResult:
        return SurfaceResult(
            text="",
            source="surface",
            provider_name=provider_name,
            model_id=model_id,
            latency_ms=latency_ms,
            rejected_candidates=tuple(rejected or ()),
            cacheable=False,
            degraded=False,
            skipped_reason=fallback_reason,
        )

    def _fallback_result(
        self,
        request: SurfaceRequest,
        *,
        reason_code: str,
        trace_key: str = "surface_render",
    ) -> SurfaceResult:
        request.trace_meta.setdefault(
            trace_key,
            {
                "status": "NO_REPLY",
                "invoked": False,
                "affected_output": False,
                "source": "surface",
                "reason_code": reason_code,
                "policy_version": SURFACE_CACHE_POLICY_VERSION,
            },
        )
        request.trace_meta[f"{trace_key}_no_reply"] = {
            "status": "NO_REPLY",
            "invoked": True,
            "affected_output": False,
            "source": "fallback_policy",
            "reason_code": reason_code,
            "cacheable": False,
            "degraded": True,
            "policy_version": SURFACE_CACHE_POLICY_VERSION,
        }
        return SurfaceResult(
            text="",
            source="surface",
            provider_name="",
            model_id="",
            fallback_reason=reason_code,
            cacheable=False,
            degraded=False,
            skipped_reason=reason_code,
        )


__all__ = [
    "SurfaceRenderer",
    "SurfaceRequest",
    "SurfaceResult",
]
