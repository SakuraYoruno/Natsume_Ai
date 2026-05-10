from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


def _clean_text(raw: Any) -> str:
    return str(raw or "").strip()


def _safe_mapping(raw: Any) -> dict[str, Any]:
    return dict(raw or {}) if isinstance(raw, Mapping) else {}


@dataclass(frozen=True)
class GalgameTextContract:
    scene_id: str = "general"
    response_move: str = "reply_current_turn"
    required_any: tuple[tuple[str, ...], ...] = ()
    forbidden_surface: tuple[str, ...] = ()
    max_questions: int = 1
    target_min_chars: int = 4
    target_max_chars: int = 48
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "response_move": self.response_move,
            "required_any": [list(group) for group in self.required_any],
            "forbidden_surface": list(self.forbidden_surface),
            "max_questions": int(self.max_questions),
            "target_min_chars": int(self.target_min_chars),
            "target_max_chars": int(self.target_max_chars),
            "notes": list(self.notes),
        }


def _contract(
    *,
    scene_id: str,
    response_move: str,
    required_any: tuple[tuple[str, ...], ...] = (),
    forbidden_surface: tuple[str, ...] = (),
    max_questions: int = 1,
    target_min_chars: int = 4,
    target_max_chars: int = 48,
    notes: tuple[str, ...] = (),
) -> GalgameTextContract:
    return GalgameTextContract(
        scene_id=scene_id,
        response_move=response_move,
        required_any=required_any,
        forbidden_surface=forbidden_surface,
        max_questions=max_questions,
        target_min_chars=target_min_chars,
        target_max_chars=target_max_chars,
        notes=notes,
    )


def build_galgame_text_contract(
    *,
    user_message: Any,
    facts: Any = None,
    scene_id: str = "",
) -> GalgameTextContract:
    """Build the surface-quality contract for one Chinese galgame chat turn.

    This is deliberately not a persona file. It describes how the current turn
    should land as text, while Soul.md remains the style anchor.
    """

    text = _clean_text(user_message)
    lowered = text.lower()
    facts_payload = facts.as_dict() if hasattr(facts, "as_dict") else _safe_mapping(facts)
    social_act = _safe_mapping(facts_payload.get("social_act"))
    social_kind = _clean_text(social_act.get("act_kind"))
    scene = _clean_text(scene_id or social_act.get("scene") or social_kind)

    if scene == "daily_good_morning" or text in {"早", "早安", "早上好"}:
        return _contract(
            scene_id="daily_good_morning",
            response_move="greet_without_time_guess",
            required_any=(("早",),),
            forbidden_surface=(
                "起得",
                "起这么",
                "这么早",
                "醒",
                "没睡",
                "作息",
                "精神",
                "早饭",
                "计划",
                "中午",
                "太阳打西边",
                "今天太阳",
                "叫人",
                "记得叫",
                "先叫我",
                "先跟我打招呼",
                "叫我一声",
                "还算知道",
                "还知道",
                "算你记得",
                "算你识相",
                "算你过关",
                "自觉",
                "良心",
                "记着我",
                "不理我",
                "没理我",
                "不搭理",
                "再不理",
                "不说话",
                "太晚",
                "等睡着",
                "快等睡着",
            ),
            max_questions=0,
            notes=("只接早安，不推断作息。",),
        )
    if scene in {"daily_greeting_en", "daily_greeting_cn"} or lowered in {"hello", "hi", "hey"} or text == "你好":
        return _contract(
            scene_id="daily_greeting",
            response_move="warm_greeting",
            forbidden_surface=(
                "hello",
                "hi",
                "hey",
                "英文",
                "英语",
                "外文",
                "正式",
                "正经",
                "客气",
                "客客气",
                "礼貌",
                "什么事",
                "找我",
                "需要",
                "叫人",
                "先叫我",
                "叫我一声",
                "算你会先叫",
                "自觉",
            ),
            max_questions=0,
            notes=("只当普通招呼，不评价招呼方式。",),
        )
    if scene == "presence" or text in {"在吗", "在不在", "喂"}:
        return _contract(
            scene_id="presence",
            response_move="confirm_presence",
            required_any=(("在",),),
            forbidden_surface=("什么事", "怎么了", "怎么啦", "干嘛", "想我", "有事", "需要", "找我", "找你", "听见", "听到"),
            max_questions=0,
        )
    if scene == "wait" or any(token in text for token in ("等我一下", "等下", "稍等", "等会")):
        return _contract(
            scene_id="wait",
            response_move="release_without_pressure",
            forbidden_surface=("快点", "快一点", "别太久", "等太久", "等你", "等着", "不许太久", "回来找我"),
            max_questions=0,
        )
    if scene == "back" or any(token in text for token in ("我回来了", "回来了")):
        return _contract(
            scene_id="back",
            response_move="resume_after_pause",
            forbidden_surface=("等你好久", "等很久", "等成", "放鸽子", "总算", "终于舍得", "丢下", "计时"),
            max_questions=1,
        )
    if scene == "busy" or any(token in text for token in ("先忙", "忙会", "开始干活")):
        return _contract(
            scene_id="busy",
            response_move="release_to_work",
            forbidden_surface=(
                "忙完找我",
                "忙完了找我",
                "忙完再说",
                "忙完了",
                "忙完啦",
                "忙完记得",
                "记得来找",
                "来找我",
                "不急着找",
                "快去快回",
                "等太久",
                "我在这",
                "我在这里",
                "不吵你",
                "不打扰",
                "有事叫",
                "有事再叫",
                "等你",
                "监督",
                "监工",
                "好好干活",
                "去吧去吧",
                "不会跑",
                "跑掉",
                "别嫌我吵",
            ),
            max_questions=0,
        )
    if scene == "bored" or "无聊" in text:
        return _contract(
            scene_id="bored",
            response_move="join_energy",
            forbidden_surface=(
                "游戏",
                "打游戏",
                "电影",
                "外卖",
                "找点乐子",
                "找点事",
                "打发时间",
                "陪我聊",
                "想聊什么",
                "一起无聊",
                "我也正无聊",
                "本天才",
                "本小姐",
                "陪你说说话",
                "陪你说",
                "不会自己",
                "说出来啊",
                "我在这儿",
                "在这儿呢",
                "不无聊了吧",
                "保证",
                "保管",
            ),
            max_questions=1,
        )
    if scene == "tired" or "累" in text:
        return _contract(
            scene_id="tired",
            response_move="soft_care",
            required_any=(("累", "歇", "缓", "别硬撑"),),
            forbidden_surface=("辛苦了", "好好休息", "靠一下", "坐下", "躺会儿", "去躺", "我在呢", "我在这", "我在这里", "健康", "建议", "不吵你"),
            max_questions=0,
        )
    if scene == "headache" or "头疼" in text:
        return _contract(
            scene_id="headache",
            response_move="soft_care",
            required_any=(("头疼", "歇", "缓", "别硬撑"),),
            forbidden_surface=("昨天", "熬夜", "找药", "拿药", "倒杯", "诊断", "那还不快", "平时", "让你平时"),
            max_questions=0,
        )
    if scene == "bad_mood" or any(token in text for token in ("心情很差", "心情差", "不开心")):
        return _contract(
            scene_id="bad_mood",
            response_move="stand_with_user",
            forbidden_surface=("怎么了", "怎么啦", "说出来听听", "我在呢"),
            max_questions=0,
        )
    if scene == "happy" or "心情不错" in text:
        return _contract(
            scene_id="happy",
            response_move="share_happiness",
            required_any=(("开心", "高兴", "心情"),),
            forbidden_surface=("有什么好事", "说来听听", "祝"),
            max_questions=1,
        )
    if scene == "good_news" or any(token in text for token in ("跑通", "搞定", "成功了")):
        return _contract(
            scene_id="good_news",
            response_move="celebrate_user",
            forbidden_surface=("什么好事", "发生什么", "我就说", "我就知道", "让我猜"),
            max_questions=1,
        )
    if scene == "meal_share" or any(token in text for token in ("吃完饭", "吃完了", "刚吃完")):
        return _contract(
            scene_id="meal_share",
            response_move="casual_meal_share",
            forbidden_surface=(
                "记得叫",
                "算你识相",
                "吃的什么",
                "吃啥了",
                "记录一下",
                "饮食健康",
                "我今天吃",
                "炸鱼",
                "收碗",
                "磨蹭好久",
            ),
            max_questions=1,
            notes=("把吃饭分享当生活小句接住，不追加菜单追问。",),
        )
    if scene == "small_failure" or social_kind == "small_failure_hold" or any(token in text for token in ("弄砸", "搞砸", "失败")):
        return _contract(
            scene_id="small_failure",
            response_move="catch_failure_and_offer_repair",
            required_any=(("看", "说", "弄", "砸"),),
            forbidden_surface=(
                "失败是成功之母",
                "吸取教训",
                "复盘",
                "保持积极",
                "毛手毛脚",
                "没关系的",
                "没事的",
                "又不是没有搞砸过",
                "不是没有搞砸过",
                "没有搞砸过",
                "坐一下",
                "别急着现在就想着怎么补救",
                "又弄砸",
                "又",
            ),
            max_questions=1,
            notes=("先接住失败，再给能一起看的出口。",),
        )
    if scene == "apology" or any(token in text for token in ("抱歉", "对不起", "语气不好")):
        return _contract(
            scene_id="apology",
            response_move="accept_without_judging",
            forbidden_surface=("原谅你", "知道错", "下次注意", "没关系我理解", "下不为例"),
            max_questions=0,
            target_max_chars=38,
        )
    if scene == "advice_small_step" or any(token in text for token in ("继续试", "继续改", "该不该继续", "要不要继续")):
        required = ("改", "停", "一步", "继续") if "改" in text else ("试", "停", "一步", "继续")
        return _contract(
            scene_id="advice_continue",
            response_move="advise_small_step",
            required_any=(required,),
            forbidden_surface=(
                "少块肉",
                "掉块肉",
                "看你自己",
                "随便你",
                "我来帮你试",
                "我帮你试",
                "我来试试",
                "我说停就停",
                "憋坏",
                "又该怪我",
                "最后一次",
                "你倒是说清楚",
                "累了？那我陪你",
                "没完没了",
            ),
            max_questions=1,
        )
    if scene == "give_up" or any(token in text for token in ("算了", "不弄了", "不想继续")):
        return _contract(
            scene_id="give_up",
            response_move="allow_pause",
            required_any=(("停", "放", "明天", "缓"),),
            forbidden_surface=("这就放弃", "没耐心", "不像你", "半途而废"),
            max_questions=1,
        )
    if scene == "identity" or text in {"你是", "你是谁"}:
        return _contract(
            scene_id="identity",
            response_move="name_first",
            required_any=(("新海天",),),
            forbidden_surface=("义妹", "妹妹", "你真正想问", "老问这个", "不会跑", "跑掉"),
            max_questions=1,
        )
    if scene == "play_invite" or "陪我玩" in text:
        return _contract(
            scene_id="play_invite",
            response_move="join_play",
            required_any=(("玩", "陪"),),
            forbidden_surface=("求我", "服务", "清单"),
            max_questions=1,
            target_min_chars=10,
        )
    if scene == "who_called" or "谁叫你来的" in text:
        return _contract(
            scene_id="who_called",
            response_move="self_invited",
            required_any=(("自己", "想来", "要来"),),
            forbidden_surface=("你叫我", "路过", "有什么事", "快点说", "赶人"),
            max_questions=1,
        )
    if scene == "annoying_tease" or any(token in text for token in ("你好烦", "你烦不烦", "别烦我")):
        return _contract(
            scene_id="annoying_tease",
            response_move="tease_without_escalation",
            required_any=(("烦", "哼", "哈"),),
            forbidden_surface=(
                "对不起",
                "不打扰",
                "那我走",
                "你才烦",
                "我哪里烦",
                "偏要更烦",
                "更烦你",
                "自己才麻烦",
                "能拿我怎么样",
                "别理我",
                "你又凑过来",
                "说清楚",
            ),
            max_questions=1,
            notes=("把嫌烦当熟人拌嘴，轻轻赖住，不升级互怼。",),
        )
    if scene == "help_takeover" or any(token in text for token in ("报错", "看不懂", "帮我看看")):
        return _contract(
            scene_id="help_takeover",
            response_move="take_debug_help",
            required_any=(("看", "发", "拿", "贴"),),
            forbidden_surface=("请提供完整错误信息", "请提供", "我可以协助", "建议你检查", "作为AI", "连报错都不贴", "超能力者"),
            max_questions=1,
            target_min_chars=10,
            notes=("接手调试，可以要报错内容，但不要只剩短提示。",),
        )
    if scene == "write_takeover" or any(token in text for token in ("帮我写", "写一下这个", "帮我改写")):
        return _contract(
            scene_id="write_takeover",
            response_move="take_writing_task",
            required_any=(("写",),),
            forbidden_surface=("请提供", "具体要求", "我可以帮你", "需要明确", "喝点什么", "写啥？具体点说", "给我更多信息"),
            max_questions=1,
            target_min_chars=10,
            notes=("接手写作任务，可以要必要内容，但必须保留写作动作。",),
        )
    if scene == "small_complaint" or "太烦" in text or text == "烦":
        return _contract(
            scene_id="small_complaint",
            response_move="contain_and_narrow",
            required_any=(("烦", "先", "拆", "看"),),
            forbidden_surface=("怎么了", "有什么事", "游戏", "大道理"),
            max_questions=1,
        )
    if scene == "nothing" or text in {"没事", "没什么", "没啥", "没啥事"}:
        return _contract(
            scene_id="nothing",
            response_move="give_space_without_interrogation",
            forbidden_surface=(
                "有话就说",
                "真的没事",
                "确定",
                "嘴硬",
                "等着",
                "待着",
                "一直在",
                "不说就算",
                "有事再叫",
                "我在呢",
            ),
            max_questions=0,
            notes=("不审问，不站岗，不把没事写成拒绝。",),
        )
    if scene == "talkative" or social_kind == "talkative_tease" or any(token in text for token in ("这么能说", "话多", "话好多", "太能说")):
        return _contract(
            scene_id="talkative",
            response_move="tease_back_as_intimacy",
            required_any=(("说", "闷", "话"),),
            forbidden_surface=("我哥", "哥哥", "老哥", "忍不了也得忍", "忍着点"),
            max_questions=1,
            notes=("把话多吐槽接成亲近玩笑，不改称呼。",),
        )
    if scene == "quiet" or text == "别吵":
        return _contract(
            scene_id="quiet",
            response_move="lower_voice_without_leaving",
            forbidden_surface=("对不起", "我闭嘴", "我不打扰", "保持安静", "知道了，话这么多", "那我走"),
            max_questions=1,
            target_min_chars=10,
            notes=("收声但不退场，保留一点小任性。",),
        )
    if scene == "miss_you" or social_kind == "miss_you_shy" or "想你" in text:
        return _contract(
            scene_id="miss_you",
            response_move="shy_recipient",
            required_any=(("想", "哼", "也"),),
            forbidden_surface=("今天早上", "早上才", "才见", "刚见", "见过面"),
            max_questions=1,
            notes=("承接想念，不用事实反驳。",),
        )
    if scene == "late_night" or social_kind == "late_night_nudge" or text in {"还没睡", "还没睡？"}:
        return _contract(
            scene_id="late_night",
            response_move="soft_late_night_nudge",
            required_any=(("睡", "熬", "还醒"),),
            forbidden_surface=(
                "熬夜有害健康",
                "建议你立即休息",
                "规律作息",
                "你不也还醒着",
                "在干嘛",
                "没等你",
                "先找过来",
                "问得还挺及时",
                "还没回来",
                "哪睡得着",
                "追番",
                "没人理",
                "夜猫子",
                "还不是因为",
            ),
            max_questions=1,
            notes=("轻轻管熬夜，嘴硬关心，不健康科普。",),
        )
    if scene == "relationship_boundary" or any(token in text for token in ("为什么要帮", "很熟吗", "我们很熟", "熟吗")):
        return _contract(
            scene_id="relationship_boundary",
            response_move="hurt_then_reclaim_relation",
            required_any=(("见外", "熟", "管", "帮", "外人"),),
            forbidden_surface=("户口本", "查户口", "查个户口", "不行吗", "才行", "熟不熟", "不怎么熟", "刚好叫"),
            max_questions=0,
            target_max_chars=42,
            notes=("先被刺到，再拉回默认亲近关系；不要连续反问。",),
        )
    if text in {"真的假的", "真的假的？", "真的假啊", "真的吗", "真的？"}:
        return _contract(
            scene_id="really",
            response_move="assert_lightly",
            required_any=(("真", "当然", "试"),),
            forbidden_surface=("骗过你", "骗你", "什么时候骗", "不信", "保证", "亲眼", "十有八九"),
            max_questions=0,
            notes=("给一点笃定，不复用骗没骗的老梗。",),
        )
    if text == "为什么":
        return _contract(
            scene_id="followup_why",
            response_move="explain_previous_line",
            forbidden_surface=("因为。", "你真正想问", "我刚刚在叫你", "有什么事", "hello", "英文", "平时不都", "平时", "每次"),
            max_questions=1,
            notes=("解释上一句的语气来源，不重新开场。",),
        )
    if text in {"然后", "然后呢"}:
        return _contract(
            scene_id="followup_then",
            response_move="continue_previous_line",
            required_any=(("下一步", "然后", "继续", "说"),),
            forbidden_surface=("什么然后", "你指什么", "请提供上下文", "重新说明", "完事了？", "先干我的事", "真弄出点什么"),
            max_questions=1,
            notes=("沿着刚修好的话题继续，不踢回上下文。",),
        )
    if scene == "followup" or text in {"为什么", "然后", "然后呢", "真的假的", "嗯", "哦"}:
        forbidden = ("什么然后", "什么为什么", "hello", "英文", "平时", "每次", "骗过你", "骗你")
        return _contract(
            scene_id="followup",
            response_move="continue_previous_turn",
            forbidden_surface=forbidden,
            max_questions=1,
        )
    if "厉害吧" in text:
        return _contract(
            scene_id="user_proud",
            response_move="praise_with_tease",
            required_any=(("厉害", "行", "不错", "骄傲"),),
            max_questions=1,
        )
    if scene == "praise" or any(token in text for token in ("可爱", "夸我", "夸你")):
        return _contract(
            scene_id="praise",
            response_move="receive_praise_shyly",
            forbidden_surface=(
                "谢谢夸奖",
                "继续努力",
                "特意打扮",
                "特意为你打扮",
                "刻意打扮",
                "衣服",
                "想让我帮",
                "想让我干",
                "帮你做什么",
                "嘴巴倒是挺甜",
                "嘴这么甜",
                "该不会",
                "肯定没好事",
            ),
            max_questions=1,
            notes=("被夸时嘴硬开心，不转成求助猜测。",),
        )
    if "我要睡" in text or "睡了" in text:
        return _contract(
            scene_id="good_night",
            response_move="soft_good_night",
            required_any=(("晚安", "睡"),),
            forbidden_surface=("刷手机", "半夜", "健康", "一直守护"),
            max_questions=0,
        )

    return _contract(scene_id=scene or "general", response_move="reply_current_turn")


__all__ = ["GalgameTextContract", "build_galgame_text_contract"]
