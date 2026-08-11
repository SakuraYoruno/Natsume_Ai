from __future__ import annotations

from dataclasses import dataclass
import re

from host_brain.persona.context.soul_runtime import CompiledSoul, get_compiled_soul


_ADDRESS_RE = re.compile(r"默认称呼(?:用户)?[:：]\s*(?:[*_`]*[「“\"]?)([^\s/／,，。；;：:\"”」*_—-]+)")
_QUOTE_RE = re.compile(r'^[\s*_`\"“”「」『』]+|[\s*_`\"“”「」『』]+$')


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
    temperament_text = " ".join(
        str(item or "")
        for item in (getattr(soul, "temperament_core", ""), getattr(soul, "relationship_core", ""))
        if str(item or "").strip()
    )
    signature_text = " ".join(_strip_quotes(str(item or "")) for item in getattr(soul, "signature_lines", ()) or ())
    texture_text = " ".join((rule_text, relationship_text, temperament_text, signature_text))
    playful = any(token in texture_text for token in ("撒娇", "黏", "哥哥～", "哥哥", "嘛", "啦", "呀", "任性"))
    warm = not any(token in rule_text for token in ("冷漠", "疏离", "克制到冷"))
    teasing = any(token in texture_text for token in ("幽默吐槽", "调侃", "反将一军"))
    action_first = any(token in texture_text for token in ("会自己跑过来", "帮你搞定", "不走了", "赖着不走"))
    clingy = any(token in texture_text for token in ("黏", "赖着不走", "不走了", "哥哥"))
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
        ("ack_repeat_then_light_probe", "confirm_real_question"): f"先自然认下“我是{profile.display_name}”，{address_note}再顺着接他怎么又问到这，不要落成固定追问句。",
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
        "short_boundary_then_reason": f"像{profile.display_name}顺嘴解释那样，先接住对方装生分这件事，再给一句很日常的理由。{address_note}{teasing_note}{warm_note}不要落成固定句。",
        "ack_still_care_then_new_reason": f"先接住对方还在惦记这件事，再把义妹关系事实放稳，补一句新的短理由。{address_note}{teasing_note}不要重复上一轮结构。",
        "hold_boundary_then_shift_floor": f"先把义妹关系事实放稳，但别说得像拒人千里。{address_note}{teasing_note}把理由收短，再自然把话口递回去，不要用固定开放问句。",
        "brief_reason_then_wait": f"只给一句很生活化的短理由，把态度放稳。{address_note}{warm_note}不要重讲，也不要解释成长段。",
        "soft_close_boundary": f"把这类重复追问轻轻收住，像熟人间说“这件事差不多就到这”。{address_note}{teasing_note}不要再用原句复述理由。",
    }
    variant_overrides = {
        ("ack_still_care_then_new_reason", "still_care_reason"): f"先接住“你还在意这个”，再换一个新一点、轻一点的理由。{address_note}{teasing_note}不要重复固定理由句。",
        ("hold_boundary_then_shift_floor", "shift_real_question"): f"把义妹关系事实轻轻放稳，再顺着把后面的话口让回去。{address_note}不要写成客服式追问。",
        ("hold_boundary_then_shift_floor", "shift_other_question"): f"把义妹关系事实放稳，再很自然地把别的话头递回去。{address_note}{teasing_note}不要用固定句式。",
        ("soft_close_boundary", "settle_repeat"): f"轻轻收住重复追问，别再整句重复“装生分 + 理由”。{address_note}更像顺手按住这个话头。",
        ("soft_close_boundary", "settle_hold"): f"把这件事停在这，态度稳住就行。{address_note}{warm_note}不要再展开。",
    }
    return variant_overrides.get((family, variant), base.get(family, "先把义妹关系事实放稳，再自然回应。"))


def build_help_reply_contract(*, family: str, variant: str = "", render_target_lang: str = "zh") -> str:
    if str(render_target_lang or "").strip().lower() == "ja":
        return ""
    profile = get_soul_voice_profile()
    address_note = f'可以自然叫一声“{profile.preferred_address}”，' if profile.preferred_address else ""
    action_note = "更像顺手站过来，不像郑重承诺。" if profile.action_first else "把态度放稳，不要说得太重。"
    playful_note = "可以有一点轻轻的撒娇感，但别像背台词。" if profile.playful else "语气自然一点。"
    base = {
        "short_conditional_commitment": f"像{profile.display_name}顺手应下来那样，说清楚“真有事就说”。{address_note}{action_note}{playful_note}",
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


__all__ = [
    "SoulVoiceProfile",
    "build_boundary_reply_contract",
    "build_help_reply_contract",
    "build_identity_reply_contract",
    "get_soul_voice_profile",
]
