from __future__ import annotations

from dataclasses import dataclass
import re

from host_brain.app.context.soul_runtime import CompiledSoul, get_compiled_soul


_ADDRESS_RE = re.compile(r"默认称呼[:：]\s*([^\s/／,，]+)")
_QUOTE_RE = re.compile(r'^[\"“”「」『』]+|[\"“”「」『』]+$')


@dataclass(frozen=True)
class SoulVoiceProfile:
    display_name: str
    preferred_address: str = ""
    playful: bool = False
    warm: bool = True


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
    signature_text = " ".join(_strip_quotes(str(item or "")) for item in getattr(soul, "signature_lines", ()) or ())
    playful = any(
        token in (rule_text + " " + signature_text)
        for token in ("撒娇", "黏", "哥哥～", "哥哥", "嘛", "啦", "呀")
    )
    warm = not any(token in rule_text for token in ("冷漠", "疏离", "克制到冷"))
    return SoulVoiceProfile(
        display_name=display_name,
        preferred_address=preferred_address,
        playful=playful,
        warm=warm,
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
        ("ack_repeat_then_light_probe", "confirm_real_question"): f"先自然认下“我是{profile.display_name}”，{address_note}再顺着接他怎么又问到这，不要落成“真正想问什么”。",
        ("ack_repeat_then_light_probe", "confirm_motive"): f"先自然认下“我是{profile.display_name}”，{address_note}再轻轻接住他怎么又问这个，不要把名字展开成介绍。",
        ("hold_name_then_open_floor", "open_other_topic"): f"先自然认下“我是{profile.display_name}”，{address_note}再像熟人间顺手把别的话头递回去，不要固定成“是不是想问别的”。",
        ("hold_name_then_open_floor", "open_real_question"): f"先自然认下“我是{profile.display_name}”，{address_note}再用更松一点的方式把话头递回去，不要用盘问句。",
        ("soft_close_then_open_floor", "settle_real_question"): f"轻轻接住这已经问过几次了，{address_note}再自然往下接，不要像在审问，也不要固定问句。",
        ("soft_close_then_open_floor", "settle_other_topic"): f"轻轻接住这已经问过几次了，{address_note}再自然把别的话头递回去，不要硬顶。",
        ("soft_close_then_open_floor", "settle_then_open"): f"轻轻把这件事收住，{address_note}再自然往下接，不要只剩一个固定开放问句。",
    }
    return variant_overrides.get((family, variant), base.get(family, "先自然接住对方，再短短回应。"))


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


__all__ = [
    "SoulVoiceProfile",
    "build_identity_fallback_text",
    "build_identity_naturalization_hint",
    "build_identity_reply_contract",
    "get_soul_voice_profile",
]
