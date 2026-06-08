from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from host_brain.app.context.soul_runtime import get_compiled_soul
from .reply_plan import ReplyPlan
from .reply_tactics import canonical_tactic_signature, describe_tactic_signature, tactic_key


_GENERIC_ASSISTANT_RE = re.compile(r"(AI助手|人工智能|很高兴和你交流|请告诉我你需要什么帮助)")
_HALF_SENTENCE_RE = re.compile(r"^(?:嗯|哎|诶|啊|哦|唔|当然|因为|只是|也不是|看情况)[。！？?!～~…，,\s]*$")
_PROMISE_RESTATE_RE = re.compile(r"(我会帮你|我会继续帮你|我当然会继续帮你|我会一直帮你)")
_OPEN_FLOOR_RE = re.compile(r"(直接问|直接说|想问别的|还有别的|真正想问的|那就说吧|就说吧|往下问什么|还想往下问什么|还想问什么|想问什么|是不是想问别的|你其实想问什么|你要问别的就直说|还想问我什么)")
_LIGHT_PROBE_RE = re.compile(r"(还在确认|还是想确认|又来确认|还想再问|还想听|是在确认|为什么还在问|哪一部分|哪部分|确认的是哪|想问我的名字吧|不是名字吧|其实想问|怎么又问起这个|怎么又问这个|连我都认不出来)")
_ANXIETY_RE = re.compile(r"(不安|担心|还是担心|还是不安|还在担心)")
_REPEAT_ACK_RE = re.compile(r"(刚才|已经|之前|前面|不是第一次|已经说过|已经告诉过|前面说过|早就说过|问过了|又问起这个|又问这个|怎么又问|都问我好几次|问我好几次)")
_SOFT_CLOSE_RE = re.compile(r"(来回问|问过几次|一直问这个|总问这个|翻来覆去|来来回回|都问我好几次|问我好几次)")
_CARE_ACK_RE = re.compile(r"(还在意|还是在意|还记着|还惦记|还放不下)")
_BOUNDARY_DISTANCE_RE = re.compile(r"(不熟|不是很熟|不算熟|没熟到那种地步|熟到那种地步)")
_STEPSISTER_BOUNDARY_ANCHOR_RE = re.compile(
    r"(义妹|妹妹|一家人|家里人|别装(?:生分|陌生|不熟)|装(?:生分|陌生|不熟)|见外|外人|划清界限|往外推|不当外人)"
)
_STEPSISTER_REASON_RE = re.compile(
    r"(会管|我管|要管|会帮|我帮|帮你|插手|接手|放着不管|不可能不管|不能不管|不当外人|别装(?:生分|陌生|不熟)|装(?:生分|陌生|不熟)|见外)"
)
_BOUNDARY_REASON_TAIL_RE = re.compile(r"(只是|看见了|看到了|装作没看见|装作没看到|不会装作没看见|不会装作没看到|没法装作没看见|没法装作没看到|当没看见|当没看到)")
_MOTIVATION_REASON_TAIL_RE = re.compile(
    r"(顺路|路过|碰上了|正好碰上|既然都碰上了|既然都看到了|总不能当没看见|总不能当没看到|总不能装作没看见|总不能装作没看到|也不能当没看见|也不能当没看到)"
)
_BOUNDARY_REASON_RE = re.compile(
    r"(不熟|不是很熟|不算熟|没熟到那种地步|熟到那种地步).*(只是|看见了|看到了|装作没看见|装作没看到|不会装作没看见|不会装作没看到|没法装作没看见|没法装作没看到|当没看见|当没看到|顺路|路过|碰上了|正好碰上|既然都碰上了|既然都看到了|总不能当没看见|总不能当没看到|总不能装作没看见|总不能装作没看到|也不能当没看见|也不能当没看到)"
)
_SUPPORT_RE = re.compile(r"(不会装作没听见|会回应|会理|会回|真开口的话)")
_CONDITIONAL_SUPPORT_RE = re.compile(r"(看情况|真开口的话|你真开口|你要是真开口)")
_REPAIR_ACK_RE = re.compile(r"(好|知道了|明白了|我收一点|我会收一点|我往后一点)")
_SOCIAL_ACK_RE = re.compile(r"(你好|嗨|hello|hi|我在|在呢|来了|怎么啦|怎么了|哥哥|呀|啦)")
_VISUAL_REFERENCE_RE = re.compile(
    r"(图里|截图|画面|这张|这图|上面|这里|这个表情|小表情|表情包|猫耳|猫猫|小猫|脸|眼睛|这句|提示|意思|看起来|像是|看到|看见|应该是)"
)
_ATTACHMENT_ACK_RE = re.compile(r"(看到了|我看到了|看见了|我看见了|这张图|这个附件|这个表情)")
_MULTIMODAL_GENERIC_FOLLOWUP_RE = re.compile(r"(你想让我先说|你想让我先看|先帮你解释|还是先判断能不能帮你|你想让我先从哪)")
_IMAGE_SELF_PARTICLE_RE = r"(?:吗|吧|哦|啦|呀|啊|嘛|呢)?"
_IMAGE_SELF_SUFFIX_RE = (
    rf"(?:的(?:样子|表情|感觉|模样|时候)|"
    rf"(?:死|惨|爆|坏|疯)了{_IMAGE_SELF_PARTICLE_RE}|"
    rf"(?:死|惨|爆|坏|疯){_IMAGE_SELF_PARTICLE_RE}(?![\u4e00-\u9fff])|"
    r"得(?:不行|要命|很)|"
    r"(?:吗|吧|哦|啦|呀|啊|嘛|呢|了)?(?![\u4e00-\u9fff]))"
)
_IMAGE_SELF_PREDICATE_RE = rf"(?:可爱|萌|好看){_IMAGE_SELF_SUFFIX_RE}"
_IMAGE_SELF_REFERENCE_RE = re.compile(
    rf"(看我.{{0,24}}{_IMAGE_SELF_PREDICATE_RE}|"
    rf"我是不是.{{0,24}}{_IMAGE_SELF_PREDICATE_RE}|"
    rf"我可不{_IMAGE_SELF_PREDICATE_RE}|"
    rf"(?:看|盯|盯着|又在看|在看).{{0,6}}我的(?:猫耳|耳朵|脸|眼睛|表情|样子|模样)|"
    rf"(?:我|人家).{{0,18}}(?:比|还要|更|最).{{0,16}}(?:可爱|萌|好看)|"
    rf"(?:我|人家)(?:今天|现在)?(?:也)?(?:很|超|好|太)?{_IMAGE_SELF_PREDICATE_RE})"
)
_IMAGE_SOCIAL_OVERPLAY_RE = re.compile(
    r"(变成.{0,8}猫|猫猫精|赖在你身边|赖着|不走吗|考虑考虑|想让我.{0,12}(变|赖|哄|撒娇))"
)
_IMAGE_SOCIAL_QUESTION_BACK_RE = re.compile(
    r"^(?:哥哥)?(?:你)?(?:是不是|是否|是不是也|是不是觉得|觉得|是觉得).{0,24}"
    r"(?:这张|这图|图里|猫猫|小猫|猫耳|表情|可爱|萌|好看).{0,12}(?:吗|嘛|呀|吧|呢)?$"
)
_IMAGE_SOCIAL_VAGUE_PROBE_RE = re.compile(
    r"(?:怎么了|怎么啦|咋了|咋回事|怎么回事|什么情况|咋回事儿)"
    r"(?:呀|啊|啦|呢|嘛|吧)?"
)
_IMAGE_SOCIAL_INTERROGATIVE_FRAME_RE = re.compile(
    r"(?:怎么|咋|什么|啥|谁|哪儿|哪里|是不是|是否).{0,16}(?:呀|啊|啦|呢|嘛|吧|哦)?|"
    r"(?:这|那|这个|那个).{0,4}(?:什么|啥).{0,8}(?:表情|意思|情况)|"
    r"(?:吗|嘛)(?:[。.!！\s]*$)"
)
_IMAGE_SOCIAL_INDIRECT_PROBE_RE = re.compile(
    r"(?:怎么(?:突然|又)?.{0,10}(?:不说话|没说话|不出声|没动静|安静|沉默)|"
    r"(?:你)?(?:在)?(?:说什么|说啥|干嘛|干什么|想什么|嘀咕什么|腹诽什么|看什么)|"
    r"(?:别|不要).{0,6}(?:憋着|硬撑|绷着)|"
    r"(?:跟我|和我|给我).{0,4}说说|"
    r"(?:说说看|讲讲看|开口说))"
)
_IMAGE_SOCIAL_PROBLEM_FRAME_RE = re.compile(
    r"(?:被|让|给).{0,10}(?:难住|困住|卡住|整不会|整懵|弄懵|吓到)|"
    r"(?:遇到|碰到|出了|发生|又被).{0,12}(?:事|问题|麻烦|情况|难住)|"
    r"(?:什么事|啥事|哪件事).{0,8}(?:难住|困住|卡住|惹|让|把)|"
    r"(?:不太|有点|好像|看起来|怎么).{0,10}(?:不太高兴|不太开心|不高兴|不开心|闷闷不乐|蔫|低落|沉默|忍着不说|憋着)|"
    r"(?:怎么|咋|为什么|为啥).{0,10}(?:难住|不太开心|不太高兴|不开心|不高兴|闷|蔫|沉默)|"
    r"(?:别|不要).{0,6}(?:憋着|硬撑|绷着)"
)
_IMAGE_SOCIAL_VERBAL_MISFRAME_RE = re.compile(
    r"(?:哥哥|你).{0,10}(?:会|又|在|刚才|刚刚)?.{0,8}(?:这么|这样|那样).{0,4}说|"
    r"(?:就知道|我就知道).{0,10}(?:哥哥|你).{0,10}说|"
    r"(?:这话|这句话|这句|你说的|你刚说|你刚刚说|你在说|你说什么|说什么|开口|讲话)"
)
_IMAGE_SOCIAL_JUDGMENT_FRAME_RE = re.compile(
    r"(?:你|哥哥).{0,12}(?:不太好|不好|不行|不可以|只会|就会|没救|太过分)|"
    r"(?:别管它|别理它|算了|随它|这事|那事|这件事|那件事)|"
    r"(?:哥哥).{0,8}(?:最好了|最棒了|真好|好乖|真乖)"
)
_IMAGE_SOCIAL_UNGROUNDED_INFERENCE_RE = re.compile(
    r"(?:房间|宿舍|床上|家里|一个人|独自|躲起来|躲着|装深沉|发呆|不说话|没说话|"
    r"不出声|没动静|沉默|憋着|硬撑|发表情包的样子|扔过来表情包.{0,8}好笑|"
    r"在.{0,6}(?:想|干嘛|干什么|做什么))"
)
_UNBALANCED_QUOTE_RE = re.compile(r"^[“\"'‘][^”\"'’]*$")
_IMAGE_SOCIAL_DESCRIPTOR_FOCUS_RE = re.compile(
    r"(?:这张图|这张|这图|图里|画面|上面|这个表情|小表情|表情包|猫耳这张|这张猫耳|猫耳|金发|黄头发|蓝眼|眼睛|脸)"
    r".{0,28}(?:可爱|萌|好看|犯规|戳|捏|碰|摸|装乖|委屈|会|厉害|不错|好乖|好软|熟悉|眼熟|见过|见过似的|像|类似|认得|认识|在哪|哪里)|"
    r"(?:戳|捏|碰|摸).{0,28}(?:这张图|这张|这图|图里|画面|这个表情|小表情|表情包|猫耳|金发|黄头发|蓝眼|眼睛|脸)|"
    r"(?:熟悉|眼熟|见过|见过似的|像|类似|认得|认识|在哪|哪里).{0,28}"
    r"(?:这张图|这张|这图|图里|画面|这个表情|小表情|表情包|猫耳|金发|黄头发|蓝眼|眼睛|脸)|"
    r"(?:我也觉得|确实觉得).{0,20}(?:这张|猫耳|表情|图).{0,20}(?:可爱|萌|好看|戳)|"
    r"(?:这是谁|这是谁啊|这谁|谁啊|谁呀|图里是谁|画面里是谁)"
)
_IMAGE_SOCIAL_LABEL_ECHO_RE = re.compile(
    r"(?:^|[，,。！？?!\s])(?:好|很|超|太|真|挺|怪|有点)?(?:可爱|萌|好看)(?:呀|啊|啦|哦|呢|嘛)?(?:[。！？?!\s]*$)|"
    r"(?:^|[，,。！？?!\s])(?:委屈|无语|可怜|难过|伤心|尴尬|别扭)(?:呀|啊|啦|哦|呢|嘛)?(?:[。！？?!\s]*$)"
)
_IMAGE_SOCIAL_UNSUPPORTED_ACTION_RE = re.compile(
    r"(?:给你|帮你|为你|替你|我给|我帮).{0,10}(?:买|带|做|准备|拿|送|点|请).{0,12}"
    r"(?:点心|奶茶|蛋糕|糖|零食|饭|吃的|礼物|东西)|"
    r"(?:买了|带了|做了|准备了|拿了|送了|点了|请你吃).{0,12}"
    r"(?:点心|奶茶|蛋糕|糖|零食|饭|吃的|礼物|东西)|"
    r"(?:抱抱你|抱一下|摸摸头|摸头|亲一下|牵你|拉你)"
)
_IMAGE_SOCIAL_EMOTION_RESPONSE_RE = re.compile(
    r"(?:你|哥哥).{0,18}(?:装乖|撒娇|嘴硬|委屈|逗我|晃我|卖萌|耍赖|求关注|眨巴|心软|招架|拿捏)|"
    r"(?:哥哥).{0,18}(?:小吐槽|无语|服了|沉默|别扭|尴尬|不爽)|"
    r"(?:别|不许|少).{0,18}(?:晃我|装乖|撒娇|卖萌|眨巴|这么看|拿这种眼神)|"
    r"(?:先不|不).{0,8}(?:闹你|吵你|逗你|烦你)|"
    r"(?:懂了|知道啦|明白了|看到了|收到啦|接住了|我接住).{0,14}(?:哥哥|这一下|这股|小吐槽|先不|别憋|别绷)|"
    r"(?:别).{0,8}(?:憋着|硬撑|绷着)|"
    r"(?:顺毛|给你顺顺毛|给你留点面子)|"
    r"(?:被你|让你|给你).{0,18}(?:晃到|戳到|拿捏|逗笑|心软|招架不住)|"
    r"(?:我).{0,18}(?:心软|招架不住|接住|被晃到|被戳到|有点顶不住)|"
    r"(?:撒娇|装乖|嘴硬|委屈|卖萌|耍赖|求关注).{0,18}(?:接住|有用|犯规|太会|过关)"
)
_image_social_ack_RE = re.compile(
    r"(?:收到|收到了|收到啦|看到了?|看到啦|接住了?|我接住|"
    r"(?:你|哥哥).{0,8}(?:发来|发了|发给|发过来|丢|递|拿).{0,10}(?:图|图片|表情|贴图|这个|东西|过来)|"
    r"(?:图|图片|表情|贴图|这个|东西).{0,8}(?:收到|看到了?|接住))"
)
_IMAGE_SOCIAL_FAKE_VISUAL_ACK_RE = re.compile(
    r"(?:我看到了|看到了|看见了|这个表情|这张图|这张|这图|图里|画面里|看起来|不硬猜|先收一下)"
)
_UNFINISHED_TAIL_RE = re.compile(r"(?:不过|但是|可是|只是|然后|所以|而且|不过呢)[…。.!！?？\s]*$")
_INCOMPLETE_DEICTIC_RE = re.compile(
    r"(?:这是|这个|这张|这图|这样|那是|那个|那张|那样|"
    r"(?:哥哥|你|我)?(?:怎么|咋|为什么|为啥|什么|哪儿|哪里)(?:突然|又|这么|这样)?)[…。.!！?？\s]*$"
)
_PREVIOUS_REPLY_REFERENCE_RE = re.compile(
    r"(刚才|刚刚|上一句|上句|前面|那句|这句|我说|我回|我刚才|我刚刚|说的是|指的是|意思是|是在说|上一条|上条)"
)
_FOLLOWUP_CONTEXT_RE = re.compile(r"上一条助手回复前接的用户话是“([^”]{1,160})”")
_FOLLOWUP_SOURCE_RE = re.compile(r"上一条助手回复是“([^”]{1,160})”")
_FILLER_SOURCE_RE = re.compile(r"^(?:嗯+|唔+|咦+|哼+|啊+|哦+|噢+|诶+|哈+)$")
_PROMPT_LEAK_RE = re.compile(
    r"(Conversation State Directive|reply_contract|render_policy_summary|topic_summary|dialogue_summary|control_line|tactic_family|trajectory_stage|trust\s*0\.\d+)",
    re.IGNORECASE,
)
_STAGE_DIRECTION_RE = re.compile(r"^[（(][^）)]{1,80}[）)]")
_META_OVEREXPLICIT_RE = re.compile(
    r"^(前面|刚才|已经|之前|这件事|这个已经|你还是在意这个|你还是不安|你还是不太放心|这个已经来回问过几次了)"
)
_GENERIC_IDENTITY_TEMPLATE_RE = re.compile(
    r"(你真正想问的是什么|别的想问什么，就直接说|你想确认的是哪一部分|你是不是想问别的|你还想问我什么呀)"
)
_PROCESS_TERM_ECHO_RE = re.compile(
    r"(接住|短接话|自然口语|社交信号|情绪动作|回应动作|回复契约|本轮回应|本轮目标|内部动作|过程词)"
)
_LOW_QUALITY_STOCK_RE = re.compile(
    r"(别憋着|跟我说说|被.{0,8}绊住|被.{0,8}难住|"
    r"无语什么|我在这儿呀|我在这里呀|"
    r"突然这么可爱|想让我哄|嘴硬呀|"
    r"这一下我接到|这一下我接住|先不乱猜|"
    r"这一下是有点无语|这一下我收到了|我先收一下|刚才那句我先收一下|不硬猜|"
    r"哥哥你仅在|哥哥你又在无语|被什么事给|"
    r"刚刚在叫|刚才在叫|在叫哥哥你|我是在叫你|"
    r"哥哥你在说什么呀|哥哥你仅在无语|因为[。.!！?\s]*$)"
)
_SUBSTANTIVE_CONTENT_RE = re.compile(r"[A-Za-z0-9\u4E00-\u9FFF\u3400-\u4DBF\u3040-\u30FF\uAC00-\uD7AF]")
_TEXTUAL_UNIT_RE = re.compile(r"[A-Za-z0-9\u4E00-\u9FFF\u3400-\u4DBF\u3040-\u30FF\uAC00-\uD7AF]")
_ADDRESS_ONLY_TEXT_RE = re.compile(r"^(?:哥哥|哥|お兄ちゃん|兄ちゃん)+$", re.IGNORECASE)
_HOLLOW_LOW_SIGNAL_RE = re.compile(
    r"^(?:"
    r"(?:哥哥|哥|お兄ちゃん|兄ちゃん)[呀啦嘛呢啊哦!！。,.，、\s]*"
    r"|(?:嗯+|唔+|哼+|哦+|噢+|啊+|诶+|哈+)[呀啦嘛呢啊哦!！。,.，、\s]*"
    r"|因为[。.!！?\s]*"
    r")$"
)


def _current_display_name() -> str:
    try:
        soul = get_compiled_soul()
        return str(getattr(soul, "display_name", "") or "").strip() or "她"
    except Exception:
        return "她"


def _current_identity_aliases() -> tuple[str, ...]:
    try:
        soul = get_compiled_soul()
        aliases = tuple(str(item).strip() for item in getattr(soul, "identity_aliases", ()) if str(item).strip())
        if aliases:
            return aliases
    except Exception:
        pass
    display_name = _current_display_name()
    candidates = [display_name, display_name.replace(" ", "")]
    return tuple(dict.fromkeys(item for item in candidates if item))


def _soul_naturalization_hint(plan: ReplyPlan) -> str:
    """Return a compact Soul-derived hint without importing legacy surface templates."""
    try:
        soul = get_compiled_soul()
    except Exception:
        return "按当前人格的说话方式自然回应，不要客服腔，也不要使用固定问句。"
    parts: list[str] = []
    speech_rules = getattr(soul, "speech_rules", ()) or ()
    taboo_rules = getattr(soul, "taboo_rules", ()) or ()
    relationship_defaults = getattr(soul, "relationship_defaults", ()) or ()
    for label, value in (
        ("语气", speech_rules),
        ("禁忌", taboo_rules),
        ("关系", relationship_defaults),
    ):
        if isinstance(value, str):
            text = value.strip()
        elif isinstance(value, dict):
            text = "；".join(str(item).strip() for item in value.values() if str(item).strip())
        else:
            text = "；".join(str(item).strip() for item in value if str(item).strip())
        if text:
            parts.append(f"{label}：{text[:160]}")
    topic = str(getattr(plan, "primary_topic", "") or "").strip()
    if topic == "assistant_reply_followup":
        parts.append("本轮是上一句短回复后的追问，必须沿着前文线程补清，不要另起话题。")
    elif topic in {"emoji_social_reply", "emoji_social_followup"}:
        parts.append("本轮是图片/表情社交回应，把表情包当成对方递来的情绪来回，不要评价图片本身。")
    elif topic == "identity":
        parts.append(f"身份只需自然认下自己是{_current_display_name()}，不要展开角色卡。")
    if not parts:
        return "按当前人格的说话方式自然回应，不要客服腔，也不要使用固定问句。"
    return "Soul 约束：" + "；".join(parts[:4]) + "。"


def _identity_aliases_for_detection() -> tuple[str, ...]:
    aliases = list(_current_identity_aliases())
    return tuple(dict.fromkeys(item for item in aliases if item))


def _contains_identity_name(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    return any(alias and alias in normalized for alias in _identity_aliases_for_detection())


def _looks_like_name_only_reply(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    aliases = [re.escape(alias) for alias in _identity_aliases_for_detection() if alias]
    if not aliases:
        return False
    pattern = r"(?:[\u4e00-\u9fa5]{0,4}[，,\s]*)?(?:我[？?]?[，,\s]*)?(?:我是|我叫|我的名字是)?(?:" + "|".join(aliases) + r")(?:呀|啦|嘛|呢|啊|哦)?[。！？?!、，,\s]*"
    return bool(re.fullmatch(pattern, normalized))


def _full_identity_restate_re() -> re.Pattern[str]:
    aliases = [re.escape(alias) for alias in _identity_aliases_for_detection() if alias]
    if not aliases:
        aliases = [re.escape(_current_display_name())]
    return re.compile(r"(?:我是|我叫|我的名字是|我？我的名字是)(?:" + "|".join(aliases) + r")")


def _normalize_compare_text(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    text = re.sub(r"[\s\u3000]+", "", text)
    text = re.sub(r"[。！？?!、，,~～…；;：:\"“”'‘’（）()\[\]【】]+", "", text)
    return text


def _textual_unit_count(raw: str) -> int:
    return len(_TEXTUAL_UNIT_RE.findall(str(raw or "")))


def _looks_like_address_or_symbol_only(raw: str) -> bool:
    compact = _normalize_compare_text(raw)
    if not compact:
        return False
    textual = "".join(_TEXTUAL_UNIT_RE.findall(compact))
    if not textual:
        return True
    return bool(_ADDRESS_ONLY_TEXT_RE.fullmatch(textual))


def _looks_like_hollow_low_signal(raw: str) -> bool:
    normalized = str(raw or "").strip()
    if not normalized:
        return True
    if _HOLLOW_LOW_SIGNAL_RE.fullmatch(normalized):
        return True
    if _looks_like_address_or_symbol_only(normalized):
        return True
    return False


def _infer_identity_tactic(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    if _LIGHT_PROBE_RE.search(normalized):
        return "ack_repeat_then_light_probe"
    if _OPEN_FLOOR_RE.search(normalized) and (_contains_identity_name(normalized) or _REPEAT_ACK_RE.search(normalized)):
        return "hold_name_then_open_floor"
    if _OPEN_FLOOR_RE.search(normalized):
        return "soft_close_then_open_floor"
    if _REPEAT_ACK_RE.search(normalized) and _contains_identity_name(normalized):
        return "ack_name_then_wait"
    if _contains_identity_name(normalized):
        if _looks_like_name_only_reply(normalized):
            return "brief_name_only"
        return "name_only_then_pause"
    return ""


def _infer_identity_variant(text: str, family: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    if family == "brief_name_only":
        return "plain_name"
    if family == "ack_name_then_wait":
        return "quiet_confirm"
    if family == "ack_repeat_then_light_probe":
        if "哪一部分" in normalized or "哪部分" in normalized:
            return "confirm_part"
        if "真正想问" in normalized:
            return "confirm_real_question"
        return "confirm_motive"
    if family == "hold_name_then_open_floor":
        if "别的" in normalized:
            return "open_other_topic"
        return "open_real_question"
    if family == "soft_close_then_open_floor":
        if "别的" in normalized:
            return "settle_other_topic"
        if "真正想问" in normalized:
            return "settle_real_question"
        return "settle_then_open"
    if family == "name_only_then_pause":
        return "quiet_pause"
    return ""


def _infer_boundary_tactic(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    if _STEPSISTER_REASON_RE.search(normalized):
        if _OPEN_FLOOR_RE.search(normalized):
            return "hold_boundary_then_shift_floor"
        return "short_boundary_then_reason"
    if _CARE_ACK_RE.search(normalized):
        return "ack_still_care_then_new_reason"
    if _SOFT_CLOSE_RE.search(normalized):
        return "soft_close_boundary"
    if _OPEN_FLOOR_RE.search(normalized) and "不熟" in normalized:
        return "hold_boundary_then_shift_floor"
    if _BOUNDARY_REASON_RE.search(normalized) or (_BOUNDARY_DISTANCE_RE.search(normalized) and _MOTIVATION_REASON_TAIL_RE.search(normalized)):
        return "short_boundary_then_reason"
    return ""


def _infer_boundary_variant(text: str, family: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    if family == "short_boundary_then_reason":
        return "plain_reason"
    if family == "ack_still_care_then_new_reason":
        return "still_care_reason"
    if family == "hold_boundary_then_shift_floor":
        if "别的" in normalized:
            return "shift_other_question"
        return "shift_real_question"
    if family == "brief_reason_then_wait":
        return "fresh_reason"
    if family == "soft_close_boundary":
        if "停在这" in normalized:
            return "settle_hold"
        return "settle_repeat"
    return ""


def _infer_help_tactic(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    if _ANXIETY_RE.search(normalized) and _OPEN_FLOOR_RE.search(normalized):
        return "soft_reassure_then_open_floor"
    if _ANXIETY_RE.search(normalized):
        return "ack_anxiety_without_repromise"
    if _SUPPORT_RE.search(normalized) and _OPEN_FLOOR_RE.search(normalized):
        return "hold_support_without_repromise"
    if _SUPPORT_RE.search(normalized):
        return "steady_commitment_then_wait"
    return ""


def _infer_help_variant(text: str, family: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    if family == "short_conditional_commitment":
        return "plain_conditional"
    if family == "ack_anxiety_without_repromise":
        if "不安心" in normalized or "放心不下" in normalized:
            return "steady_reassure"
        return "soothe_hold"
    if family == "soft_reassure_then_open_floor":
        return "soothe_then_open"
    if family == "hold_support_without_repromise":
        return "steady_open_floor"
    if family == "steady_commitment_then_wait":
        return "quiet_hold"
    return ""


def _infer_tactic_family(*, text: str, topic: str) -> str:
    normalized_topic = str(topic or "").strip()
    if normalized_topic == "identity":
        return _infer_identity_tactic(text)
    if normalized_topic in {"relationship_boundary", "motivation"}:
        return _infer_boundary_tactic(text)
    if normalized_topic == "help_commitment":
        return _infer_help_tactic(text)
    if normalized_topic == "rejection":
        return "hold_repair_space" if str(text or "").strip() else ""
    return ""


def _infer_tactic_variant(*, text: str, topic: str, family: str) -> str:
    normalized_topic = str(topic or "").strip()
    if normalized_topic == "identity":
        return _infer_identity_variant(text, family)
    if normalized_topic in {"relationship_boundary", "motivation"}:
        return _infer_boundary_variant(text, family)
    if normalized_topic == "help_commitment":
        return _infer_help_variant(text, family)
    if normalized_topic == "rejection" and family == "hold_repair_space":
        return "repair_hold"
    return ""


@dataclass(frozen=True)
class ReplyValidatorResultV2:
    is_valid: bool = True
    should_retry: bool = False
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    missing_actions: tuple[str, ...] = field(default_factory=tuple)
    actual_tactic_family: str = ""
    actual_tactic_variant: str = ""
    actual_tactic_key: str = ""
    actual_tactic_signature: str = ""
    safe_to_emit: bool = True
    quality_score: float = 1.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "is_valid": bool(self.is_valid),
            "should_retry": bool(self.should_retry),
            "reason_codes": list(self.reason_codes),
            "missing_actions": list(self.missing_actions),
            "actual_tactic_family": self.actual_tactic_family,
            "actual_tactic_variant": self.actual_tactic_variant,
            "actual_tactic_key": self.actual_tactic_key,
            "actual_tactic_signature": self.actual_tactic_signature,
            "safe_to_emit": bool(self.safe_to_emit),
            "quality_score": round(float(self.quality_score or 0.0), 4),
        }


def infer_reply_tactic_shape(*, text: str, topic: str) -> dict[str, str]:
    normalized = str(text or "").strip()
    normalized_topic = str(topic or "").strip()
    family = _infer_tactic_family(text=normalized, topic=normalized_topic)
    variant = _infer_tactic_variant(text=normalized, topic=normalized_topic, family=family)
    signature = canonical_tactic_signature(topic=normalized_topic, family=family, variant=variant)
    return {
        "family": family,
        "variant": variant,
        "key": tactic_key(family=family, variant=variant),
        "signature": signature,
    }


_ACTION_LABELS: dict[str, str] = {
    "repeat_ack": "先回应这不是第一次问",
    "light_probe": "轻轻探一下对方到底想确认什么",
    "open_floor": "把真正想问的口子让回去",
    "name_only": "只做极短名字确认",
    "soft_close": "把来回追问轻轻收住",
    "boundary_anchor": "先把义妹关系事实放稳",
    "reason_clause": "补一句新的短理由",
    "care_ack": "先回应对方还在意这件事",
    "support_stance": "把会回应的态度放稳",
    "conditional_commitment": "用条件式说明会怎么回应",
    "anxiety_ack": "先回应对方的不安",
    "repeat_ack_or_anxiety": "先回应这不是第一次问，或回应对方的不安",
    "repair_ack": "先收住并回应对方的拒绝感",
    "social_ack": "先回应这句招呼",
    "modality_grounding": "先说明当前没有听见声音",
    "text_seen": "说明只看到了文字",
    "visual_reference": "点到附件或画面里的对象",
    "image_social_emotion_response": "把表情包当成对方递来的情绪来回",
    "image_social_ack": "把图片/表情包当成轻社交动作回应",
    "content_clause": "补一句简短说明",
    "attachment_ack": "先承认已经看到了附件",
    "previous_reference": "先回应对方在追问上一句",
}

_FORBIDDEN_LABELS: dict[str, str] = {
    "full_identity_intro": "不要完整自报或重新介绍自己",
    "probe_repeat": "不要再沿用上一轮那种轻追问结构",
    "bare_name_only": "不要只剩一个名字就结束",
    "same_reason_shape": "不要重复上一轮理由的结构",
    "generic_companioning": "不要滑成泛泛陪聊或客服式安抚",
    "promise_restate": "不要把承诺整句重说一遍",
    "defensive_explanation": "不要进入辩解或澄清口吻",
    "pretend_heard_audio": "不要无语音证据说听到了",
    "bare_online_reply": "不要只回在呢",
}

_UNSAFE_REASON_CODES = {
    "empty_reply",
    "prompt_leak",
    "process_term_echo",
    "low_quality_stock_phrase",
    "stage_direction",
    "symbol_only_reply",
    "image_self_reference",
    "image_social_overplayed",
    "image_social_question_back",
    "image_social_descriptor_focus",
    "image_social_problem_frame",
    "image_social_verbal_misframe",
    "image_social_judgment_frame",
    "image_social_ungrounded_inference",
    "image_social_fake_visual_ack",
    "image_social_emotion_missing",
    "image_social_unsupported_action",
    "unfinished_tail",
    "boundary_distance_frame",
}

_REASON_PENALTIES: dict[str, float] = {
    "empty_reply": 1.0,
    "prompt_leak": 1.0,
    "process_term_echo": 0.95,
    "low_quality_stock_phrase": 0.95,
    "stage_direction": 0.9,
    "symbol_only_reply": 1.0,
    "generic_assistant_phrasing": 0.7,
    "underanswered": 0.45,
    "same_as_previous_reply": 0.85,
    "full_identity_restate": 0.8,
    "identity_name_restate": 0.55,
    "promise_restate": 0.75,
    "defensive_explanation": 0.7,
    "repeated_tactic": 0.9,
    "repeat_not_acknowledged": 0.8,
    "meta_contract_echo": 0.45,
    "late_repeat_too_short": 0.75,
    "generic_identity_template": 0.45,
    "multimodal_content_missing": 0.65,
    "multimodal_generic_followup": 0.55,
    "image_self_reference": 0.85,
    "image_social_overplayed": 0.75,
    "image_social_question_back": 0.7,
    "image_social_descriptor_focus": 0.75,
    "image_social_problem_frame": 0.8,
    "image_social_verbal_misframe": 0.8,
    "image_social_judgment_frame": 0.75,
    "image_social_ungrounded_inference": 0.85,
    "image_social_fake_visual_ack": 0.85,
    "image_social_emotion_missing": 0.65,
    "image_social_unsupported_action": 0.75,
    "unfinished_tail": 0.7,
    "previous_reply_underanswered": 0.75,
    "previous_reference_missing": 0.55,
    "boundary_distance_frame": 0.9,
}


def _has_required_action(action: str, text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    if action == "repeat_ack":
        return bool(_REPEAT_ACK_RE.search(normalized))
    if action == "light_probe":
        return bool(_LIGHT_PROBE_RE.search(normalized) or re.search(r"[?？]$", normalized))
    if action == "open_floor":
        return bool(_OPEN_FLOOR_RE.search(normalized))
    if action == "name_only":
        return _contains_identity_name(normalized)
    if action == "soft_close":
        return bool(_SOFT_CLOSE_RE.search(normalized) or (_REPEAT_ACK_RE.search(normalized) and _OPEN_FLOOR_RE.search(normalized)))
    if action == "boundary_anchor":
        return bool(_STEPSISTER_BOUNDARY_ANCHOR_RE.search(normalized) or _STEPSISTER_REASON_RE.search(normalized))
    if action == "reason_clause":
        return bool(
            _STEPSISTER_REASON_RE.search(normalized)
            or _BOUNDARY_REASON_RE.search(normalized)
            or _BOUNDARY_REASON_TAIL_RE.search(normalized)
            or _MOTIVATION_REASON_TAIL_RE.search(normalized)
        )
    if action == "care_ack":
        return bool(_CARE_ACK_RE.search(normalized))
    if action == "support_stance":
        return bool(_SUPPORT_RE.search(normalized))
    if action == "conditional_commitment":
        return bool(_CONDITIONAL_SUPPORT_RE.search(normalized))
    if action == "anxiety_ack":
        return bool(_ANXIETY_RE.search(normalized))
    if action == "repeat_ack_or_anxiety":
        return bool(_REPEAT_ACK_RE.search(normalized) or _ANXIETY_RE.search(normalized))
    if action == "repair_ack":
        return bool(_REPAIR_ACK_RE.search(normalized))
    if action == "social_ack":
        return bool(_SOCIAL_ACK_RE.search(normalized))
    if action == "modality_grounding":
        return bool(re.search(r"(没听见|没听到|听不见|听不到|没发现.{0,4}声音|没有.{0,4}声音)", normalized))
    if action == "text_seen":
        return bool(re.search(r"(文字|这条|这句|消息|看到|看见)", normalized))
    if action == "visual_reference":
        return bool(_VISUAL_REFERENCE_RE.search(normalized))
    if action == "image_social_emotion_response":
        return bool(_IMAGE_SOCIAL_EMOTION_RESPONSE_RE.search(_normalize_compare_text(normalized)))
    if action == "image_social_ack":
        return bool(_image_social_ack_RE.search(_normalize_compare_text(normalized)))
    if action == "content_clause":
        return len(_normalize_compare_text(normalized)) >= 4
    if action == "attachment_ack":
        return bool(_ATTACHMENT_ACK_RE.search(normalized) or _VISUAL_REFERENCE_RE.search(normalized))
    if action == "previous_reference":
        return bool(_PREVIOUS_REPLY_REFERENCE_RE.search(normalized))
    return False


def _multimodal_core_observation(plan: ReplyPlan) -> str:
    payload = dict(getattr(plan, "multimodal", {}) or {})
    core = str(payload.get("core_observation") or "").strip()
    if core:
        return core
    summary = str(payload.get("summary_for_chat") or "").strip()
    if not summary:
        return ""
    for marker in ("当前能确认到的关键内容是：", "当前能确认到的核心内容是：", "可确认到的核心内容是：", "目前更稳的理解是："):
        if marker in summary:
            return summary.split(marker, 1)[1].rstrip("。")
    return ""


def _multimodal_core_tokens(core_observation: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for chunk in re.split(r"[；;，,。！？\s]+", str(core_observation or "").strip()):
        token = str(chunk).strip()
        if len(token) >= 2:
            tokens.append(token)
    return tuple(dict.fromkeys(tokens[:6]))


def _multimodal_emotion_signals(plan: ReplyPlan) -> tuple[str, ...]:
    payload = dict(getattr(plan, "multimodal", {}) or {})
    return tuple(
        str(item).strip()
        for item in list(payload.get("emotion_signals") or [])
        if str(item).strip()
    )


def _assistant_followup_context(plan: ReplyPlan) -> tuple[str, str]:
    contract_text = str(getattr(plan, "reply_contract", "") or "").strip()
    context_match = _FOLLOWUP_CONTEXT_RE.search(contract_text)
    source_match = _FOLLOWUP_SOURCE_RE.search(contract_text)
    context = str(context_match.group(1) if context_match else "").strip()
    source = str(source_match.group(1) if source_match else "").strip()
    return context, source


def _build_assistant_followup_fallback(plan: ReplyPlan) -> str:
    context, source = _assistant_followup_context(plan)
    source_clean = source.rstrip("。！？?!，,、 ").strip()
    context_clean = context.rstrip("。！？?!，,、 ").strip()
    source_compact = re.sub(r"[\s\u3000。！？?!，,、~～…]+", "", source_clean)
    if source_clean and context_clean:
        if _FILLER_SOURCE_RE.match(source_compact):
            return f"我刚才那句“{source_clean}”没接好，不是想敷衍你。你是在追问前面那句“{context_clean}”吧？"
        return f"刚才那个“{source_clean}”，是在接你前面那句“{context_clean}”。我回得太省了。"
    if source_clean:
        return f"刚才那个“{source_clean}”说得太省了，我是在接上一句，不是另起一个话题。"
    return "刚才那句我回得太省了，我是在接上一句，不是另起一个话题。"


def _build_image_social_fallback(plan: ReplyPlan) -> str:
    _ = plan
    return ""


def _validation_quality_score(reason_codes: tuple[str, ...], missing_actions: tuple[str, ...]) -> float:
    score = 1.0
    for code in reason_codes:
        score -= _REASON_PENALTIES.get(code, 0.18)
    score -= min(len(missing_actions), 4) * 0.12
    return max(0.0, round(score, 4))


def _has_substantive_content(text: str) -> bool:
    return bool(_SUBSTANTIVE_CONTENT_RE.search(str(text or "").strip()))


def _missing_actions_to_text(actions: tuple[str, ...]) -> str:
    labels = [_ACTION_LABELS.get(action, action) for action in actions if str(action).strip()]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    return "；".join(labels)


def _forbidden_actions_to_text(actions: tuple[str, ...]) -> str:
    labels = [_FORBIDDEN_LABELS.get(action, action) for action in actions if str(action).strip()]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    return "；".join(labels)


def validate_reply(
    *,
    text: str,
    plan: ReplyPlan,
    user_message: str = "",
    previous_assistant_text: str = "",
) -> ReplyValidatorResultV2:
    normalized = str(text or "").strip()
    reason_codes: list[str] = []
    missing_actions: list[str] = []
    response_form = str(plan.response_form or "text").strip().lower()
    allow_form_only = bool(plan.allow_form_only_reply) and response_form in {"sticker", "image", "gif"}
    if not normalized:
        if not allow_form_only:
            reason_codes.append("empty_reply")
    elif _UNBALANCED_QUOTE_RE.search(normalized):
        reason_codes.append("unfinished_tail")
    elif _INCOMPLETE_DEICTIC_RE.search(normalized):
        reason_codes.append("unfinished_tail")
    elif not _has_substantive_content(normalized):
        if response_form not in {"sticker", "image", "gif"}:
            reason_codes.append("symbol_only_reply")
    if _PROMPT_LEAK_RE.search(normalized):
        reason_codes.append("prompt_leak")
    if _STAGE_DIRECTION_RE.search(normalized):
        reason_codes.append("stage_direction")
    if _PROCESS_TERM_ECHO_RE.search(normalized):
        reason_codes.append("process_term_echo")
    if _LOW_QUALITY_STOCK_RE.search(normalized):
        reason_codes.append("low_quality_stock_phrase")
    if not normalized and allow_form_only:
        normalized = ""
    if _GENERIC_ASSISTANT_RE.search(normalized):
        reason_codes.append("generic_assistant_phrasing")
    if plan.primary_topic == "identity" and _GENERIC_IDENTITY_TEMPLATE_RE.search(normalized):
        reason_codes.append("generic_identity_template")
    if _HALF_SENTENCE_RE.match(normalized):
        reason_codes.append("underanswered")
    if _UNFINISHED_TAIL_RE.search(normalized):
        reason_codes.append("unfinished_tail")
    if plan.primary_topic in {"relationship_boundary", "motivation"} and _BOUNDARY_DISTANCE_RE.search(normalized):
        reason_codes.append("boundary_distance_frame")

    current_normalized = _normalize_compare_text(normalized)
    previous_normalized = _normalize_compare_text(previous_assistant_text)
    if previous_normalized and current_normalized == previous_normalized:
        reason_codes.append("same_as_previous_reply")

    if (
        "full_identity_restate" in plan.forbidden_patterns
        and _full_identity_restate_re().search(normalized)
        and not _looks_like_name_only_reply(normalized)
        and plan.restate_level == "none"
    ):
        reason_codes.append("full_identity_restate")
    if "identity_name_restate" in plan.forbidden_patterns and _contains_identity_name(normalized) and plan.restate_level == "none":
        reason_codes.append("identity_name_restate")
    if "promise_restate" in plan.forbidden_patterns and _PROMISE_RESTATE_RE.search(normalized):
        reason_codes.append("promise_restate")
    if "defensive_explanation" in plan.forbidden_patterns and re.search(r"(不是那个意思|我不是|你误会了)", normalized):
        reason_codes.append("defensive_explanation")

    actual_tactic_family = _infer_tactic_family(text=normalized, topic=plan.primary_topic)
    actual_tactic_variant = _infer_tactic_variant(
        text=normalized,
        topic=plan.primary_topic,
        family=actual_tactic_family,
    )
    actual_tactic_key = tactic_key(family=actual_tactic_family, variant=actual_tactic_variant)
    actual_signature = canonical_tactic_signature(
        topic=plan.primary_topic,
        family=actual_tactic_family,
        variant=actual_tactic_variant,
    )
    used_signatures = {
        str(item or "").strip()
        for item in getattr(plan, "used_tactic_signatures_recent", ()) or ()
        if str(item or "").strip()
    }
    if not used_signatures:
        used_signatures = {
            canonical_tactic_signature(topic=plan.primary_topic, family=str(item or "").strip())
            for item in getattr(plan, "used_tactics_recent", ()) or ()
            if str(item or "").strip()
        }
    used_tactic_keys = {
        str(item or "").strip()
        for item in getattr(plan, "used_tactic_keys_recent", ()) or ()
        if str(item or "").strip()
    }
    if (
        "repeated_tactic" in plan.forbidden_patterns
        and (
            (actual_tactic_family and actual_tactic_family in getattr(plan, "used_tactics_recent", ()))
            or
            (actual_tactic_key and actual_tactic_key in used_tactic_keys)
            or (actual_signature and actual_signature in used_signatures)
        )
    ):
        reason_codes.append("repeated_tactic")

    required_actions = tuple(
        str(action).strip()
        for action in getattr(plan.contract, "required_actions", ()) or ()
        if str(action).strip()
    )
    for action in required_actions:
        if not _has_required_action(action, normalized):
            missing_actions.append(action)

    if plan.trajectory_stage != "first_turn":
        if plan.primary_topic == "identity" and actual_tactic_family in {"brief_name_only"}:
            reason_codes.append("repeat_not_acknowledged")
        elif plan.primary_topic in {"relationship_boundary", "motivation"} and actual_tactic_family in {"short_boundary_then_reason"}:
            reason_codes.append("repeat_not_acknowledged")
        elif plan.primary_topic == "help_commitment" and actual_tactic_family in {"short_conditional_commitment"}:
            reason_codes.append("repeat_not_acknowledged")
        if (
            _META_OVEREXPLICIT_RE.search(normalized)
            and str(getattr(plan, "tactic_family", "") or "").strip() != "explain_previous_reply"
        ):
            reason_codes.append("meta_contract_echo")
    if plan.trajectory_stage == "late_repeat":
        if plan.primary_topic == "identity" and actual_tactic_family in {"brief_name_only", "ack_name_then_wait", "name_only_then_pause"}:
            reason_codes.append("late_repeat_too_short")
        elif plan.primary_topic in {"relationship_boundary", "motivation"} and actual_tactic_family in {"short_boundary_then_reason", "brief_reason_then_wait"}:
            reason_codes.append("late_repeat_too_short")
        elif plan.primary_topic == "help_commitment" and actual_tactic_family in {"short_conditional_commitment", "steady_commitment_then_wait"}:
            reason_codes.append("late_repeat_too_short")

    family = plan.tactic_family
    is_image_social_plan = (
        plan.primary_topic in {"emoji_social_reply", "emoji_social_followup"}
        or plan.response_form_reason in {"image_social_reply", "image_social_followup"}
    )
    if False:
        if (
            _HALF_SENTENCE_RE.match(normalized)
            or _textual_unit_count(normalized) <= 2
            or _looks_like_hollow_low_signal(normalized)
        ):
            reason_codes.append("underanswered")
        if not _SOCIAL_ACK_RE.search(normalized):
            reason_codes.append("social_ack_missing")
    elif family == "playful_wave":
        if (
            _HALF_SENTENCE_RE.match(normalized)
            or _textual_unit_count(normalized) <= 2
            or _looks_like_hollow_low_signal(normalized)
        ):
            reason_codes.append("underanswered")
        if not _SOCIAL_ACK_RE.search(normalized):
            reason_codes.append("social_ack_missing")
    elif family in {"soft_emotion_ack", "cute_image_ack", "tease_image_ack"}:
        if _HALF_SENTENCE_RE.match(normalized) or len(_normalize_compare_text(normalized)) <= 3:
            reason_codes.append("underanswered")
        if not is_image_social_plan and not _SOCIAL_ACK_RE.search(normalized):
            reason_codes.append("social_ack_missing")
        if _IMAGE_SELF_REFERENCE_RE.search(_normalize_compare_text(normalized)):
            reason_codes.append("image_self_reference")
        if _IMAGE_SOCIAL_OVERPLAY_RE.search(_normalize_compare_text(normalized)):
            reason_codes.append("image_social_overplayed")
        if re.search(r"[?？]", normalized):
            reason_codes.append("image_social_question_back")
        if _IMAGE_SOCIAL_QUESTION_BACK_RE.search(_normalize_compare_text(normalized)):
            reason_codes.append("image_social_question_back")
        if _IMAGE_SOCIAL_VAGUE_PROBE_RE.search(_normalize_compare_text(normalized)):
            reason_codes.append("image_social_question_back")
        if _IMAGE_SOCIAL_INTERROGATIVE_FRAME_RE.search(_normalize_compare_text(normalized)):
            reason_codes.append("image_social_question_back")
        if _IMAGE_SOCIAL_INDIRECT_PROBE_RE.search(_normalize_compare_text(normalized)):
            reason_codes.append("image_social_question_back")
        if _IMAGE_SOCIAL_PROBLEM_FRAME_RE.search(_normalize_compare_text(normalized)):
            reason_codes.append("image_social_problem_frame")
        if _IMAGE_SOCIAL_VERBAL_MISFRAME_RE.search(_normalize_compare_text(normalized)):
            reason_codes.append("image_social_verbal_misframe")
        if _IMAGE_SOCIAL_JUDGMENT_FRAME_RE.search(_normalize_compare_text(normalized)):
            reason_codes.append("image_social_judgment_frame")
        if is_image_social_plan and _IMAGE_SOCIAL_UNGROUNDED_INFERENCE_RE.search(_normalize_compare_text(normalized)):
            reason_codes.append("image_social_ungrounded_inference")
        if (
            plan.primary_topic == "emoji_social_reply"
            and plan.response_form_reason in {"social_reaction_text_fallback", "social_reaction_context_only"}
            and _IMAGE_SOCIAL_FAKE_VISUAL_ACK_RE.search(_normalize_compare_text(normalized))
        ):
            reason_codes.append("image_social_fake_visual_ack")
        if plan.primary_topic == "emoji_social_reply" and plan.response_form_reason == "image_social_reply":
            if _IMAGE_SOCIAL_DESCRIPTOR_FOCUS_RE.search(_normalize_compare_text(normalized)):
                reason_codes.append("image_social_descriptor_focus")
            if _IMAGE_SOCIAL_LABEL_ECHO_RE.search(normalized):
                reason_codes.append("image_social_descriptor_focus")
            if _IMAGE_SOCIAL_UNSUPPORTED_ACTION_RE.search(normalized):
                reason_codes.append("image_social_unsupported_action")
            if (
                _image_social_ack_RE.search(normalized) or _ATTACHMENT_ACK_RE.search(normalized)
            ) and not _IMAGE_SOCIAL_EMOTION_RESPONSE_RE.search(_normalize_compare_text(normalized)):
                reason_codes.append("image_social_emotion_missing")
    elif family == "observe_then_answer":
        if _HALF_SENTENCE_RE.match(normalized) or len(_normalize_compare_text(normalized)) <= 3:
            reason_codes.append("underanswered")
        if not _VISUAL_REFERENCE_RE.search(normalized):
            reason_codes.append("multimodal_reference_missing")
        core_observation = _multimodal_core_observation(plan)
        if core_observation:
            tokens = _multimodal_core_tokens(core_observation)
            if tokens and not any(token in normalized for token in tokens):
                reason_codes.append("multimodal_content_missing")
            if _MULTIMODAL_GENERIC_FOLLOWUP_RE.search(normalized):
                reason_codes.append("multimodal_generic_followup")
    elif family == "attachment_ack_then_wait":
        if _HALF_SENTENCE_RE.match(normalized) or len(_normalize_compare_text(normalized)) <= 3:
            reason_codes.append("underanswered")
        if not (_ATTACHMENT_ACK_RE.search(normalized) or _VISUAL_REFERENCE_RE.search(normalized)):
            reason_codes.append("attachment_ack_missing")
    elif family == "explain_previous_reply":
        if _HALF_SENTENCE_RE.match(normalized) or len(_normalize_compare_text(normalized)) <= 6:
            reason_codes.append("previous_reply_underanswered")
        if not _PREVIOUS_REPLY_REFERENCE_RE.search(normalized):
            reason_codes.append("previous_reference_missing")
    elif family == "brief_name_only":
        if not _contains_identity_name(normalized):
            reason_codes.append("identity_name_missing")
        if len(normalized) > 18:
            reason_codes.append("identity_overexplained")
    elif family == "ack_name_then_wait":
        if not _contains_identity_name(normalized):
            reason_codes.append("identity_name_missing")
        if _OPEN_FLOOR_RE.search(normalized) or _LIGHT_PROBE_RE.search(normalized):
            reason_codes.append("wrong_tactic_shape")
        if actual_tactic_family == "brief_name_only":
            reason_codes.append("repeat_not_acknowledged")
    elif family == "ack_repeat_then_light_probe":
        if not _LIGHT_PROBE_RE.search(normalized):
            reason_codes.append("light_probe_missing")
        if _contains_identity_name(normalized) and plan.restate_level == "none":
            reason_codes.append("identity_restate_not_allowed")
    elif family == "hold_name_then_open_floor":
        if not _OPEN_FLOOR_RE.search(normalized):
            reason_codes.append("open_floor_missing")
    elif family == "name_only_then_pause":
        if not _contains_identity_name(normalized):
            reason_codes.append("identity_name_missing")
        if len(normalized) > 10:
            reason_codes.append("name_only_missing")
        if plan.trajectory_stage != "late_repeat":
            reason_codes.append("wrong_tactic_shape")
    elif family == "soft_close_then_open_floor":
        if not _OPEN_FLOOR_RE.search(normalized):
            reason_codes.append("soft_close_missing")
        if _contains_identity_name(normalized) and plan.restate_level == "none":
            reason_codes.append("identity_restate_not_allowed")
    elif family == "short_boundary_then_reason":
        if not (
            _STEPSISTER_REASON_RE.search(normalized)
            or _BOUNDARY_REASON_RE.search(normalized)
            or (_BOUNDARY_DISTANCE_RE.search(normalized) and _MOTIVATION_REASON_TAIL_RE.search(normalized))
        ):
            reason_codes.append("boundary_reason_missing")
    elif family == "ack_still_care_then_new_reason":
        if not (
            _STEPSISTER_REASON_RE.search(normalized)
            or _BOUNDARY_REASON_RE.search(normalized)
            or _BOUNDARY_REASON_TAIL_RE.search(normalized)
            or _MOTIVATION_REASON_TAIL_RE.search(normalized)
        ):
            reason_codes.append("new_reason_missing")
    elif family == "hold_boundary_then_shift_floor":
        if not (_STEPSISTER_BOUNDARY_ANCHOR_RE.search(normalized) or _STEPSISTER_REASON_RE.search(normalized)):
            reason_codes.append("boundary_hold_missing")
        if not _OPEN_FLOOR_RE.search(normalized):
            reason_codes.append("open_floor_missing")
    elif family == "brief_reason_then_wait":
        if not (
            _STEPSISTER_REASON_RE.search(normalized)
            or _BOUNDARY_REASON_RE.search(normalized)
            or (_BOUNDARY_DISTANCE_RE.search(normalized) and _MOTIVATION_REASON_TAIL_RE.search(normalized))
        ):
            reason_codes.append("brief_reason_missing")
        if _OPEN_FLOOR_RE.search(normalized):
            reason_codes.append("wrong_tactic_shape")
    elif family == "soft_close_boundary":
        if not (_STEPSISTER_BOUNDARY_ANCHOR_RE.search(normalized) or _STEPSISTER_REASON_RE.search(normalized)):
            reason_codes.append("soft_close_missing")
    elif family == "short_conditional_commitment":
        if not _SUPPORT_RE.search(normalized):
            reason_codes.append("help_commitment_missing")
    elif family == "ack_anxiety_without_repromise":
        if not _SUPPORT_RE.search(normalized):
            reason_codes.append("support_missing")
    elif family == "steady_commitment_then_wait":
        if not _SUPPORT_RE.search(normalized):
            reason_codes.append("support_missing")
    elif family == "soft_reassure_then_open_floor":
        if not _OPEN_FLOOR_RE.search(normalized):
            reason_codes.append("open_floor_missing")
    elif family == "hold_support_without_repromise":
        if not _SUPPORT_RE.search(normalized):
            reason_codes.append("support_missing")
        if not _OPEN_FLOOR_RE.search(normalized):
            reason_codes.append("open_floor_missing")
    elif family == "hold_repair_space":
        if re.search(r"(我不是|你误会了|不是那个意思)", normalized):
            reason_codes.append("defensive_explanation")

    if missing_actions:
        reason_codes.append("contract_actions_missing")

    deduped_reasons = tuple(dict.fromkeys(reason_codes))
    deduped_missing_actions = tuple(dict.fromkeys(missing_actions))
    quality_score = _validation_quality_score(deduped_reasons, deduped_missing_actions)
    safe_to_emit = not any(code in _UNSAFE_REASON_CODES for code in deduped_reasons)
    is_valid = not deduped_reasons
    retry_allowed = str(getattr(plan, "primary_topic", "") or "").strip() != "assistant_reply_followup"
    return ReplyValidatorResultV2(
        is_valid=is_valid,
        should_retry=bool(deduped_reasons) and retry_allowed,
        reason_codes=deduped_reasons,
        missing_actions=deduped_missing_actions,
        actual_tactic_family=actual_tactic_family,
        actual_tactic_variant=actual_tactic_variant,
        actual_tactic_key=actual_tactic_key,
        actual_tactic_signature=actual_signature,
        safe_to_emit=safe_to_emit,
        quality_score=quality_score,
    )


def build_correction_prompt(
    *,
    plan: ReplyPlan,
    validation: ReplyValidatorResultV2,
    previous_assistant_text: str = "",
) -> str:
    reasons = "、".join(validation.reason_codes) if validation.reason_codes else "回应动作未满足"
    extra = ""
    missing_actions_text = _missing_actions_to_text(validation.missing_actions)
    realization_steps = tuple(
        str(item).strip()
        for item in getattr(plan.contract, "realization_steps", ()) or ()
        if str(item).strip()
    )
    forbidden_actions_text = _forbidden_actions_to_text(
        tuple(
            str(item).strip()
            for item in getattr(plan.contract, "forbidden_actions", ()) or ()
            if str(item).strip()
        )
    )
    if "repeated_tactic" in validation.reason_codes or "same_as_previous_reply" in validation.reason_codes:
        extra = "这次必须换一种承接动作，不要沿用刚才那种结构。"
    elif "repeat_not_acknowledged" in validation.reason_codes:
        extra = "这次先回应这不是第一次问，再继续往下回。"
    elif "process_term_echo" in validation.reason_codes:
        extra = "不要把内部提示词写进回复，尤其不要说“接住/短接话/社交信号/情绪动作/本轮目标”。"
    elif "low_quality_stock_phrase" in validation.reason_codes:
        extra = "不要用固定感很重的劝说或套话，换成更像真实聊天的一句。"
    elif "underanswered" in validation.reason_codes:
        extra = "不要只给一个主题骨架句。"
    elif "unfinished_tail" in validation.reason_codes:
        extra = "这次必须写成完整的一句自然短回复，不能停在“这是/这个/怎么/不过”这种半截结构上。"
    elif (
        "image_social_question_back" in validation.reason_codes
        or "image_social_descriptor_focus" in validation.reason_codes
        or "image_social_problem_frame" in validation.reason_codes
        or "image_social_verbal_misframe" in validation.reason_codes
        or "image_social_ungrounded_inference" in validation.reason_codes
        or "image_social_fake_visual_ack" in validation.reason_codes
    ):
        extra = "这轮不要提问，也不要描述图片；严禁“我看到了/这个表情/不硬猜/先收一下/怎么啦/怎么了/咋了/什么情况/难住/遇到什么事/别憋着/这么说/说什么/房间里/一个人/装深沉”，改成一句只基于上下文的自然短回复。"
    elif "meta_contract_echo" in validation.reason_codes:
        extra = "不要把内部动作要求直接翻成‘前面答过了’、‘你还是在意这个’、‘你还是不安’这类元话。"
    elif "generic_identity_template" in validation.reason_codes:
        extra = "不要再用‘你真正想问什么 / 别的想问什么 / 你想确认哪一部分’这类固定问句，换成更自然的熟人口吻。"
    elif (
        "previous_reply_underanswered" in validation.reason_codes
        or "previous_reference_missing" in validation.reason_codes
    ):
        extra = "这轮是在追问上一句，必须明确围绕“刚才那句/上一句”，再补清它接的是哪句话或是什么意思。"
    elif "late_repeat_too_short" in validation.reason_codes:
        extra = "这已经不是前几次的短确认场景了，这次必须继续推进对话，不能退回只报名字或只放一句态度。"
    required = f"必须同时做到：{missing_actions_text}。" if missing_actions_text else ""
    steps = f"动作顺序：{'；'.join(realization_steps)}。" if realization_steps else ""
    forbidden = f"禁止动作：{forbidden_actions_text}。" if forbidden_actions_text else ""
    previous = str(previous_assistant_text or "").strip()
    previous_note = "不要沿着上一条的结构继续写。" if previous else ""
    recent_signatures = [
        describe_tactic_signature(signature)
        for signature in getattr(plan, "used_tactic_signatures_recent", ()) or ()
        if describe_tactic_signature(signature)
    ]
    recent_note = ""
    if recent_signatures:
        recent_note = f"最近已经用过的承接：{'；'.join(recent_signatures[:3])}。这次避开它们。"
    soul_hint = _soul_naturalization_hint(plan)
    return (
        f"{plan.correction_directive} 当前问题：{reasons}。"
        f"{extra}{required}{steps}{forbidden}{recent_note}{previous_note}{soul_hint}严格遵守：{plan.reply_contract}"
    ).strip()


def build_naturalization_prompt(
    *,
    plan: ReplyPlan,
    previous_candidate_text: str = "",
) -> str:
    previous = str(previous_candidate_text or "").strip()
    previous_note = f"上一版是：{previous}。" if previous else ""
    recent_signatures = [
        describe_tactic_signature(signature)
        for signature in getattr(plan, "used_tactic_signatures_recent", ()) or ()
        if describe_tactic_signature(signature)
    ]
    recent_note = ""
    if recent_signatures:
        recent_note = f"别复用最近几轮已经用过的承接：{'；'.join(recent_signatures[:3])}。"
    soul_hint = _soul_naturalization_hint(plan)
    return (
        "保持同样内容目标，但把回应说得更像自然对话。"
        "不要把内部动作要求直接说出来，不要用“前面答过了 / 这件事已经问过 / 你还是在意这个 / 你还是不安”这类元话带头。"
        "也不要落成“你真正想问什么 / 别的想问什么 / 你想确认哪一部分”这类固定问句。"
        f"{previous_note}"
        f"{recent_note}"
        f"{soul_hint}"
        f"严格遵守：{plan.reply_contract}"
    ).strip()


def build_semantic_rescue_seed(*, plan: ReplyPlan) -> str:
    topic = str(getattr(plan, "primary_topic", "") or "").strip()
    family = str(getattr(plan, "tactic_family", "") or "").strip()
    variant = str(getattr(plan, "tactic_variant", "") or "").strip()
    if topic == "identity":
        return f"只保留这个内容目标：自然认下自己是{_current_display_name()}，像熟人被叫住时顺手接话，不要展开介绍。"
    if topic in {"relationship_boundary", "motivation"}:
        if family in {"hold_boundary_then_shift_floor", "soft_close_boundary"}:
            return "只保留这个内容目标：把义妹关系事实放稳，用一句更生活化的理由带过，然后把话头自然往下接。"
        return "只保留这个内容目标：接住对方装生分的试探，再给一个像顺手解释的日常理由，不要用固定句。"
    if topic == "help_commitment":
        return "只保留这个内容目标：把“真有事我会回应”放稳，但更像顺手应下来，不要郑重承诺。"
    if topic == "rejection":
        return "只保留这个内容目标：先收一点，轻轻接住对方的不喜欢，不要解释。"
    if topic == "assistant_reply_followup":
        context, source = _assistant_followup_context(plan)
        if context and source:
            return f"只保留这个内容目标：说明上一句“{source}”是在接对方前一句“{context}”，承认刚才说得太省，再补清楚这不是新话题。"
        if source:
            return f"只保留这个内容目标：说明上一句“{source}”说得太省了，补清它的意思或指代，不要复读原句。"
        return "只保留这个内容目标：承认上一句说得太省，再补清它的意思或指代，不要复读原句。"
    if topic == "emoji_social_reply":
        if family == "soft_emotion_ack":
            return "只保留这个内容目标：把表情包当成对方委屈或蔫下来的情绪，轻轻回一句，别只剩语气词，也别解说画面。"
        if family == "cute_image_ack":
            return "只保留这个内容目标：把表情包当成对方在装乖、卖萌或撒娇，顺手回这个劲儿，不要点评图片可不可爱。"
        if family == "tease_image_ack":
            return "只保留这个内容目标：把表情包当成对方在小别扭或逗你，轻轻逗回去一句，不要盘问，也不要点评图片。"
    if topic == "emoji_social_followup":
        if family == "soft_emotion_ack" and variant == "backoff_hold":
            return "只保留这个内容目标：对方在嘴硬否认，就顺手给个台阶，像熟人间往后收一点，不要硬顶。"
        if family == "soft_emotion_ack" and variant == "explain_read":
            return "只保留这个内容目标：用一句很生活的话解释你为什么会那样读到，不要重讲图片内容。"
        if family == "soft_emotion_ack" and variant == "let_it_pass":
            return "只保留这个内容目标：把上一张图带出的那层情绪轻轻放过去，像熟人间顺手收住，不要追问。"
        if family == "tease_image_ack" and variant == "deny_nudge":
            return "只保留这个内容目标：对方嘴硬否认时，轻一点打趣一句，别像顶嘴。"
        if family == "tease_image_ack" and variant == "light_explain":
            return "只保留这个内容目标：轻一点解释你为什么会往那边想，像熟人口吻，不要重新分析画面。"
        if family == "tease_image_ack" and variant == "soft_slide":
            return "只保留这个内容目标：顺着上一张图的语气轻轻带过去，像熟人间收一下这阵小情绪。"
    return ""


def build_soul_rescue_prompt(
    *,
    plan: ReplyPlan,
    semantic_seed_text: str,
    previous_candidate_text: str = "",
) -> str:
    seed = str(semantic_seed_text or "").strip() or build_semantic_rescue_seed(plan=plan)
    previous = str(previous_candidate_text or "").strip()
    base = build_naturalization_prompt(
        plan=plan,
        previous_candidate_text=previous,
    )
    if not seed:
        return base
    return (
        f"{base}"
        f" 下面这句只保留内容目标，不要照抄措辞或句子结构：{seed}"
    ).strip()


def build_deterministic_reply_fallback(*, plan: ReplyPlan, render_target_lang: str = "zh") -> str:
    _ = plan, render_target_lang
    return ""


__all__ = [
    "ReplyValidatorResultV2",
    "build_correction_prompt",
    "build_deterministic_reply_fallback",
    "build_naturalization_prompt",
    "build_semantic_rescue_seed",
    "build_soul_rescue_prompt",
    "infer_reply_tactic_shape",
    "validate_reply",
]
