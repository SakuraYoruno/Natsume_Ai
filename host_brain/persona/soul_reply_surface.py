from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import re

from host_brain.app.context.soul_runtime import CompiledSoul, get_compiled_soul
from host_brain.persona.fallback_policy import is_forbidden_surface_text


_ADDRESS_RE = re.compile(r"默认称呼[:：]\s*([^\s/／,，]+)")
_QUOTE_RE = re.compile(r'^[\"“”「」『』]+|[\"“”「」『』]+$')
_FOLLOWUP_CONTEXT_RE = re.compile(r"上一条助手回复前接的用户话是“([^”]{1,160})”")
_FOLLOWUP_SOURCE_RE = re.compile(r"上一条助手回复是“([^”]{1,160})”")
_FILLER_SOURCE_RE = re.compile(r"^(?:嗯+|唔+|咦+|哼+|啊+|哦+|噢+|诶+|哈+)$")
_SURFACE_COMPACT_RE = re.compile(r"[\s\u3000。！？?!，,、~～…；;：:\"“”'‘’（）()\[\]【】]+")


@dataclass(frozen=True)
class SoulVoiceProfile:
    display_name: str
    preferred_address: str = ""
    playful: bool = False
    warm: bool = True
    teasing: bool = False
    action_first: bool = False
    clingy: bool = False


def _normalize_text(raw: str) -> str:
    return str(raw or "").strip()


def _strip_quotes(raw: str) -> str:
    return _QUOTE_RE.sub("", _normalize_text(raw))


def _compact_surface_text(raw: str) -> str:
    return _SURFACE_COMPACT_RE.sub("", _normalize_text(raw))


def is_reply_surface_sample_disallowed(*, primary_topic: str, text: str) -> bool:
    """Reject polluted/generated fallback samples before they become hot cache.

    Reply-surface memory stores only text, so old cache entries written before
    metadata filters existed can still be returned by Redis/in-memory stores.
    This content guard keeps deterministic safety lines from masquerading as
    natural generated replies.
    """
    topic = _normalize_text(primary_topic)
    compact = _compact_surface_text(text)
    if not compact:
        return True
    if is_forbidden_surface_text(text):
        return True
    if topic != "assistant_reply_followup":
        return False
    if _FILLER_SOURCE_RE.match(compact):
        return True
    template_marker_groups = (
        ("刚才那句", "没接住", "不是在敷衍", "你是在问前面"),
        ("刚才我回得太短", "不是突然换话题"),
        ("刚才那个", "不是敷衍", "没接住"),
    )
    if any(all(marker in compact for marker in group) for group in template_marker_groups):
        return True
    if "那截吧" in compact and ("你是在问" in compact or "前面" in compact):
        return True
    if any(marker in compact for marker in ("是不是在问", "是不是想问", "想问的是", "你想问")):
        return True
    if any(marker in compact for marker in ("是不是想知道", "想知道我刚才那个", "唔是什么意思", "唔是啥意思", "唔什么意思")):
        return True
    if any(marker in compact for marker in ("你问这个是什么意思", "你问这是什么意思", "这个是什么意思呀", "这个什么意思呀")):
        return True
    if re.fullmatch(r"(哥哥|哥)?(你问)?(的是)?哪个(呀|啊|呢|啦|嘛|吧)?", compact):
        return True
    if any(marker in compact for marker in ("你问", "你说")) and any(marker in compact for marker in ("刚才那声", "刚才那个", "上一条", "短回复")):
        return True
    if "刚才那声" in compact:
        return True
    if "唔" in compact and any(marker in compact for marker in ("是在思考什么", "在思考什么", "在想什么", "是什么意思", "是啥意思", "什么意思")):
        return True
    if any(marker in compact for marker in ("卡了一下", "卡住了", "那个意思啦", "那个意思", "就这个意思", "就是这个意思")):
        return True
    if any(marker in compact for marker in ("问那个", "那个呀", "就是嗯", "就是那个", "就那样")):
        return True
    if any(marker in compact for marker in ("被什么吓到", "被吓到", "吓到了", "是不是被什么", "是不是被吓")):
        return True
    if any(marker in compact for marker in ("抱歉", "对不起", "不太清楚", "我确实不清楚", "我也不清楚", "我是想说")):
        return True
    if "知道什么" in compact and any(marker in compact for marker in ("是想说知道什么", "知道什么来着")):
        return True
    return False


def _build_voice_profile(soul: CompiledSoul) -> SoulVoiceProfile:
    display_name = _normalize_text(getattr(soul, "display_name", "")) or "她"
    preferred_address = ""
    for item in getattr(soul, "relationship_defaults", ()) or ():
        match = _ADDRESS_RE.search(str(item or ""))
        if match:
            preferred_address = _normalize_text(match.group(1))
            break

    rule_text = " ".join(str(item or "") for item in getattr(soul, "speech_rules", ()) or ())
    relationship_text = " ".join(str(item or "") for item in getattr(soul, "relationship_defaults", ()) or ())
    temperament_text = " ".join(str(item or "") for item in (getattr(soul, "temperament_core", ""), getattr(soul, "relationship_core", "")) if str(item or "").strip())
    signature_text = " ".join(_strip_quotes(str(item or "")) for item in getattr(soul, "signature_lines", ()) or ())
    playful = any(
        token in (rule_text + " " + relationship_text + " " + signature_text)
        for token in ("撒娇", "黏", "哥哥～", "哥哥", "嘛", "啦", "呀", "任性")
    )
    warm = not any(token in rule_text for token in ("冷漠", "疏离", "克制到冷"))
    teasing = any(token in (temperament_text + " " + signature_text) for token in ("幽默吐槽", "调侃", "反将一军"))
    action_first = any(token in (relationship_text + " " + signature_text) for token in ("会自己跑过来", "帮你搞定", "不走了", "赖着不走"))
    clingy = any(token in (relationship_text + " " + signature_text) for token in ("黏", "赖着不走", "不走了", "哥哥"))
    return SoulVoiceProfile(
        display_name=display_name,
        preferred_address=preferred_address,
        playful=playful,
        warm=warm,
        teasing=teasing,
        action_first=action_first,
        clingy=clingy,
    )


def get_soul_voice_profile() -> SoulVoiceProfile:
    try:
        return _build_voice_profile(get_compiled_soul())
    except Exception:
        return SoulVoiceProfile(display_name="她")


def _name_claim(profile: SoulVoiceProfile) -> str:
    if profile.playful:
        return f"我是{profile.display_name}呀。"
    if profile.warm:
        return f"我是{profile.display_name}。"
    return f"{profile.display_name}。"


def _address_prefix(profile: SoulVoiceProfile) -> str:
    if profile.preferred_address:
        return f"{profile.preferred_address}，"
    return ""


def _load_cached_primary_reply_text(
    *,
    primary_topic: str,
    family: str,
    variant: str,
    response_form_reason: str,
    render_target_lang: str,
    context_fingerprint: str = "",
    exclude_texts: Iterable[str] | None = None,
) -> str:
    supported_topics = {
        "greeting",
        "presence_ping",
        "light_ack",
        "playful_ping",
        "identity",
        "assistant_reply_followup",
    }
    if primary_topic not in supported_topics:
        return ""
    try:
        from host_brain.app.dependencies import get_reply_surface_memory_store  # noqa: PLC0415
        from host_brain.app.dependencies import get_conversation_turn_store  # noqa: PLC0415
        from host_brain.persona.reply_surface_memory import (  # noqa: PLC0415
            build_reply_surface_memory_key,
            sample_reply_surface_from_turn_history,
        )

        soul = get_compiled_soul()
        soul_id = str(getattr(soul, "soul_id", "") or "").strip()
        cache_key = build_reply_surface_memory_key(
            soul_id=soul_id,
            primary_topic=primary_topic,
            tactic_family=family,
            tactic_variant=variant,
            response_form_reason=response_form_reason,
            render_target_lang=render_target_lang,
            context_fingerprint=context_fingerprint,
        )
        memory_store = get_reply_surface_memory_store()
        expanded_exclude = [str(item or "").strip() for item in list(exclude_texts or []) if str(item or "").strip()]
        for _ in range(4):
            cached = str(
                memory_store.sample(
                    key=cache_key,
                    exclude=expanded_exclude,
                )
            ).strip()
            if not cached:
                break
            if not is_reply_surface_sample_disallowed(primary_topic=primary_topic, text=cached):
                return cached
            expanded_exclude.append(cached)
        historical = str(
            sample_reply_surface_from_turn_history(
                turn_store=get_conversation_turn_store(),
                soul_id=soul_id,
                primary_topic=primary_topic,
                tactic_family=family,
                tactic_variant=variant,
                response_form_reason=response_form_reason,
                render_target_lang=render_target_lang,
                context_fingerprint=context_fingerprint,
                exclude=exclude_texts,
            )
        ).strip()
        if historical and not is_reply_surface_sample_disallowed(primary_topic=primary_topic, text=historical):
            memory_store.remember(key=cache_key, text=historical)
            return historical
        return ""
    except Exception:
        return ""


def _assistant_followup_primary_reply_text(
    *,
    plan: object,
    render_target_lang: str = "zh",
    exclude_texts: Iterable[str] | None = None,
    allow_semantic_fallback: bool = True,
) -> str:
    contract_text = str(getattr(plan, "reply_contract", "") or "").strip()
    source_match = _FOLLOWUP_SOURCE_RE.search(contract_text)
    context_match = _FOLLOWUP_CONTEXT_RE.search(contract_text)
    source = _strip_quotes(str(source_match.group(1) if source_match else "").strip()).rstrip("。！？?!，,、 ").strip()
    context = _strip_quotes(str(context_match.group(1) if context_match else "").strip()).rstrip("。！？?!，,、 ").strip()
    if not source or not context:
        return ""
    source_compact = re.sub(r"[\s\u3000。！？?!，,、~～…]+", "", source)
    context_compact = re.sub(r"[\s\u3000。！？?!，,、~～…]+", "", context)
    if not source_compact or not context_compact or len(source_compact) > 8:
        return ""
    try:
        from host_brain.persona.reply_surface_memory import (  # noqa: PLC0415
            build_assistant_followup_context_fingerprint,
        )

        context_fingerprint = build_assistant_followup_context_fingerprint(source=source, context=context)
    except Exception:
        context_fingerprint = ""
    if not context_fingerprint:
        return ""
    cached = _load_cached_primary_reply_text(
        primary_topic="assistant_reply_followup",
        family=str(getattr(plan, "tactic_family", "") or "").strip(),
        variant=str(getattr(plan, "tactic_variant", "") or "").strip(),
        response_form_reason=str(getattr(plan, "response_form_reason", "") or "").strip(),
        render_target_lang=render_target_lang,
        context_fingerprint=context_fingerprint,
        exclude_texts=exclude_texts,
    )
    if cached:
        return cached
    _ = allow_semantic_fallback
    return ""


def build_phatic_fallback_text(*, family: str, variant: str = "", render_target_lang: str = "zh") -> str:
    if str(render_target_lang or "").strip().lower() == "ja":
        return ""
    profile = get_soul_voice_profile()
    address = _address_prefix(profile)
    variant_map = {
        ("phatic_wave", "greeting"): f"{address}我在呀。" if address else "我在呀。",
        ("phatic_wave", "presence"): f"{address}在呀在呀，怎么啦？" if address else "在呀在呀，怎么啦？",
        ("phatic_wave", "light_ack"): f"{address}嗯，我在呢。" if address else "嗯，我在呢。",
        ("playful_wave", "playful_ping"): f"{address}突然来戳我呀？" if address else "突然来戳我呀？",
    }
    if (family, variant) in variant_map:
        return variant_map[(family, variant)]
    if family == "playful_wave":
        return f"{address}怎么啦，想找我呀？" if address else "怎么啦，想找我呀？"
    return f"{address}我在呀。" if address else "我在呀。"


def build_image_social_fallback_text(*, family: str, variant: str = "", render_target_lang: str = "zh") -> str:
    if str(render_target_lang or "").strip().lower() == "ja":
        return ""
    profile = get_soul_voice_profile()
    address = _address_prefix(profile)
    variant_map = {
        ("soft_emotion_ack", "distress_image"): f"{address}这张看着有点蔫，我先不乱猜。" if address else "这张看着有点蔫，我先不乱猜。",
        ("soft_emotion_ack", "soft_hold"): f"{address}这张软软的，有点让人想放轻声音。" if address else "这张软软的，有点让人想放轻声音。",
        ("soft_emotion_ack", "steady_hold"): f"{address}这张情绪有点低，我先轻点接。" if address else "这张情绪有点低，我先轻点接。",
        ("soft_emotion_ack", "image_social"): f"{address}这张我看到了，先不替你乱扣意思。" if address else "这张我看到了，先不替你乱扣意思。",
        ("soft_emotion_ack", "backoff_hold"): f"{address}好嘛，那我先把刚才那层意思收回去。" if address else "好嘛，那我先把刚才那层意思收回去。",
        ("soft_emotion_ack", "explain_read"): f"{address}我刚才是顺着那张图的情绪读过去了。" if address else "我刚才是顺着那张图的情绪读过去了。",
        ("soft_emotion_ack", "let_it_pass"): f"{address}行吧，那这张我就先当你随手丢来的。" if address else "行吧，那这张我就先当你随手丢来的。",
        ("cute_image_ack", "cute_ping"): f"{address}这张可爱得有点犯规。" if address else "这张可爱得有点犯规。",
        ("cute_image_ack", "soft_hold"): f"{address}这张软萌得有点犯规。" if address else "这张软萌得有点犯规。",
        ("tease_image_ack", "light_nudge"): f"{address}这张小表情有点会戳人。" if address else "这张小表情有点会戳人。",
        ("tease_image_ack", "deny_nudge"): f"{address}好好好，那我把刚才那点解读收回去。" if address else "好好好，那我把刚才那点解读收回去。",
        ("tease_image_ack", "light_explain"): f"{address}我刚才是被这张小表情带偏了一下。" if address else "我刚才是被这张小表情带偏了一下。",
        ("tease_image_ack", "soft_slide"): f"{address}行吧，那这张就当轻轻戳我一下。" if address else "行吧，那这张就当轻轻戳我一下。",
    }
    if (family, variant) in variant_map:
        return variant_map[(family, variant)]
    family_map = {
        "soft_emotion_ack": f"{address}这张我看到了，先不乱猜。" if address else "这张我看到了，先不乱猜。",
        "cute_image_ack": f"{address}这张可爱得有点犯规。" if address else "这张可爱得有点犯规。",
        "tease_image_ack": f"{address}这张小表情有点会戳人。" if address else "这张小表情有点会戳人。",
    }
    return family_map.get(family, f"{address}这张我看到了，先不乱猜它的意思。" if address else "这张我看到了，先不乱猜它的意思。")


def build_boundary_fallback_text(*, family: str, variant: str = "", render_target_lang: str = "zh") -> str:
    if str(render_target_lang or "").strip().lower() == "ja":
        return ""
    profile = get_soul_voice_profile()
    address = _address_prefix(profile)
    variant_map = {
        ("short_boundary_then_reason", "plain_reason"): f"{address}也没熟到那种地步啦，不过看见了我也不会装作没看到。" if address else "也没熟到那种地步啦，不过看见了我也不会装作没看到。",
        ("ack_still_care_then_new_reason", "still_care_reason"): f"{address}你还惦记这个呀？也没熟到那种地步，不过我不会装作没看到。" if address else "你还惦记这个呀？也没熟到那种地步，不过我不会装作没看到。",
        ("hold_boundary_then_shift_floor", "shift_real_question"): f"{address}也没熟到那种地步啦，不过这事我已经说过啦。你真正想问的是后面那层吧？" if address else "也没熟到那种地步啦，不过这事我已经说过啦。你真正想问的是后面那层吧？",
        ("hold_boundary_then_shift_floor", "shift_other_question"): f"{address}也没熟到那种地步啦，不过这事我已经说过啦。你要问别的就直接问嘛。" if address else "也没熟到那种地步啦，不过这事我已经说过啦。你要问别的就直接问嘛。",
        ("brief_reason_then_wait", "fresh_reason"): f"{address}也没熟到那种地步啦，就是看见了没法装作没看到。" if address else "也没熟到那种地步啦，就是看见了没法装作没看到。",
        ("soft_close_boundary", "settle_repeat"): f"{address}这件事我已经说过啦，也没熟到那种地步，不过我不会装作没看到。" if address else "这件事我已经说过啦，也没熟到那种地步，不过我不会装作没看到。",
        ("soft_close_boundary", "settle_hold"): f"{address}先停在这吧，反正我不会装作没看到。" if address else "先停在这吧，反正我不会装作没看到。",
    }
    if (family, variant) in variant_map:
        return variant_map[(family, variant)]
    return f"{address}也没熟到那种地步啦，不过看见了我也不会装作没看到。" if address else "也没熟到那种地步啦，不过看见了我也不会装作没看到。"


def build_help_fallback_text(*, family: str, variant: str = "", render_target_lang: str = "zh") -> str:
    if str(render_target_lang or "").strip().lower() == "ja":
        return ""
    profile = get_soul_voice_profile()
    address = _address_prefix(profile)
    variant_map = {
        ("short_conditional_commitment", "plain_conditional"): f"{address}你真有事就说呀，我又不会装作没听见。" if address else "你真有事就说呀，我又不会装作没听见。",
        ("ack_anxiety_without_repromise", "soothe_hold"): f"{address}你还在担心这个呀？真有事我会回应你的。" if address else "你还在担心这个呀？真有事我会回应你的。",
        ("ack_anxiety_without_repromise", "steady_reassure"): f"{address}你真碰上事就叫我，我会回应的。" if address else "你真碰上事就叫我，我会回应的。",
        ("soft_reassure_then_open_floor", "soothe_then_open"): f"{address}真有事你就直说嘛。" if address else "真有事你就直说嘛。",
        ("hold_support_without_repromise", "steady_open_floor"): f"{address}我会搭把手的。你要说别的就接着说呀。" if address else "我会搭把手的。你要说别的就接着说呀。",
        ("steady_commitment_then_wait", "quiet_hold"): f"{address}反正我不会装作没听见的。" if address else "反正我不会装作没听见的。",
    }
    if (family, variant) in variant_map:
        return variant_map[(family, variant)]
    return f"{address}真有事就说呀，我会回应的。" if address else "真有事就说呀，我会回应的。"


def build_repair_fallback_text(*, render_target_lang: str = "zh") -> str:
    if str(render_target_lang or "").strip().lower() == "ja":
        return ""
    profile = get_soul_voice_profile()
    address = _address_prefix(profile)
    return f"{address}好嘛，我收一点。你要是不想听这个，我们就先停在这。" if address else "好嘛，我收一点。你要是不想听这个，我们就先停在这。"


def build_identity_reply_contract(*, family: str, variant: str = "", render_target_lang: str = "zh") -> str:
    if str(render_target_lang or "").strip().lower() == "ja":
        return ""
    profile = get_soul_voice_profile()
    address_note = ""
    if profile.preferred_address:
        address_note = f"可以自然叫对方“{profile.preferred_address}”，"
    tone_note = "语气活一点，像熟人间认领自己。" if profile.playful else "语气自然一点，像熟人间接上话。"
    base = {
        "brief_name_only": f"像被叫住时自然认领自己那样，只短短说“我是{profile.display_name}”，不要展开设定。",
        "ack_name_then_wait": f"这轮不是第一次问身份：{address_note}{tone_note}短短认下“我是{profile.display_name}”，然后停住，不要像客服或审问，也不要落成固定问句。",
        "ack_repeat_then_light_probe": f"先自然认下“我是{profile.display_name}”，{address_note}再用轻一点的方式接住他怎么又问这个，像熟人间顺手接话，不要用固定追问句。",
        "hold_name_then_open_floor": f"先自然认下“我是{profile.display_name}”，{address_note}再像熟人接话那样把话口让回去，不要用盘问式或固定开放问句。",
        "name_only_then_pause": f"只做最短的身份确认，比如“我是{profile.display_name}呀”，不要继续解释。",
        "soft_close_then_open_floor": f"轻轻接住他又问了一次，{address_note}可以带一点小抱怨，再自然把话口让回去，不要硬顶，也不要掉进固定问句。",
    }
    variant_overrides = {
        ("ack_repeat_then_light_probe", "confirm_part"): f"先自然认下“我是{profile.display_name}”，{address_note}再顺手接一句，像在逗他又确认这个，不要固定成“哪一部分”。",
        ("ack_repeat_then_light_probe", "confirm_real_question"): f"先自然认下“我是{profile.display_name}”，{address_note}再顺着接他怎么又问到这，不要落成“真正想问什么”。",
        ("ack_repeat_then_light_probe", "confirm_motive"): f"先自然认下“我是{profile.display_name}”，{address_note}再轻轻接住他怎么又问这个，不要把名字展开成介绍。",
        ("hold_name_then_open_floor", "open_other_topic"): f"先自然认下“我是{profile.display_name}”，{address_note}再像熟人间顺手把别的话头递回去，不要固定成“是不是想问别的”。",
        ("hold_name_then_open_floor", "open_real_question"): f"先自然认下“我是{profile.display_name}”，{address_note}再用更松一点的方式把话头递回去，不要用盘问句。",
        ("soft_close_then_open_floor", "settle_real_question"): f"轻轻接住这已经问过几次了，{address_note}再自然往下接，不要像在审问，也不要固定问句。",
        ("soft_close_then_open_floor", "settle_other_topic"): f"轻轻接住这已经问过几次了，{address_note}再自然把别的话头递回去，不要硬顶。",
        ("soft_close_then_open_floor", "settle_then_open"): f"轻轻把这件事收住，{address_note}再自然往下接，不要只剩一个固定开放问句。",
    }
    return variant_overrides.get((family, variant), base.get(family, "先自然接住对方，再短短回应。"))


def build_boundary_reply_contract(*, family: str, variant: str = "", render_target_lang: str = "zh") -> str:
    if str(render_target_lang or "").strip().lower() == "ja":
        return ""
    profile = get_soul_voice_profile()
    address_note = f'可以自然叫一声“{profile.preferred_address}”，' if profile.preferred_address else ""
    teasing_note = "可以带一点轻轻的吐槽或撒娇感，" if (profile.playful or profile.teasing) else ""
    warm_note = "语气别冷，也不要像在划清界限。" if profile.warm else "语气自然一点，不要太硬。"
    base = {
        "short_boundary_then_reason": f"像新海天顺嘴解释那样，先轻轻把“不算熟”说清，再给一句很日常的理由。{address_note}{teasing_note}{warm_note}不要落成固定句。",
        "ack_still_care_then_new_reason": f"先接住对方还在惦记这件事，再把距离放稳，补一句新的短理由。{address_note}{teasing_note}不要重复上一轮结构。",
        "hold_boundary_then_shift_floor": f"先把距离放稳，但别说得像拒人千里。{address_note}{teasing_note}把理由收短，再自然把话口递回去，不要用固定开放问句。",
        "brief_reason_then_wait": f"只给一句很生活化的短理由，把态度放稳。{address_note}{warm_note}不要重讲，也不要解释成长段。",
        "soft_close_boundary": f"把这类重复追问轻轻收住，像熟人间说“这件事差不多就到这”。{address_note}{teasing_note}不要再用原句复述理由。",
    }
    variant_overrides = {
        ("ack_still_care_then_new_reason", "still_care_reason"): f"先接住“你还在意这个”，再换一个新一点、轻一点的理由。{address_note}{teasing_note}不要重复固定理由句。",
        ("hold_boundary_then_shift_floor", "shift_real_question"): f"把“不算熟”轻轻放稳，再顺着把真正想问的口子让回去。{address_note}不要写成客服式追问。",
        ("hold_boundary_then_shift_floor", "shift_other_question"): f"把距离放稳，再很自然地把别的话头递回去。{address_note}{teasing_note}不要用固定句式。",
        ("soft_close_boundary", "settle_repeat"): f"轻轻收住重复追问，别再整句重复“不熟 + 理由”。{address_note}更像顺手按住这个话头。",
        ("soft_close_boundary", "settle_hold"): f"把这件事停在这，态度稳住就行。{address_note}{warm_note}不要再展开。",
    }
    return variant_overrides.get((family, variant), base.get(family, "先把距离放稳，再自然回应。"))


def build_help_reply_contract(*, family: str, variant: str = "", render_target_lang: str = "zh") -> str:
    if str(render_target_lang or "").strip().lower() == "ja":
        return ""
    profile = get_soul_voice_profile()
    address_note = f'可以自然叫一声“{profile.preferred_address}”，' if profile.preferred_address else ""
    action_note = "更像顺手站过来，不像郑重承诺。" if profile.action_first else "把态度放稳，不要说得太重。"
    playful_note = "可以有一点轻轻的撒娇感，但别像背台词。" if profile.playful else "语气自然一点。"
    base = {
        "short_conditional_commitment": f"像新海天顺手应下来那样，说清楚“真有事就说”。{address_note}{action_note}{playful_note}",
        "ack_anxiety_without_repromise": f"先接住对方还在担心，再把“我会回应”这件事放稳。{address_note}{playful_note}不要把承诺整句重报。",
        "steady_commitment_then_wait": f"把会回应的态度放稳就够了。{address_note}{action_note}不要变成长承诺。",
        "soft_reassure_then_open_floor": f"先让对方安心一点，再把话口自然递回去。{address_note}{playful_note}不要用固定开放问句。",
        "hold_support_without_repromise": f"把会回应这件事放稳，再顺手把别的话头接回去。{address_note}{action_note}",
    }
    variant_overrides = {
        ("ack_anxiety_without_repromise", "soothe_hold"): f"先接住“你还在担心”，再把态度放稳。{address_note}不要整句重许诺。",
        ("ack_anxiety_without_repromise", "steady_reassure"): f"把安心感说得更稳一点，但仍然别用完整承诺句。{address_note}{playful_note}",
        ("soft_reassure_then_open_floor", "soothe_then_open"): f"先安一下心，再自然把话头让回去。{address_note}不要落成固定追问。",
        ("hold_support_without_repromise", "steady_open_floor"): f"稳住“我会回应”，再把别的话口接回来。{address_note}{action_note}不要沿用上一轮结构。",
    }
    return variant_overrides.get((family, variant), base.get(family, "先把态度放稳，再自然回应。"))


def build_identity_fallback_text(*, family: str, variant: str = "", render_target_lang: str = "zh") -> str:
    if str(render_target_lang or "").strip().lower() == "ja":
        return ""
    profile = get_soul_voice_profile()
    name_line = _name_claim(profile)
    address = _address_prefix(profile)
    variant_map = {
        ("brief_name_only", "plain_name"): name_line,
        ("ack_name_then_wait", "quiet_confirm"): f"{address}连我都认不出来啦？{name_line}" if address else f"连我都认不出来啦？{name_line}",
        ("ack_repeat_then_light_probe", "confirm_part"): f"{address}怎么又问起这个啦？{name_line}",
        ("ack_repeat_then_light_probe", "confirm_real_question"): f"{address}又问到这个呀？{name_line}",
        ("ack_repeat_then_light_probe", "confirm_motive"): f"{address}{name_line}怎么又问起这个啦？",
        ("hold_name_then_open_floor", "open_other_topic"): f"{address}{name_line}你要说什么就直接说嘛。",
        ("hold_name_then_open_floor", "open_real_question"): f"{address}{name_line}你要是还有别的，就直接跟我说呀。",
        ("soft_close_then_open_floor", "settle_real_question"): f"{address}都问我好几次啦。{name_line}你要是还有别的，就直接说呀。",
        ("soft_close_then_open_floor", "settle_other_topic"): f"{address}都问我好几次啦。你要说什么就直接说嘛。",
        ("soft_close_then_open_floor", "settle_then_open"): f"{address}都问我好几次啦。你就直接跟我说嘛。",
        ("name_only_then_pause", "quiet_pause"): name_line,
    }
    if (family, variant) in variant_map:
        return variant_map[(family, variant)]
    family_map = {
        "brief_name_only": name_line,
        "ack_name_then_wait": f"{address}连我都认不出来啦？{name_line}" if address else f"连我都认不出来啦？{name_line}",
        "ack_repeat_then_light_probe": f"{address}{name_line}怎么又问起这个啦？",
        "hold_name_then_open_floor": f"{address}{name_line}你要说什么就直接说嘛。",
        "name_only_then_pause": name_line,
        "soft_close_then_open_floor": f"{address}都问我好几次啦。你就直接跟我说嘛。",
    }
    return family_map.get(family, name_line)


def build_identity_naturalization_hint(*, family: str, render_target_lang: str = "zh") -> str:
    if str(render_target_lang or "").strip().lower() == "ja":
        return ""
    profile = get_soul_voice_profile()
    address_note = f'可以自然叫一声“{profile.preferred_address}”，' if profile.preferred_address else ""
    if family in {"ack_repeat_then_light_probe", "hold_name_then_open_floor", "soft_close_then_open_floor"}:
        return f"{address_note}语气像熟人间撒娇着重新接上话，不要像盘问。"
    return f"{address_note}语气自然一点，像熟悉的人被叫住时顺手认下自己。"


def build_boundary_naturalization_hint(*, family: str, render_target_lang: str = "zh") -> str:
    if str(render_target_lang or "").strip().lower() == "ja":
        return ""
    profile = get_soul_voice_profile()
    address_note = f'可以自然叫一声“{profile.preferred_address}”，' if profile.preferred_address else ""
    if family in {"hold_boundary_then_shift_floor", "soft_close_boundary"}:
        return f"{address_note}语气像熟人间顺嘴把话按住，不要像划界线或盘问。"
    return f"{address_note}语气像顺手解释一句，不要太硬，也不要像客服。"


def build_help_naturalization_hint(*, family: str, render_target_lang: str = "zh") -> str:
    if str(render_target_lang or "").strip().lower() == "ja":
        return ""
    profile = get_soul_voice_profile()
    address_note = f'可以自然叫一声“{profile.preferred_address}”，' if profile.preferred_address else ""
    if family in {"soft_reassure_then_open_floor", "hold_support_without_repromise"}:
        return f"{address_note}语气像顺手站过来接一句，不要像正式承诺。"
    return f"{address_note}语气放松一点，像嘴上轻轻应下来，不要郑重其事。"


def build_image_social_naturalization_hint(*, family: str, render_target_lang: str = "zh") -> str:
    if str(render_target_lang or "").strip().lower() == "ja":
        return ""
    profile = get_soul_voice_profile()
    address_note = f'可以自然叫一声“{profile.preferred_address}”，' if profile.preferred_address else ""
    if family == "soft_emotion_ack":
        return f"{address_note}像看见对方委屈或蔫下来时顺手哄一句，不要解说“这张图/这个表情”。"
    if family == "cute_image_ack":
        return f"{address_note}像被对方拿可爱表情蹭了一下时顺手接话，可以带一点撒娇感，但不要显得用力卖萌。"
    if family == "tease_image_ack":
        return f"{address_note}像看到对方拿小别扭表情来戳你时轻轻逗一句，不要变成盘问。"
    return f"{address_note}顺着图里的语气自然接一句，不要解说画面。"


def build_deterministic_primary_reply_text(
    *,
    plan: object,
    render_target_lang: str = "zh",
    exclude_texts: Iterable[str] | None = None,
    allow_semantic_fallback: bool = True,
) -> str:
    """Render low-risk, thread-aware primary replies without invoking the LLM.

    Primary render now prefers previously generated natural replies cached by
    topic/tactic. If no cached reply exists, the normal chat generation
    pipeline should handle the request instead of falling back to a hardcoded
    surface line.
    """
    primary_topic = str(getattr(plan, "primary_topic", "") or "").strip()
    family = str(getattr(plan, "tactic_family", "") or "").strip()
    variant = str(getattr(plan, "tactic_variant", "") or "").strip()
    response_form_reason = str(getattr(plan, "response_form_reason", "") or "").strip()
    if primary_topic == "assistant_reply_followup" and family == "explain_previous_reply":
        return _assistant_followup_primary_reply_text(
            plan=plan,
            render_target_lang=render_target_lang,
            exclude_texts=exclude_texts,
            allow_semantic_fallback=allow_semantic_fallback,
        )
    return _load_cached_primary_reply_text(
        primary_topic=primary_topic,
        family=family,
        variant=variant,
        response_form_reason=response_form_reason,
        render_target_lang=render_target_lang,
        exclude_texts=exclude_texts,
    )


__all__ = [
    "SoulVoiceProfile",
    "build_boundary_naturalization_hint",
    "build_boundary_reply_contract",
    "build_boundary_fallback_text",
    "build_help_fallback_text",
    "build_help_naturalization_hint",
    "build_help_reply_contract",
    "build_identity_fallback_text",
    "build_identity_naturalization_hint",
    "build_identity_reply_contract",
    "build_image_social_fallback_text",
    "build_deterministic_primary_reply_text",
    "build_image_social_naturalization_hint",
    "build_phatic_fallback_text",
    "build_repair_fallback_text",
    "get_soul_voice_profile",
    "is_reply_surface_sample_disallowed",
]
