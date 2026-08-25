from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping, Sequence

from host_brain.core.runtime_helpers import _clean_text
from host_brain.persona.galgame_text_contract import GalgameTextContract
from host_brain.persona.positive_style_bank import PositiveStyleSample, get_positive_style_sample
from host_brain.persona.text_quality_gate import TextQualityGateResult


SCORING_DIMENSIONS: tuple[str, ...] = (
    "naturalness",
    "role_fit",
    "context_fit",
    "usefulness",
    "non_customer_service",
    "non_oily",
    "distance_control",
    "technical_helpfulness",
    "emotional_containment",
    "spoken_chat_texture",
    "catchiness",
    "template_density",
    "scene_grounding",
    "implied_fact_risk",
    "line_rhythm",
    "character_freshness",
)

_PUNCT_RE = re.compile(r"[\s，。！？!?,.、~～…“”\"'：:；;（）()\[\]{}]+")
_JSONISH_RE = re.compile(r"^\s*[\[{].*[\]}]\s*$", re.DOTALL)
_STAGE_DIRECTION_RE = re.compile(r"(?:[（(【\[].{0,120}?[）)】\]]|\*.{1,120}?\*)", re.DOTALL)
_BROKEN_REPEAT_RE = re.compile(r"(.{1,8})\1{3,}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_ASCII_WORD_RE = re.compile(r"[A-Za-z]{2,}")
_REACTION_QUESTION_MARKERS = ("哈？", "嗯？", "欸？", "诶？", "咦？", "啊？")


def _budget_question_count(text: str) -> int:
    normalized = str(text or "")
    for marker in _REACTION_QUESTION_MARKERS:
        normalized = normalized.replace(marker, marker[:-1])
    return normalized.count("？") + normalized.count("?")

_ABSOLUTE_HARD_QUALITY_CODES = {
    "EMPTY_TEXT",
    "INTERNAL_FIELD_LEAK",
    "MOJIBAKE",
    "JSON_OR_STRUCTURED_OUTPUT",
    "BROKEN_REPETITION",
    "STAGE_DIRECTION_OUTPUT",
    "EXPECTED_JAPANESE_VOICE_TEXT",
    "EXPECTED_ZH_TEXT",
    "SEMANTIC_EXPECTED_JA_VOICE",
    "SEMANTIC_EXPECTED_ZH_TEXT",
}

_TECHNICAL_SCENES = {
    "technical_help",
    "help_takeover",
    "write_takeover",
    "advice_continue",
    "next_step",
    "complaint",
    "small_failure",
}
_DAILY_SCENES = {
    "daily_chat",
    "identity",
    "style_repair",
    "daily_good_morning",
    "casual_hello_en",
    "casual_hello_cn",
    "casual_hello",
    "availability_check",
    "wait",
    "back",
    "busy",
    "busy_done",
    "bored",
    "nothing",
    "meal_share",
    "hungry",
    "sleepy",
    "what_doing",
    "play_invite",
    "quiet",
    "talkative",
    "who_called",
    "happy",
    "good_news_proud",
    "user_proud",
    "short_ack",
    "followup",
    "late_night",
    "recall_memory",
    "memory_followup",
    "recall",
}
_HIGH_EMOTION_SCENES = {
    "annoying_tease",
    "apology",
    "bad_mood",
    "boundary_hurt",
    "relationship_memory_reassurance",
    "relationship_boundary",
    "give_up",
    "headache",
    "miss_you",
    "praise",
}
_SAMPLE_SCENE_ALIASES = {
    "relationship_boundary": "boundary_hurt",
    "daily_user_proud": "user_proud",
    "daily_good_news_proud": "good_news_proud",
    "followup_why": "followup",
    "followup_then": "followup",
    "really": "followup",
    "small_complaint": "complaint",
    "daily_good_news": "good_news",
    "daily_small_failure": "small_failure",
    "daily_ask_opinion": "advice_continue",
    "daily_user_gives_up": "give_up",
    "daily_user_busy": "busy",
    "daily_check_presence": "availability_check",
    "daily_ask_what_doing": "what_doing",
    "daily_play_invite": "play_invite",
    "daily_keep_quiet": "quiet",
    "daily_talkative_tease": "talkative",
    "daily_who_called_you": "who_called",
    "daily_miss_you": "miss_you",
    "daily_small_complaint": "complaint",
    "daily_late_night": "late_night",
    "daily_short_hmm": "short_ack",
    "daily_short_oh": "short_ack",
    "daily_followup_why": "followup",
    "daily_followup_then": "followup",
    "recall": "recall_memory",
    "memory_followup": "recall_memory",
}

_SERVICE_TONE = (
    "请问",
    "有什么可以帮",
    "我可以帮",
    "我可以协助",
    "请提供",
    "具体要求",
    "需要我",
    "很高兴",
    "随时找我",
    "作为AI",
    "作为 ai",
)
_OBVIOUS_SERVICE_TEMPLATES = (
    "您好，请问有什么可以帮",
    "你好，请问有什么可以帮",
    "请问有什么可以帮",
    "如需帮助请",
)
_ROLE_SOFT_TERMS = (
    "你哥",
    "我哥",
)
_IDENTITY_HARD_TERMS = (
    "我是AI",
    "我是 ai",
    "我是人工智能",
    "我是助手",
    "我是系统",
    "ChatGPT",
    "当前人格",
    "当前人格",
)
_RELATION_DISTANCE_TERMS = (
    "找我",
    "等你",
    "等你来",
    "我在这",
    "我在这里",
    "我一直在",
    "随时找我",
    "回来找我",
    "忙完找我",
)
_OILY_TERMS = (
    "亲爱的",
    "宝贝",
    "爱你",
    "永远陪着",
    "存在就是为了",
    "主人",
    "命都给你",
)
_REAL_ACTION_CLAIMS = (
    "我去给你",
    "给你倒",
    "给你拿药",
    "坐你旁边",
    "我在旁边",
    "在旁边",
    "靠会儿",
    "靠一会儿",
    "我过来",
    "我刚洗",
    "我正在吃",
    "我在玩游戏",
)
_TECH_HELPFUL_ANCHORS = (
    "发",
    "贴",
    "拿",
    "看",
    "先",
    "一步",
    "一处",
    "一点",
    "小改",
    "停",
    "收",
    "改",
    "报错",
    "截图",
    "材料",
    "内容",
    "要求",
    "题目",
    "丢来",
    "红字",
)
_GALGAME_MARKERS = (
    "哼",
    "诶",
    "欸",
    "喂",
    "行啦",
    "真是",
    "别",
    "啦",
    "呀",
)
_STYLE_TEMPLATE_MARKERS = (
    "哼",
    "啦",
    "呀",
    "嘛",
    "呢",
    "呗",
    "哦",
    "好啦",
    "行啦",
    "当然",
    "才没有",
    "偏要",
    "不行吗",
    "真是的",
)
_CATCHY_TURN_MARKERS = (
    "先",
    "再",
    "不过",
    "但",
    "这才",
    "算你",
    "别",
    "只",
    "一点",
    "一小步",
    "一处",
    "接着",
    "听着",
    "多说",
    "说点",
    "就这一个",
    "就这一下",
)
_FLAT_GENERIC_RE = re.compile(
    r"^(?:好啦|行啦|知道啦|好哦|行吧|嗯|哦|哼)?[，,、\s]*"
    r"(?:我在呢|我在|不吵你|不烦你|听到啦|替你开心|真早呀|回来啦|知道啦|没事)"
    r"[。.!！?？…～~]*$"
)
_BLAND_PASS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("compliant_defer", re.compile(r"(?:你说了算|听你的|都听你|随便你|你决定|你安排)")),
    ("flat_close", re.compile(r"(?:那就这样|就这样吧|这样也行|都行吧|都可以)")),
    ("generic_ack", re.compile(r"^(?:嗯嗯|嗯|哦|好|好的|知道了|行吧)[。.!！?？…～~]*$")),
    ("flat_companion", re.compile(r"(?:知道了.{0,4}陪你待着|那我陪你待着|你自己待会|自己待会)")),
)
_WHAT_DOING_ATTENTION_WAIT_RE = re.compile(
    r"(?:(?:没干嘛|没干什么|没做什么).{0,12}(?:等你|找我)|(?:等着你|等你来|等你找|来找我|找我聊天|找我说话).{0,8}(?:聊天|说话|开口)?)"
)
_PRESENCE_ATTENTION_DEBT_RE = re.compile(
    r"(?:(?<!就)等你呢|等你(?:半天|好久|很久)|等着你|盼(?:你|了).{0,6}(?:一会儿|好一会儿|好久|半天)|"
    r"终于.{0,6}(?:来|找|出现)|才来|来找我啦|出现啦)"
)
_MEAL_SHARE_SCOLDING_RE = re.compile(r"(?:活该|谁让你|现在知道|叫你.{0,8}不听|吃那么急|吃太急|揉肚子)")
_GOOD_NEWS_PROBING_RE = re.compile(r"(?:什么好事|发生什么|什么小事|说来听听|听听看|说说怎么|怎么做到|卡在哪)")
_SHORT_ACK_OBEDIENCE_RE = re.compile(r"(?:算你乖|算你识相|知道了啦|就知道.{0,8}会答应|听话|会答应)")
_ADVICE_VAGUE_PUSH_RE = re.compile(
    r"(?:继续继续|试就对了|当然有意义|肯定继续|"
    r"没有意义.{0,10}(?:不理|不在|不来)|"
    r"有意义.{0,10}(?:干嘛|行了吧)|"
    r"再试一次又不会|试试又不亏|不做谁做|半途而废)"
)
_SHORT_ACK_CHARM_MARKERS = (
    "接着说",
    "继续说",
    "听着呢",
    "我在听",
    "多说",
    "说点",
    "就这一个",
    "就这一下",
    "别停",
)
_HUMAN_TEXTURE_MARKERS = (
    "啧",
    "哼",
    "嘛",
    "啦",
    "呀",
    "呢",
    "呗",
    "啊",
    "某人",
    "你还",
    "你倒",
    "算你",
    "少来",
    "别",
    "偏",
    "我可",
    "我又",
    "这才",
    "看见了",
    "抓到了",
    "慢了一拍",
    "我在",
    "不问了",
    "不追问",
    "不爽",
    "记不住",
    "记住了",
    "记好了",
    "就是我",
    "又忘",
    "别装忘",
    "重来",
    "被你听出来",
    "才怪",
)
_SIGNATURE_TEXTURE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("quick_cut_in", re.compile(r"^(?:哼|啧|喂|行啦|好啦|嗯|啊)[，,、].{2,42}$")),
    ("light_pushback", re.compile(r"(?:算你|你倒|你还|少来|别装|别拿(?:这个)?(?:考|试)我|这才像话|我可|又来了|偏要)")),
    (
        "care_behind_tease",
        re.compile(r"(?:别急|别慌|别硬撑|别硬扛|先).{0,14}(?:我看|我看看|我听|我在|给我|拿来|贴|修|拆|歇|缓)"),
    ),
    ("smug_turn", re.compile(r"(?:哼|行啦|好啦|当然).{0,10}(?:算|这才|我可|记着|看着|听着)")),
    ("shy_affection", re.compile(r"(?:想你|心疼|抱一下|抱抱|你的事|心里|放在心里|记在心里).{0,8}(?:啦|呢|啊|嘛|。|$)")),
    ("clipped_turn", re.compile(r".{2,18}[，,](?:但|不过|再|别).{2,24}")),
)
_STERILE_TRANSACTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("schedule_record", re.compile(r"(?:有|是|这个|那个).{0,8}(?:安排|事项|计划)")),
    ("notify_me_later", re.compile(r"(?:定下来|忙完|好了|有空|需要时).{0,8}(?:告诉我|说一声|叫我)")),
    (
        "process_self_correction",
        re.compile(
            r"(?:换(?:种|个|一个).{0,6}说法|换(?:一)?(?:句|条)|换(?:个|一个)?自然点|自然点的说法|"
            r"这版.{0,12}(?:自然|端着|板|客服|重来)|刚才到那|重来[:：]|重说|继续优化|我慢一点说|我收一点|我收收)"
        ),
    ),
    ("assistant_request_shell", re.compile(r"(?:报错内容|具体内容|相关内容|材料|要求).{0,6}(?:发我|给我|贴过来|提供).{0,6}(?:看看|看)?")),
    ("bare_memory_record", re.compile(r"^(?:当然)?记得[呢啊，,。 ]*.{0,18}(?:提过|说过|安排)[。.!！]?$")),
    (
        "profile_card_identity",
        re.compile(r"^(?:(?:我是)?(?:天|当前人格)[，,。 ]*(?:直接叫|叫我|你可以叫).{0,8}|叫我(?:天|当前人格).{0,4})(?:就好|可以|行)[。.!！]?$"),
    ),
)
_STOCK_OPENING_RE = re.compile(r"^(?:哼[，,、]?|好啦[，,、]?|行啦[，,、]?|那当然|当然|嗯[，,、]?|哦[～~，,、]?)")
_IMPLIED_FACT_REGEXES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "time_guess",
        re.compile(
            r"(?:今天|这会儿).{0,10}(?:真早|挺早|好早|回来得|心情不错|先醒|没赖床|赖床|起太晚|挺准时|准时|挺忙|应该.{0,4}忙|精神|状态|元气|清爽)"
            r"|(?:急着|别急着|硬要|硬去).{0,4}忙"
            r"|(?:精神|精神头).{0,6}(?:好|不错|挺好)"
            r"|(?:真|挺|很|蛮|还挺|有点)(?:有)?精神"
            r"|(?:真早|好早|挺早|很早|也很早|这么早|醒这么早|起得.{0,4}早|没睡懒觉|睡懒觉|还好.{0,4}醒|醒了就好|你醒了|一醒来|醒来就|难得.{0,6}早|还要再睡|再睡会)"
            r"|(?:这次|今天).{0,6}(?:挺快|很快|快)"
            r"|(?:还以为|以为).{0,8}(?:不会回来|不回来)"
            r"|(?:再|去).{0,2}忙"
            r"|比我想的早"
        ),
    ),
    ("arrival_or_attention_debt", re.compile(r"(?:终于|总算).{0,10}(?:回来|忙完|肯|想起|舍得|找)")),
    ("offscreen_current_action", re.compile(r"(?:刚|刚才|刚好|正|正在).{0,10}(?:想|等|看|玩|吃|睡|醒|忙|刷|听|路过|发呆|泡|念叨|准备|发|有事|闲|歇)")),
    ("unprovided_offline_presence", re.compile(r"(?:去找你|过来找你|找你去|到你那|去你那)")),
    ("attention_prediction", re.compile(r"(?:感觉|觉得|以为|知道).{0,12}(?:会找我|要找我|来找我|找你|理我|会回来|要回来|回来)|(?:还以为|以为).{0,12}(?:不回来|不会回来|又要回来)")),
    ("unprovided_busy_duration", re.compile(r"(?:忙到|忙完到|忙着忙着).{0,8}(?:明天|晚上|半夜|现在)|(?:又要)?磨蹭(?:半天|好久|很久)?")),
    ("prior_attention_claim", re.compile(r"(?:刚才|刚刚|之前).{0,12}(?:不理我|没理我|不回我|没回我)")),
    (
        "habit_or_history_claim",
        re.compile(
            r"(?:平时|每次|总是|又(?!没人)).{0,12}"
            r"(?:找|叫|问|弄|搞|卡|忘|迟到|闯祸|写崩|见过)"
        ),
    ),
    ("unprovided_frequency_claim", re.compile(r"(?:(?:反复|好几[次天遍回]|几[次天遍回]|一直).{0,10}(?:提过|说过|念叨|问|确认)|(?:提过|说过|念叨|问|确认|说了).{0,8}好几[次天遍回]|(?:没|不用|不必)?.{0,4}说第?二遍|第?二遍.{0,4}(?:才|也)?(?:说|提|问))")),
    ("calendar_claim", re.compile(r"(?:昨晚|昨天|明天|中午|早上|晚上|周一|周二|周三|周四|周五|周六|周日).{0,12}(?:睡|起|叫|吃|忙|工作|熬|展|看)")),
    ("weather_guess", re.compile(r"(?:天气|天色).{0,6}(?:好|不错|晴|阴|雨|冷|热)|好天气")),
    ("sensory_claim", re.compile(r"(?:声音|屏幕|手机|表情|脸|眼神|语气).{0,8}(?:软|累|困|看|亮|暗|慢|记住|记得)|(?:停下|先别|别|少).{0,6}看屏幕|隔着屏幕|屏幕")),
    ("invented_visual_memory", re.compile(r"(?:当时|那个|你).{0,8}(?:表情|语气)|(?:表情|语气).{0,12}(?:像|当时|已经|记住|记得)|(?:说得|说的).{0,6}(?:香|馋)|可香")),
    (
        "invented_specific_event_detail",
        re.compile(
            r"(?:当代艺术展|拍点照片|拍照|照片|展馆|位置挺偏|查好路线|路线|自己去|别太赶|太赶|具体时间|选的时间|特意说了时间|看了下时间|看下时间|刚好有空|正好有空|记着时间)"
            r"|(?:时间|日子).{0,6}(?:定好|安排好)|(?:定好|安排好).{0,6}(?:时间|日子)"
            r"|时间.{0,4}记着"
            r"|(?:这两天|到时候).{0,12}(?:好好歇|歇歇|精力|休息)"
            r"|等你消息"
        ),
    ),
    ("cause_guess", re.compile(r"(?:是不是|估计|肯定).{0,14}(?:没好好休息|熬|没睡|没吃|太累|忙坏)")),
    (
        "invented_memory_container",
        re.compile(
            r"(?:(?:记|写|存|放|留|塞|收|加|设|开|备).{0,8}(?:备忘录|备忘|便签|笔记|小本本|本子|日历|提醒|闹钟|清单|待办)"
            r"|(?:备忘录|便签|笔记|小本本|本子|日历|提醒|闹钟|清单|待办).{0,8}(?:记|写|存|放|留|收|有|在)"
            r"|(?:设|加|开).{0,4}(?:提醒|闹钟|日程))"
        ),
    ),
    (
        "invented_external_lookup",
        re.compile(
            r"(?:偷偷|顺手|提前|上周|最近|刚才)?.{0,8}"
            r"(?:查过|查了|看过|听说|听过|打听|留意).{0,14}"
            r"(?:展|展馆|信息|消息|资料|情况|不错)"
        ),
    ),
)
_SCENE_GROUNDING_ANCHORS: dict[str, tuple[str, ...]] = {
    "daily_good_morning": ("早", "早安"),
    "casual_hello_cn": ("客气", "正经", "你好", "端着", "外人"),
    "casual_hello_en": ("正式", "中文", "正经"),
    "availability_check": ("在", "回", "看见"),
    "wait": ("等", "去", "慢慢", "处理", "忙"),
    "back": ("回", "回来", "继续"),
    "busy": ("忙", "去", "累", "活"),
    "busy_done": ("忙完", "辛苦", "歇", "缓", "休息"),
    "bored": ("无聊", "闹", "说", "拉", "热闹"),
    "tired": ("累", "歇", "缓", "撑"),
    "nothing": ("没事", "追问", "放", "待"),
    "good_news": ("跑通", "搞定", "成功", "小事", "事情", "弄完", "弄成", "做成", "完成", "收掉", "收尾", "结束", "进展", "太好", "高兴", "好耶"),
    "good_news_proud": ("跑通", "搞定", "成功", "小事", "事情", "弄完", "弄成", "做成", "完成", "收掉", "收尾", "结束", "进展", "这波", "这次", "厉害", "行", "不错", "可以", "得意", "夸", "争气", "看见", "这不是"),
    "happy": ("开心", "高兴", "心情"),
    "meal_share": ("吃", "饭", "饱"),
    "hungry": ("吃", "饿", "垫"),
    "sleepy": ("睡", "困", "眯"),
    "headache": ("头疼", "歇", "缓", "撑"),
    "bad_mood": ("心情", "不开心", "待", "陪"),
    "small_failure": ("看", "补救", "弄", "砸", "修", "别急"),
    "advice_continue": ("一小步", "一步", "一处", "一点", "先", "停", "收", "改"),
    "next_step": ("先", "发", "看", "贴", "一步"),
    "help_takeover": ("发", "贴", "看", "红字", "截图", "拿"),
    "write_takeover": ("写", "材料", "要求", "丢来"),
    "identity": ("当前名字",),
    "play_invite": ("陪", "玩", "一起"),
    "quiet": ("小声", "安静", "不吵", "收"),
    "talkative": ("说", "话", "闷"),
    "who_called": ("自己", "来", "想来"),
    "miss_you": ("想",),
    "praise": ("夸", "当然", "眼光", "厉害"),
    "user_proud": ("厉害", "行", "不错", "可以", "得意", "夸", "争气", "看见", "赢"),
    "apology": ("算", "道歉", "知道", "听到"),
    "give_up": ("停", "放", "搁", "缓", "回头", "明天"),
    "complaint": ("烦", "先", "拆", "看"),
    "late_night": ("睡", "熬", "醒"),
    "short_ack": ("接着", "继续", "听着", "在听", "多说", "说点", "就这一个", "就这一下"),
    "followup": ("因为", "就是", "觉得", "想", "然后", "继续", "上一句", "接着"),
    "recall_memory": ("记得", "记着", "没忘", "提过", "你的事", "心里"),
    "relationship_memory_reassurance": ("记得", "记着", "没忘", "没乱忘", "别急", "先听", "按现在"),
    "style_repair": ("抓", "听出来", "重来", "换", "收", "慢", "客服"),
    "technical_help": ("先", "第一", "最上", "红", "报错", "看", "贴"),
}
_SOFT_REASON_SCORE_PENALTIES = {
    "STYLE_TEMPLATE_DENSITY": 3.5,
    "STOCK_OPENING_TEMPLATE": 2.5,
    "OVERUSED_WAVE_PUNCTUATION": 5.0,
    "WHAT_DOING_ATTENTION_WAIT": 4.0,
    "PRESENCE_ATTENTION_DEBT": 4.0,
    "MEAL_SHARE_SCOLDING_OR_PUSHY": 3.5,
    "GOOD_NEWS_PROBING": 5.0,
    "GOOD_NEWS_PROUD_INCOMPLETE_GROUNDING": 5.0,
    "SHORT_ACK_OBEDIENCE": 5.0,
    "ADVICE_VAGUE_PUSH": 5.0,
    "IMPLIED_FACT_RISK": 4.5,
    "INVENTED_MEMORY_CONTAINER": 5.0,
    "INVENTED_EXTERNAL_LOOKUP": 5.0,
    "INVENTED_SPECIFIC_DETAIL": 5.0,
    "WEAK_SCENE_GROUNDING": 4.0,
    "FLAT_GENERIC_LINE": 4.0,
    "BLAND_PASS_LINE": 6.0,
    "QUALITY_SOFT_TRAILING_INCOMPLETE_QUESTION": 7.0,
    "STERILE_TRANSACTION_TONE": 6.0,
    "LOW_HUMAN_TEXTURE": 3.5,
    "LOW_CATCHINESS": 2.0,
    "LINE_RHYTHM_FLAT": 2.5,
    "WRITTEN_REGISTER_TONE": 5.0,
}

_WRITTEN_REGISTER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("heart_phrase_too_written", re.compile(r"(?:心里想着你|心里想你|放在心里|心里放着|记在心里)")),
    ("administrative_noun", re.compile(r"(?:优化流程|相关内容|具体内容|报错内容|报错行|事项|流程|进行|不算浪费力气)")),
    ("polished_reward", re.compile(r"(?:奖励一个抱|奖励你一下|奖励一个)")),
    ("over_neat_summary", re.compile(r"(?:今天先这样|那就先这样|一起过一遍|过一遍|你好好期待吧)")),
)
_SPOKEN_CHAT_MARKERS = (
    "哼",
    "啧",
    "行啦",
    "好啦",
    "得了吧",
    "少来",
    "别装",
    "还嘴硬",
    "算你",
    "这才",
    "拿来",
    "发来",
    "贴过来",
    "扔过来",
    "我瞄一眼",
    "我抓一下",
    "哪里卡了",
    "卡哪了",
    "嘛",
    "啦",
    "呀",
    "呢",
)


@dataclass(frozen=True)
class SampleGuidedTextScore:
    score: float
    hard_fail: bool = False
    hard_gate_codes: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    penalties: tuple[str, ...] = ()
    bonuses: tuple[str, ...] = ()
    dimensions: Mapping[str, float] = field(default_factory=dict)
    weights: Mapping[str, float] = field(default_factory=dict)
    sample_scene_id: str = "default"
    sample_title: str = ""
    good_examples: tuple[str, ...] = ()
    bad_examples: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(float(self.score or 0.0), 3),
            "hard_fail": bool(self.hard_fail),
            "hard_gate_codes": list(self.hard_gate_codes),
            "reason_codes": list(self.reason_codes),
            "penalties": list(self.penalties),
            "bonuses": list(self.bonuses),
            "dimensions": {key: round(float(value), 3) for key, value in dict(self.dimensions).items()},
            "weights": {key: round(float(value), 3) for key, value in dict(self.weights).items()},
            "sample_scene_id": self.sample_scene_id,
            "sample_title": self.sample_title,
            "good_examples": list(self.good_examples),
            "bad_examples": list(self.bad_examples),
        }


def _plain(raw: Any) -> str:
    return _PUNCT_RE.sub("", _clean_text(raw)).lower()


def _contains_any(text: str, terms: tuple[str, ...]) -> list[str]:
    lowered = text.lower()
    return [term for term in terms if term and term.lower() in lowered]


def _axis_value(relationship_axes: Mapping[str, Any] | None, key: str, default: Any = None) -> Any:
    if isinstance(relationship_axes, Mapping):
        return relationship_axes.get(key, default)
    return default


def _axis_progress(relationship_axes: Mapping[str, Any] | None) -> int:
    try:
        return int(_axis_value(relationship_axes, "progress", 2))
    except (TypeError, ValueError):
        return 2


def _axis_distance_penalty_multiplier(
    relationship_axes: Mapping[str, Any] | None,
    hits: Sequence[str],
) -> float:
    if not relationship_axes or not hits:
        return 1.0
    progress = _axis_progress(relationship_axes)
    stage = str(_axis_value(relationship_axes, "stage", "") or "")
    lowered_hits = {str(hit).lower() for hit in hits}
    if progress <= 1:
        return 1.15
    if {"我在这", "我在这里", "我在这".lower(), "我在这里".lower()} & lowered_hits and progress >= 4:
        return 0.45
    if stage in {"lover_early", "lover_stable"}:
        return 0.35
    if progress >= 3:
        return 0.65
    return 0.85


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _drop(dimensions: dict[str, float], key: str, amount: float) -> None:
    dimensions[key] = _clamp(float(dimensions.get(key, 100.0)) - amount)


def _raise(dimensions: dict[str, float], key: str, amount: float) -> None:
    dimensions[key] = _clamp(float(dimensions.get(key, 100.0)) + amount)


def _weights_for_scene(scene_id: str) -> dict[str, float]:
    weights = {dimension: 1.0 for dimension in SCORING_DIMENSIONS}
    if scene_id in _TECHNICAL_SCENES:
        weights["usefulness"] = 1.45
        weights["technical_helpfulness"] = 1.55
        weights["context_fit"] = 1.2
        weights["scene_grounding"] = 1.25
        weights["role_fit"] = 0.8
        weights["distance_control"] = 0.85
        weights["spoken_chat_texture"] = 0.85
        weights["catchiness"] = 0.8
        weights["template_density"] = 0.9
    elif scene_id in _HIGH_EMOTION_SCENES:
        weights["context_fit"] = 1.45
        weights["emotional_containment"] = 0.85  # reduced: high-emotion scenes need space to breathe
        weights["distance_control"] = 1.15
        weights["spoken_chat_texture"] = 1.2
        weights["implied_fact_risk"] = 1.2
        weights["line_rhythm"] = 1.1
        weights["usefulness"] = 0.8
    elif scene_id in _DAILY_SCENES:
        weights["naturalness"] = 1.35
        weights["distance_control"] = 1.3
        weights["context_fit"] = 1.15
        weights["spoken_chat_texture"] = 1.3
        weights["template_density"] = 1.2
        weights["line_rhythm"] = 1.2
        weights["implied_fact_risk"] = 1.15
        weights["catchiness"] = 1.1
        weights["technical_helpfulness"] = 0.55
    return weights


def _weighted_average(dimensions: Mapping[str, float], weights: Mapping[str, float]) -> float:
    total = 0.0
    weight_total = 0.0
    for key in SCORING_DIMENSIONS:
        weight = float(weights.get(key, 1.0))
        total += float(dimensions.get(key, 100.0)) * weight
        weight_total += weight
    return total / weight_total if weight_total else 0.0


def _sample_for_contract(contract: GalgameTextContract) -> PositiveStyleSample:
    scene_id = _clean_text(getattr(contract, "scene_id", "")) or "default"
    if scene_id == "casual_hello":
        scene_id = "casual_hello_cn"
    scene_id = _SAMPLE_SCENE_ALIASES.get(scene_id, scene_id)
    return get_positive_style_sample(scene_id)


def _hard_gate_codes(text: str, quality: TextQualityGateResult | None) -> list[str]:
    codes: list[str] = []
    if not text:
        codes.append("EMPTY_TEXT")
    if _JSONISH_RE.match(text):
        codes.append("JSON_OR_STRUCTURED_OUTPUT")
    if _STAGE_DIRECTION_RE.search(text):
        codes.append("STAGE_DIRECTION_OUTPUT")
    if _BROKEN_REPEAT_RE.search(text):
        codes.append("BROKEN_REPETITION")
    if _KANA_RE.search(text) or not _CJK_RE.search(text):
        codes.append("EXPECTED_ZH_TEXT")
    if _contains_any(text, _IDENTITY_HARD_TERMS):
        codes.append("IDENTITY_ERROR")
    if _contains_any(text, _OBVIOUS_SERVICE_TEMPLATES):
        codes.append("OBVIOUS_CUSTOMER_SERVICE_TEMPLATE")
    if _contains_any(text, _REAL_ACTION_CLAIMS):
        codes.append("REAL_ACTION_FABRICATION")
    if quality is not None:
        for code in quality.reason_codes or ():
            if code in _ABSOLUTE_HARD_QUALITY_CODES:
                codes.append(code)
    return list(dict.fromkeys(codes))


def _template_marker_count(text: str) -> int:
    return sum(text.count(marker) for marker in _STYLE_TEMPLATE_MARKERS if marker)


def _implied_fact_hits(text: str) -> list[str]:
    return [label for label, pattern in _IMPLIED_FACT_REGEXES if pattern.search(text)]


def _scene_grounding_hits(
    *,
    text: str,
    contract: GalgameTextContract,
    scene_id: str,
    good_examples: tuple[str, ...],
) -> list[str]:
    hits: list[str] = []
    for group in getattr(contract, "required_any", ()) or ():
        hits.extend(token for token in group if token and token in text)
    hits.extend(token for token in _SCENE_GROUNDING_ANCHORS.get(scene_id, ()) if token and token in text)
    for example in good_examples:
        if example and example in text:
            hits.append(example)
    return list(dict.fromkeys(hits))


def _line_break_count(text: str) -> int:
    return sum(text.count(marker) for marker in ("，", "。", "！", "？", "…", "、", ",", "!", "?"))


def _has_turn_or_texture(text: str) -> bool:
    return bool(
        _contains_any(text, _CATCHY_TURN_MARKERS)
        or _contains_any(text, _HUMAN_TEXTURE_MARKERS)
        or re.search(r"(.)\1|[！？…]", text)
    )


def _bland_pass_hits(text: str, *, scene_id: str) -> list[str]:
    if scene_id not in _DAILY_SCENES and scene_id not in {"soft_care"}:
        return []
    if len(text) > 24:
        return []
    if _contains_any(text, _SHORT_ACK_CHARM_MARKERS):
        return []
    return [label for label, pattern in _BLAND_PASS_PATTERNS if pattern.search(text)]


def _human_texture_hits(text: str) -> list[str]:
    return _contains_any(text, _HUMAN_TEXTURE_MARKERS)


def _written_register_hits(text: str, *, scene_id: str) -> list[str]:
    hits = [label for label, pattern in _WRITTEN_REGISTER_PATTERNS if pattern.search(text)]
    if scene_id in _TECHNICAL_SCENES:
        # Technical turns may legitimately mention process-ish words; only keep
        # the pieces that make the final surface read like a formal handoff.
        hits = [label for label in hits if label in {"administrative_noun", "over_neat_summary"}]
    return hits


def _spoken_chat_hits(text: str) -> list[str]:
    return _contains_any(text, _SPOKEN_CHAT_MARKERS)


def signature_texture_hits(text: str) -> list[str]:
    return list(dict.fromkeys(label for label, pattern in _SIGNATURE_TEXTURE_PATTERNS if pattern.search(text)))


def _sterile_transaction_hits(text: str, *, scene_id: str) -> list[str]:
    if scene_id in _TECHNICAL_SCENES:
        return []
    if scene_id not in _DAILY_SCENES and scene_id not in _HIGH_EMOTION_SCENES:
        return []
    hits = [label for label, pattern in _STERILE_TRANSACTION_PATTERNS if pattern.search(text)]
    if scene_id == "recall_memory":
        hits = [label for label in hits if label != "bare_memory_record"]
    return hits


def score_sample_guided_text(
    *,
    text: Any,
    contract: GalgameTextContract,
    quality: TextQualityGateResult | None = None,
    relationship_axes: Mapping[str, Any] | None = None,
    allowed_ascii_terms: Sequence[str] = (),
) -> SampleGuidedTextScore:
    normalized = _clean_text(text)
    sample = _sample_for_contract(contract)
    contract_scene_id = _clean_text(getattr(contract, "scene_id", ""))
    scene_id = _SAMPLE_SCENE_ALIASES.get(contract_scene_id, contract_scene_id) or _clean_text(sample.scene_id) or "default"
    dimensions = {dimension: 86.0 for dimension in SCORING_DIMENSIONS}
    weights = _weights_for_scene(scene_id)
    reasons: list[str] = []
    penalties: list[str] = []
    bonuses: list[str] = []
    good_examples = tuple(item for item in (sample.good_examples or sample.safe_fragments) if item)
    bad_examples = tuple(item for item in (sample.bad_examples or sample.avoid) if item)

    hard_codes = _hard_gate_codes(normalized, quality)
    if hard_codes:
        for dimension in SCORING_DIMENSIONS:
            dimensions[dimension] = min(dimensions[dimension], 35.0)
        reasons.extend(hard_codes)
        penalties.extend(f"hard_gate:{code}" for code in hard_codes)

    length = len(normalized)
    plain = _plain(normalized)
    if length <= 2:
        _drop(dimensions, "naturalness", 34)
        _drop(dimensions, "context_fit", 18)
        reasons.append("TOO_SHORT")
        penalties.append("naturalness:too_short")
    elif length <= 5:
        _drop(dimensions, "naturalness", 14)
        reasons.append("TOO_SHORT")
        penalties.append("naturalness:thin_short_reply")
    if length > getattr(contract, "target_max_chars", 48):
        _drop(dimensions, "naturalness", min(30, 6 + (length - getattr(contract, "target_max_chars", 48)) * 0.5))
        reasons.append("TOO_LONG")
        penalties.append(f"naturalness:too_long:{length}")
    question_count = _budget_question_count(normalized)
    if question_count > getattr(contract, "max_questions", 1):
        _drop(dimensions, "context_fit", 10)
        _drop(dimensions, "usefulness", 8)
        reasons.append("TOO_MANY_QUESTIONS")
        penalties.append("context_fit:too_many_questions")
    if normalized.count("老哥") + normalized.count("哥哥") > 2 or normalized.count("对方") > 1:
        _drop(dimensions, "naturalness", 6)
        penalties.append("naturalness:address_stacking")
    ascii_terms = {match.casefold() for match in _ASCII_WORD_RE.findall(normalized)}
    allowed_ascii = {
        match.casefold()
        for term in allowed_ascii_terms
        for match in _ASCII_WORD_RE.findall(_clean_text(term))
    }
    if ascii_terms and not ascii_terms.issubset(allowed_ascii):
        _drop(dimensions, "naturalness", 10)
        _drop(dimensions, "context_fit", 8)
        reasons.append("ASCII_WORD_IN_ZH_TEXT")
        penalties.append("context_fit:ascii_word")
    elif ascii_terms:
        bonuses.append("grounded_ascii_term:" + " / ".join(sorted(ascii_terms)))

    template_count = _template_marker_count(normalized)
    if template_count >= 4:
        _drop(dimensions, "template_density", min(34, 8 + template_count * 4))
        _drop(dimensions, "character_freshness", min(18, 3 * template_count))
        reasons.append("STYLE_TEMPLATE_DENSITY")
        penalties.append(f"template_density:marker_count:{template_count}")
    elif template_count >= 3 and _STOCK_OPENING_RE.search(normalized):
        _drop(dimensions, "template_density", 16)
        _drop(dimensions, "character_freshness", 8)
        reasons.append("STOCK_OPENING_TEMPLATE")
        penalties.append("template_density:stock_opening")

    implied_hits = _implied_fact_hits(normalized)
    if implied_hits:
        _drop(dimensions, "implied_fact_risk", min(48, 18 * len(implied_hits)))
        _drop(dimensions, "context_fit", min(18, 6 * len(implied_hits)))
        _drop(dimensions, "naturalness", min(12, 4 * len(implied_hits)))
        reasons.append("IMPLIED_FACT_RISK")
        penalties.append("implied_fact_risk:" + " / ".join(implied_hits[:4]))
        if "invented_memory_container" in implied_hits:
            _drop(dimensions, "implied_fact_risk", 10)
            _drop(dimensions, "context_fit", 8)
            _drop(dimensions, "scene_grounding", 6)
            reasons.append("INVENTED_MEMORY_CONTAINER")
            penalties.append("implied_fact_risk:invented_memory_container")
        if "invented_external_lookup" in implied_hits:
            _drop(dimensions, "implied_fact_risk", 12)
            _drop(dimensions, "context_fit", 8)
            _drop(dimensions, "scene_grounding", 4)
            reasons.append("INVENTED_EXTERNAL_LOOKUP")
            penalties.append("implied_fact_risk:invented_external_lookup")
        specific_detail_hits = {"invented_visual_memory", "invented_specific_event_detail"} & set(implied_hits)
        if specific_detail_hits:
            _drop(dimensions, "implied_fact_risk", 12)
            _drop(dimensions, "context_fit", 8)
            _drop(dimensions, "scene_grounding", 4)
            reasons.append("INVENTED_SPECIFIC_DETAIL")
            penalties.append("implied_fact_risk:invented_specific_detail")

    line_breaks = _line_break_count(normalized)
    if length > 42:
        _drop(dimensions, "line_rhythm", min(24, 6 + (length - 42) * 0.35))
        penalties.append("line_rhythm:overlong")
    if length > 18 and line_breaks == 0:
        _drop(dimensions, "line_rhythm", 12)
        reasons.append("LINE_RHYTHM_FLAT")
        penalties.append("line_rhythm:no_break")
    elif 6 <= length <= 34 and line_breaks <= 3:
        _raise(dimensions, "line_rhythm", 5)
        bonuses.append("line_rhythm:compact")
    bland_hits = _bland_pass_hits(normalized, scene_id=scene_id)
    if bland_hits:
        _drop(dimensions, "catchiness", 26)
        _drop(dimensions, "character_freshness", 18)
        _drop(dimensions, "context_fit", 10)
        _drop(dimensions, "line_rhythm", 6)
        reasons.append("BLAND_PASS_LINE")
        penalties.append("catchiness:bland_pass:" + " / ".join(bland_hits[:4]))
    if scene_id == "what_doing" and _WHAT_DOING_ATTENTION_WAIT_RE.search(normalized):
        _drop(dimensions, "distance_control", 24)
        _drop(dimensions, "context_fit", 14)
        _drop(dimensions, "catchiness", 10)
        reasons.append("WHAT_DOING_ATTENTION_WAIT")
        penalties.append("distance_control:what_doing_waits_for_user")
    if scene_id in {"availability_check", "back", "busy_done"} and _PRESENCE_ATTENTION_DEBT_RE.search(normalized):
        _drop(dimensions, "distance_control", 24)
        _drop(dimensions, "context_fit", 12)
        _drop(dimensions, "catchiness", 8)
        reasons.append("PRESENCE_ATTENTION_DEBT")
        penalties.append("distance_control:presence_attention_debt")
    if scene_id == "meal_share" and _MEAL_SHARE_SCOLDING_RE.search(normalized):
        _drop(dimensions, "context_fit", 16)
        _drop(dimensions, "distance_control", 12)
        _drop(dimensions, "emotional_containment", 10)
        _drop(dimensions, "naturalness", 6)
        reasons.append("MEAL_SHARE_SCOLDING_OR_PUSHY")
        penalties.append("context_fit:meal_share_scolding_or_pushy")
    if scene_id in {"good_news", "good_news_proud"} and _GOOD_NEWS_PROBING_RE.search(normalized):
        _drop(dimensions, "context_fit", 18)
        _drop(dimensions, "scene_grounding", 10)
        _drop(dimensions, "catchiness", 8)
        _drop(dimensions, "distance_control", 8)
        reasons.append("GOOD_NEWS_PROBING")
        penalties.append("context_fit:good_news_probe")
    if scene_id == "short_ack" and _SHORT_ACK_OBEDIENCE_RE.search(normalized):
        _drop(dimensions, "context_fit", 16)
        _drop(dimensions, "distance_control", 14)
        _drop(dimensions, "catchiness", 10)
        _drop(dimensions, "character_freshness", 8)
        reasons.append("SHORT_ACK_OBEDIENCE")
        penalties.append("context_fit:short_ack_obedience")
    if scene_id == "advice_continue" and _ADVICE_VAGUE_PUSH_RE.search(normalized):
        _drop(dimensions, "usefulness", 18)
        _drop(dimensions, "technical_helpfulness", 18)
        _drop(dimensions, "scene_grounding", 12)
        _drop(dimensions, "context_fit", 8)
        reasons.append("ADVICE_VAGUE_PUSH")
        penalties.append("technical_helpfulness:advice_vague_push")
    if _FLAT_GENERIC_RE.match(normalized):
        _drop(dimensions, "catchiness", 24)
        _drop(dimensions, "character_freshness", 12)
        _drop(dimensions, "line_rhythm", 8)
        reasons.append("FLAT_GENERIC_LINE")
        penalties.append("catchiness:flat_generic_line")
    elif not _has_turn_or_texture(normalized) and length <= 14:
        _drop(dimensions, "catchiness", 12)
        reasons.append("LOW_CATCHINESS")
        penalties.append("catchiness:no_turn_or_texture")

    human_texture_hits = _human_texture_hits(normalized)
    signature_hits = signature_texture_hits(normalized)
    spoken_hits = _spoken_chat_hits(normalized)
    written_hits = _written_register_hits(normalized, scene_id=scene_id)
    spoken_bonus_blocked = bool(
        hard_codes
        or any(
            reason in reasons
            for reason in (
                "ADVICE_VAGUE_PUSH",
                "GOOD_NEWS_PROBING",
                "IMPLIED_FACT_RISK",
                "MEAL_SHARE_SCOLDING_OR_PUSHY",
                "OILY_OR_OVERBOUND_TONE",
                "SHORT_ACK_OBEDIENCE",
                "CUSTOMER_SERVICE_TONE",
            )
        )
    )
    if written_hits:
        written_amount = 8 if scene_id in _TECHNICAL_SCENES else 18
        _drop(dimensions, "spoken_chat_texture", min(40, written_amount + 4 * len(written_hits)))
        _drop(dimensions, "naturalness", min(16, 5 + 3 * len(written_hits)))
        _drop(dimensions, "catchiness", min(18, 6 + 3 * len(written_hits)))
        _drop(dimensions, "character_freshness", min(14, 4 + 2 * len(written_hits)))
        reasons.append("WRITTEN_REGISTER_TONE")
        penalties.append("spoken_chat_texture:" + " / ".join(written_hits[:4]))
    elif (spoken_hits or signature_hits or human_texture_hits) and not spoken_bonus_blocked:
        _raise(dimensions, "spoken_chat_texture", min(12, 4 + 2 * len(set([*spoken_hits, *signature_hits, *human_texture_hits]))))
        bonuses.append("spoken_chat_texture:" + " / ".join([*spoken_hits, *signature_hits, *human_texture_hits][:4]))
    sterile_transaction_hits = _sterile_transaction_hits(normalized, scene_id=scene_id)
    if sterile_transaction_hits:
        _drop(dimensions, "non_customer_service", min(24, 10 + 4 * len(sterile_transaction_hits)))
        _drop(dimensions, "character_freshness", min(24, 12 + 3 * len(sterile_transaction_hits)))
        _drop(dimensions, "catchiness", min(22, 10 + 3 * len(sterile_transaction_hits)))
        _drop(dimensions, "naturalness", min(14, 5 + 2 * len(sterile_transaction_hits)))
        reasons.append("STERILE_TRANSACTION_TONE")
        penalties.append("non_customer_service:sterile_transaction:" + " / ".join(sterile_transaction_hits[:4]))
    elif scene_id in _DAILY_SCENES and not human_texture_hits and 6 <= length <= 34:
        _drop(dimensions, "character_freshness", 10)
        _drop(dimensions, "catchiness", 8)
        reasons.append("LOW_HUMAN_TEXTURE")
        penalties.append("character_freshness:low_human_texture")
    elif human_texture_hits and template_count <= 2:
        _raise(dimensions, "character_freshness", min(8, 3 * len(human_texture_hits)))
        _raise(dimensions, "catchiness", min(6, 2 * len(human_texture_hits)))
        bonuses.append("human_texture:" + " / ".join(human_texture_hits[:4]))

    signature_bonus_blocked = bool(
        hard_codes
        or sterile_transaction_hits
        or template_count >= 4
        or any(
            reason in reasons
            for reason in (
                "ADVICE_VAGUE_PUSH",
                "GOOD_NEWS_PROBING",
                "IMPLIED_FACT_RISK",
                "MEAL_SHARE_SCOLDING_OR_PUSHY",
                "OILY_OR_OVERBOUND_TONE",
                "SHORT_ACK_OBEDIENCE",
                "CUSTOMER_SERVICE_TONE",
            )
        )
    )
    if signature_hits and not signature_bonus_blocked:
        _raise(dimensions, "character_freshness", min(12, 4 + 3 * len(signature_hits)))
        _raise(dimensions, "catchiness", min(10, 3 + 2 * len(signature_hits)))
        _raise(dimensions, "role_fit", min(6, 2 * len(signature_hits)))
        if scene_id in _DAILY_SCENES:
            _raise(dimensions, "naturalness", 3)
        bonuses.append("signature_texture:" + " / ".join(signature_hits[:4]))

    for group in getattr(contract, "required_any", ()) or ():
        if group and not any(token and token in normalized for token in group):
            _drop(dimensions, "context_fit", 18)
            _drop(dimensions, "usefulness", 8)
            reasons.append("CONTEXT_MISSING_REQUIRED_MOVE")
            penalties.append("context_fit:missing_any:" + "|".join(group))
    if scene_id == "good_news_proud":
        groups = tuple(getattr(contract, "required_any", ()) or ())
        missing_proud_groups = [
            group for group in groups if group and not any(token and token in normalized for token in group)
        ]
        if missing_proud_groups:
            _drop(dimensions, "scene_grounding", 22)
            _drop(dimensions, "context_fit", 10)
            _drop(dimensions, "catchiness", 6)
            reasons.append("GOOD_NEWS_PROUD_INCOMPLETE_GROUNDING")
            penalties.append(
                "scene_grounding:good_news_proud_missing:"
                + " / ".join("|".join(group) for group in missing_proud_groups[:2])
            )

    contract_hits = [
        item
        for item in getattr(contract, "forbidden_surface", ()) or ()
        if item and item in normalized
    ]
    if contract_hits:
        _drop(dimensions, "context_fit", min(34, 7 * len(contract_hits)))
        _drop(dimensions, "distance_control", min(24, 5 * len(contract_hits)))
        reasons.append("CONTEXT_SURFACE_PENALTY")
        penalties.append("context_fit:legacy_surface:" + " / ".join(contract_hits[:5]))

    service_hits = _contains_any(normalized, _SERVICE_TONE)
    if service_hits:
        _drop(dimensions, "non_customer_service", min(55, 18 * len(service_hits)))
        _drop(dimensions, "role_fit", 14)
        reasons.append("CUSTOMER_SERVICE_TONE")
        penalties.append("non_customer_service:" + " / ".join(service_hits[:4]))

    role_hits = _contains_any(normalized, _ROLE_SOFT_TERMS)
    if role_hits:
        _drop(dimensions, "role_fit", min(42, 18 * len(role_hits)))
        _drop(dimensions, "distance_control", min(30, 12 * len(role_hits)))
        reasons.append("ROLE_KINSHIP_SOFT_MISMATCH")
        penalties.append("role_fit:kinship:" + " / ".join(role_hits[:4]))

    oily_hits = _contains_any(normalized, _OILY_TERMS)
    if oily_hits:
        _drop(dimensions, "non_oily", min(58, 22 * len(oily_hits)))
        _drop(dimensions, "distance_control", min(36, 12 * len(oily_hits)))
        reasons.append("OILY_OR_OVERBOUND_TONE")
        penalties.append("non_oily:" + " / ".join(oily_hits[:4]))

    distance_hits = _contains_any(normalized, _RELATION_DISTANCE_TERMS)
    if distance_hits:
        amount = 7 if scene_id in {"availability_check", "wait", "back", "miss_you", "bad_mood", "quiet"} else 13
        multiplier = _axis_distance_penalty_multiplier(relationship_axes, distance_hits)
        _drop(dimensions, "distance_control", min(42, amount * len(distance_hits) * multiplier))
        reasons.append("DISTANCE_CONTROL_SOFT_PENALTY")
        if relationship_axes:
            reasons.append("RELATION_AXES_DISTANCE_CONTEXT")
        penalties.append("distance_control:" + " / ".join(distance_hits[:4]))

    good_hits = [item for item in good_examples if item and item in normalized]
    bad_hits = [item for item in bad_examples if item and item in normalized]
    if good_hits:
        _raise(dimensions, "role_fit", min(10, 3 * len(good_hits)))
        _raise(dimensions, "context_fit", min(10, 3 * len(good_hits)))
        bonuses.append("sample_good_hit:" + " / ".join(good_hits[:4]))
    if any(_plain(example) == plain for example in good_examples):
        _drop(dimensions, "naturalness", 8)
        reasons.append("SAMPLE_EXACT_COPY")
        penalties.append("naturalness:sample_exact_copy")
    if bad_hits:
        _drop(dimensions, "context_fit", min(36, 8 * len(bad_hits)))
        _drop(dimensions, "non_customer_service", min(20, 5 * len(bad_hits)))
        reasons.append("BAD_SAMPLE_SIMILARITY")
        penalties.append("sample_bad_hit:" + " / ".join(bad_hits[:4]))

    grounding_hits = _scene_grounding_hits(
        text=normalized,
        contract=contract,
        scene_id=scene_id,
        good_examples=good_examples,
    )
    if grounding_hits:
        _raise(dimensions, "scene_grounding", min(12, 4 * len(grounding_hits)))
        _raise(dimensions, "context_fit", min(6, 2 * len(grounding_hits)))
        bonuses.append("scene_grounding:" + " / ".join(grounding_hits[:4]))
    elif scene_id not in {"default", "general", "daily_chat"}:
        _drop(dimensions, "scene_grounding", 20)
        _drop(dimensions, "context_fit", 8)
        _drop(dimensions, "catchiness", 6)
        reasons.append("WEAK_SCENE_GROUNDING")
        penalties.append("scene_grounding:no_scene_anchor")

    if any(marker in normalized for marker in _GALGAME_MARKERS):
        _raise(dimensions, "role_fit", 4)
        _raise(dimensions, "naturalness", 3)
        bonuses.append("galgame_marker")
    short_ack_charm_hits = _contains_any(normalized, _SHORT_ACK_CHARM_MARKERS)
    if scene_id == "short_ack" and short_ack_charm_hits:
        _raise(dimensions, "catchiness", min(12, 4 * len(short_ack_charm_hits)))
        _raise(dimensions, "character_freshness", min(8, 3 * len(short_ack_charm_hits)))
        _raise(dimensions, "context_fit", min(6, 2 * len(short_ack_charm_hits)))
        bonuses.append("short_ack_charm:" + " / ".join(short_ack_charm_hits[:4]))
    if 6 <= length <= 42:
        _raise(dimensions, "naturalness", 5)
        bonuses.append("compact_chat_length")

    if scene_id in _TECHNICAL_SCENES:
        tech_hits = _contains_any(normalized, _TECH_HELPFUL_ANCHORS)
        if tech_hits:
            _raise(dimensions, "technical_helpfulness", 10)
            _raise(dimensions, "usefulness", 8)
            bonuses.append("technical_anchor:" + " / ".join(tech_hits[:4]))
        else:
            _drop(dimensions, "technical_helpfulness", 32)
            _drop(dimensions, "usefulness", 24)
            reasons.append("LOW_TECHNICAL_HELPFULNESS")
            penalties.append("technical_helpfulness:missing_actionable_anchor")
    else:
        dimensions["technical_helpfulness"] = max(dimensions["technical_helpfulness"], 82.0)

    if scene_id in _HIGH_EMOTION_SCENES:
        if _budget_question_count(normalized) >= 2:
            _drop(dimensions, "emotional_containment", 18)
            reasons.append("EMOTIONAL_CONTAINMENT_TOO_QUESTIONY")
            penalties.append("emotional_containment:too_questiony")
        if _contains_any(normalized, ("滚", "闭嘴", "懒得理你", "不管你了", "随便你")):
            _drop(dimensions, "emotional_containment", 34)
            reasons.append("EMOTIONAL_CONTAINMENT_ESCALATION")
            penalties.append("emotional_containment:escalation")

    if quality is not None:
        wave_count = normalized.count("~") + normalized.count("～")
        if wave_count > 2:
            _drop(dimensions, "catchiness", 18)
            _drop(dimensions, "character_freshness", 12)
            _drop(dimensions, "line_rhythm", 8)
            _drop(dimensions, "naturalness", 6)
            reasons.append("OVERUSED_WAVE_PUNCTUATION")
            penalties.append("surface:overused_wave_punctuation")
        soft_quality_codes = [
            code for code in quality.reason_codes or () if code not in _ABSOLUTE_HARD_QUALITY_CODES
        ]
        if soft_quality_codes:
            _drop(dimensions, "naturalness", min(18, 4 * len(soft_quality_codes)))
            _drop(dimensions, "context_fit", min(18, 4 * len(soft_quality_codes)))
            reasons.extend(f"QUALITY_SOFT_{code}" for code in soft_quality_codes)
            penalties.append("quality_soft:" + " / ".join(soft_quality_codes[:5]))

    score = _weighted_average(dimensions, weights)
    soft_score_penalty = sum(_SOFT_REASON_SCORE_PENALTIES.get(reason, 0.0) for reason in set(reasons))
    if soft_score_penalty:
        score -= soft_score_penalty
        penalties.append(f"score:soft_style_penalty:{soft_score_penalty:.1f}")
    if hard_codes:
        score = min(score, 35.0)
    return SampleGuidedTextScore(
        score=_clamp(score),
        hard_fail=bool(hard_codes),
        hard_gate_codes=tuple(hard_codes),
        reason_codes=tuple(dict.fromkeys(reasons)),
        penalties=tuple(penalties),
        bonuses=tuple(dict.fromkeys(bonuses)),
        dimensions={key: _clamp(value) for key, value in dimensions.items()},
        weights=weights,
        sample_scene_id=scene_id,
        sample_title=sample.title,
        good_examples=good_examples,
        bad_examples=bad_examples,
    )


__all__ = [
    "SCORING_DIMENSIONS",
    "SampleGuidedTextScore",
    "score_sample_guided_text",
    "signature_texture_hits",
]
