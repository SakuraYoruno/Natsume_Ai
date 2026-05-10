from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
from typing import Any


_INTERNAL_FIELD_RE = re.compile(
    r"(reply_contract|tactic_family|trajectory_stage|control_line|"
    r"Conversation State Directive|render_policy_summary|response_form_reason)",
    re.IGNORECASE,
)
_MOJIBAKE_RE = re.compile(r"(Ã.|â.|å.|ä.|�)")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_ROLE_PREFIX_RE = re.compile(r"^(新海天|天|assistant|ai|系统)\s*[：:]\s*", re.IGNORECASE)
_JSONISH_RE = re.compile(r"^\s*[\[{].*[\]}]\s*$", re.DOTALL)
_BROKEN_REPEAT_RE = re.compile(r"(.{1,8})\1{3,}")
_ASCII_ELLIPSIS_RE = re.compile(r"\.\.\.")
_TRAILING_INCOMPLETE_QUESTION_RE = re.compile(r"(卡在哪|在哪|哪里|怎么|什么|为啥|为什么)\s*$")
_THIN_ACK_ONLY_RE = re.compile(
    r"^[….\s。！!？?、，,]*(?:嗯[、，,\s]*)?(?:哦|嗯|好|行|知道了|来了|在|我在|我在呢|在呀|在啊|在啦|在呢|在这|听着|听见了)[….\s。！!？?、，,]*$"
)
_STAGE_DIRECTION_RE = re.compile(
    r"(?:[（(【\[].{0,120}?[）)】\]]|\*.{1,120}?\*)",
    re.DOTALL,
)
_QUOTE_AFTER_DIRECTION_RE = re.compile(r"(?:[）)】\]]|\*)\s*[\r\n]+\s*[“\"']")
_RELATIONSHIP_BOUNDARY_MISREAD_RE = re.compile(
    r"(?:我们|咱们).{0,6}很熟吗|谁规定.{0,8}很熟.{0,8}帮忙|还问.{0,4}熟不熟",
    re.DOTALL,
)
_SPEECH_QUOTE_RE = re.compile(r"[“\"'「『]([^“”\"'「」『』\r\n]{1,360})[”\"'」』]")

_FORBIDDEN_TEXT_PATTERNS: tuple[str, ...] = (
    "哥哥! 👋",
    "尼尼! 👋",
    "哥哥！👋",
    "尼尼！👋",
    "哥哥",
    "我哥",
    "您老",
    "因为。",
    "我刚刚在叫哥哥你呀",
    "我刚刚在叫尼尼你呀",
    "我先看到的内容大概是",
    "我先看到的内容是",
    "内容大概是",
    "扫描",
    "这张图里的",
    "这张图片里的",
    "没熟到那种地步",
    "刚认识",
    "第一次见面",
    "第一次见",
    "找错人",
    "发错人",
    "真正想问",
    "为你死都无所谓",
    "我们都住在一起了",
    "住在一起了，你还问熟不熟",
    "谁让你是尼尼",
    "我可是你妹妹",
    "我是你妹妹",
    "尼尼的妹妹",
    "你妹",
    "义妹",
    "重组家庭",
    "身份证明",
    "身份证号",
    "记性不好",
    "明天醒了",
    "理所当然",
    "怎么了？",
    "怎么了?",
    "怎么啦？",
    "怎么啦?",
    "什么事呀",
    "什么事啊",
    "什么事？",
    "什么事?",
    "有什么事吗",
    "有事吗",
    "找我什么事",
    "突然这么客气",
    "怎么突然这么客气",
    "今天怎么有空",
    "主动找我",
    "直接说事",
    "找点乐子",
    "忙你的去",
    "熬到天快亮",
    "天快亮",
    "才歇下来",
    "长蘑菇",
    "总算记得",
    "总算想起来",
    "终于想起我",
    "总算想起我",
    "这声早",
    "多热情",
    "还有个人在等",
    "再睡五分钟",
    "某人早上",
    "是什么好事",
    "你想做什么",
    "你想玩什么",
    "那你想做什么",
    "那你想玩什么",
    "讲个笑话",
    "快说快说",
    "快说说",
    "那必须是你",
    "搞什么名堂",
    "反正迟早的事",
    "让我猜猜",
    "熬夜硬抠",
    "安静这么久",
    "没吱声",
    "什么为什么",
    "爱说不说",
    "等这么久",
    "等尼尼好久",
    "太久我就",
    "不等了",
    "不等你",
    "可别让我等",
    "才刚开始",
    "昨晚睡",
    "昨晚",
    "昨天晚上",
    "起不来",
    "起这么准时",
    "算你准时",
    "今天放假",
    "中午才",
    "等到中午",
    "睡到中午",
    "刷手机",
    "回得太早",
    "晒到被子",
    "人在这里",
    "不像你会说",
    "偷偷摸鱼",
    "刚想找你",
    "刚打算自己",
    "冰箱",
    "炸鸡",
    "乌龙茶",
    "西兰花",
    "泡杯热茶",
    "给你泡",
    "给你倒",
    "我去给你",
    "笨手笨脚",
    "原谅你",
    "跟哥说",
    "你一个人待着吧",
    "我不说了",
    "想让我帮你干什么",
    "想让我帮你干啥",
    "想让我干什么",
    "收拾碗筷",
    "自己做的还是外卖",
    "说来听听",
    "讲来听听",
    "谁又惹",
    "我们家尼尼",
    "我家尼尼",
    "饭吃了没",
    "反斜杠",
    "改成正的",
    "这就放弃",
    "没耐心",
    "少块肉",
    "掉块肉",
    "平时",
    "平常",
    "每次",
    "总是",
    "偷偷绕",
    "绕开细节",
    "好好干活",
    "早凉透",
    "我这不是在回你",
    "我哪有不理你",
    "我这不是在嘛",
    "药我放",
    "药放",
    "自己记得吃",
    "知道错",
    "知错",
    "下次可别",
    "下次不准",
    "下不为例",
    "下回再这样",
    "凭空编",
    "舒服的姿势",
    "腿搭过来",
    "把腿搭",
    "陪你吵",
    "吵两句",
    "摔一次",
    "没摔过",
    "谁稀罕",
    "你拿我怎么办",
    "刚加好友",
    "不熟我也",
    "不熟也",
    "不算熟",
    "就算不熟",
    "熟不熟先放一边",
    "熟不熟先",
    "我们之间的关系",
    "要我帮什么",
    "要我帮啥",
    "划清界限",
    "划界限",
    "看完就走",
    "关我什么事",
    "现在就走",
    "懒得帮",
    "用不着我",
    "什么大事",
    "什么然后",
    "问我然后呢",
    "上次说",
    "上次那个",
    "之前说",
    "不容易嘛",
    "本小姐",
    "可怜兮兮",
    "勉强帮你",
    "顺手帮个忙",
    "谁让你是我尼尼",
    "我过来陪你",
    "过来陪你",
    "过来陪我",
    "我过来",
    "你要不要过来",
    "我凑过来",
    "凑过来",
    "坐你旁边",
    "在你身边",
    "过来我这边",
    "来我这边",
    "闲着也是闲着",
    "说呗",
    "抢我台词",
    "又点名",
    "刚起",
    "才起",
    "挺早",
    "挺精神",
    "醒得",
    "起得",
    "还在睡",
    "在睡呢",
    "没睡醒",
    "才醒",
    "这个点才醒",
    "醒啦",
    "醒啦？",
    "还知道找我",
    "有点良心",
    "早上先找我",
    "主动发消息",
    "居然主动发",
    "难得你会先打招呼",
    "难得主动",
    "这么主动",
    "今天这么主动",
    "主动跟我打招呼",
    "主动打招呼",
    "主动报到",
    "被我吵醒",
    "查我岗",
    "想问什么",
    "想说什么",
    "有什么想说",
    "有什么想问",
    "直接说就好",
    "你说就是",
    "有什么打算",
    "打算没",
    "精神不错",
    "突然夸",
    "还困",
    "困着",
    "能睡",
    "没吃早饭",
    "吃早饭",
    "有点饿",
    "被热醒",
    "打发时间",
    "打发一下时间",
    "消磨时间",
    "大眼瞪小眼",
    "看个电影",
    "看电影",
    "玩点啥",
    "来玩点",
    "猜拳",
    "请夜宵",
    "字典",
    "h这个字母",
    "学会说英文",
    "今儿个",
    "怎么想起用英文",
    "怪恶心",
    "跟谁学",
    "吃错药",
    "开眼",
    "直接说“在吗”",
    "直接说在吗",
    "检查系统",
    "系统在线",
    "大清早",
    "一大早",
    "海外党",
    "找人聊天",
    "有事说事",
    "别试探",
    "正好想到",
    "想到尼尼",
    "无聊想找人",
    "想找人拌嘴",
    "才找我",
    "有事先叫你",
    "先叫你",
    "你先找我",
    "你讲",
    "忙完叫我",
    "你忙完叫我",
    "我先忙",
    "我也先忙",
    "我去忙",
    "自己的事",
    "忙点自己",
    "打会游戏",
    "打会儿游戏",
    "正闷着",
    "咱俩都无聊",
    "找点事干",
    "找点事做",
    "找点事干干",
    "无聊炸",
    "保证比",
    "椅子",
    "行，你忙你的",
    "行吧，去忙你的",
    "你忙你的",
    "先忙你的",
    "忙你的",
    "屋里闷死",
    "屋里太闷",
    "没认真听",
    "声音放大",
    "放大一点",
    "一起无聊好了",
    "一起无聊",
    "没事干",
    "我刚拆",
    "刚拆了一盒",
    "拼图",
    "捡到钱",
    "打赢那关",
    "太阳打西边",
    "审你一下",
    "装得很熟",
    "装得很熟的样子",
    "今天干嘛去了",
    "今天干嘛去",
    "不像你耶",
    "掐指一算",
    "数到三百",
    "等睡着",
    "迷路",
    "等你半天",
    "被拐跑",
    "便利店",
    "收租金",
    "刚好路过",
    "路过这边",
    "特意等你",
    "这边自己待",
    "自己待会",
    "拯救一下",
    "无聊的时光",
    "懒得动",
    "见识一下我的厉害",
    "见识我的厉害",
    "冒出来",
    "这都几点了",
    "睡过整个上午",
    "整个上午",
    "今天天气",
    "阳台",
    "看了会儿天",
    "赖床",
    "吃个早饭再出门",
    "欢迎回来",
    "求我一句",
    "一起骂他",
    "谁惹你",
    "带带我",
    "剥橘子",
    "喏，先把这个",
    "正闲着剥",
    "正闲着",
    "正好闲着",
    "刚好闲着",
    "正好也没什么事",
    "我正好也没什么事",
    "我也闲着",
    "反正我也闲着",
    "闲着呢",
    "现在有空",
    "有空陪你",
    "太闷了",
    "帮你补上",
    "正想找你",
    "刚想找你",
    "中午是不是",
    "胃又要抗议",
    "很严重吗",
    "怎么补救",
    "什么问题",
    "那个地方的问题",
    "看了几分钟",
    "过程呢",
    "多大点事",
    "你也有今天",
    "带你干点别的",
    "要不算了",
    "刷完一局",
    "打完一局",
    "玩完一局",
    "哎哟",
    "哎呦",
    "不是白陪",
    "白陪的",
    "等你很久",
    "等了很久",
    "外星人",
    "手机被",
    "劫持",
    "太阳还挺",
    "太阳都晒",
    "妹妹聊天",
    "工作群",
    "商务邮件",
    "先说是什么事",
    "不先问我",
    "先问我一下",
    "一直往下接",
    "手头事",
    "手边的事",
    "这会儿倒是",
    "倒是挺主动",
    "挺主动的嘛",
    "催什么",
    "盯着屏幕",
    "我家妹妹",
    "不该你主动",
    "第三空间",
    "黑眼圈",
    "身体顾好",
    "绝不饶",
    "不饶他",
    "小本本",
    "我在这里",
    "我在这儿呢",
    "我在这就行",
    "记得我在这",
    "我应了一声",
    "抬眼",
    "看向你",
    "嘴角带着",
    "自然的弧度",
    "急得团团转",
    "不识好人心",
    "看个片",
    "看片",
    "陪我一起看",
    "来找我玩",
    "找我玩",
    "干嘛，无聊了",
    "别喊第二遍",
    "把我晾",
    "晾在这",
    "晾着",
    "开始计时",
    "计时了",
    "陪你闹",
    "陪你闹一下",
    "碍事吧",
    "不碍事吧",
    "再有下次",
    "真不理你",
    "讨账",
    "没人逼你",
    "记性这么差",
    "记性不太好",
    "装傻",
    "找到我",
    "算你识相",
    "识相",
    "不会让你继续无聊",
    "继续无聊下去",
    "变出乐子",
    "碍你的眼",
    "急死你",
    "谁教你",
    "谁教你的",
    "从哪学",
    "哪学来的",
    "招呼打法",
    "我刚把手机",
    "把手机锁",
    "手机锁",
    "数到最后",
    "最后一秒",
    "待在这儿可以吧",
    "待在这儿",
    "我就待着",
    "在这待着",
    "我这边先",
    "我这边",
    "刚在发呆",
    "在发呆",
    "发呆",
    "跟谁说话",
    "是在跟谁",
    "国际路线",
    "走国际路线",
    "过来吧",
    "我在旁边",
    "在旁边",
    "旁边呢",
    "跟跟",
    "明明刚才",
    "刚才明明",
    "刚才还想",
    "想跟我说话",
    "开始烦你",
    "你最烦",
    "亲爱的",
    "好好休息吧",
    "我都快忘了",
    "搞点事情",
    "本天才",
    "大发慈悲",
    "点名要我登场",
    "随叫随到",
    "就在这儿待着",
    "在这儿待着",
    "不管你了",
    "自己玩会儿游戏",
    "坐直了",
    "要坐直",
    "喊得这么轻",
    "喊得轻",
    "戴一会儿耳机",
    "戴耳机",
    "一个人无聊",
    "无聊还来找我",
    "还来找我",
    "让人开心一下",
    "板着脸",
    "骗谁呢",
    "转性了",
    "你这一声",
    "一叫我就听见",
    "听见啦",
    "听见了",
    "听到了",
    "头一回听",
    "听尼尼",
    "刚好接到",
    "刚把游戏",
    "游戏也打开",
    "把游戏也打开",
    "把游戏打开",
    "刷到这条消息",
    "刚好又刷到",
    "刚好刷到",
    "去忙的事",
    "找点别的事做",
    "自己找点",
    "有意思的事干",
    "附身",
    "被谁附身",
    "刚看到",
    "刚好看到",
    "等到明天",
    "等你到明天",
    "办完事记得找我",
    "办完事",
    "保证你马上",
    "肚子饿不饿",
    "陪你去吃饭",
    "拿刀",
    "架你脖子",
    "身份卡",
    "卡在哪儿了",
    "卡在哪了",
    "找借口溜",
    "打字都慢",
    "在这赖着",
    "在这里晃悠",
    "确认上瘾",
    "不管熟不熟",
    "你爱怎么说怎么说",
    "忙昏头",
    "故意逗我玩",
    "这东西我熟",
    "我小声点",
    "假装听",
    "聊这么久",
    "人倒也没走",
    "客服",
    "像跟客服",
    "我安静会儿",
    "玩会儿手机",
    "玩会手机",
    "包看懂",
    "诚心诚意",
    "勉强收下",
    "不闹你了",
    "还在这跟我聊",
    "一直问",
    "以前该不会",
    "认真看我",
    "管你熟不熟",
    "问这么多遍",
    "还记不住",
    "不无聊的东西",
    "玩点好玩的",
    "连这个都看不懂",
    "硬撑到挂掉",
    "挂掉",
    "死磕",
    "改到吐",
    "没人追你",
    "要怎么玩",
    "保证让你",
    "保管比",
    "添点热闹",
    "开把游戏",
    "打游戏",
    "游戏声音",
    "打瞌睡",
    "那局",
    "算我赢",
    "布丁",
    "挂着语音",
    "语音眯",
    "我可没说要等你",
    "随时能戳我",
    "随时都在",
    "随时在",
    "收着等",
    "我收着",
    "接住",
    "刚好接住",
    "线接",
    "线接回来了",
    "刚才那句继续",
    "我等一下",
    "窗口",
    "窗口开着",
    "挂着",
    "先撤",
    "再不来",
    "我就先撤",
    "记着哦",
    "加倍还",
    "加点麻烦",
    "添乱",
    "找麻烦",
    "奶茶",
    "客户",
    "对客户",
    "我一直在",
    "我一直都在",
    "忙完啦",
    "忙完了",
    "忙完要是",
    "记得叫我",
    "叫我一下",
    "打开我看看",
    "倒是打开我看看",
    "一眼就看出来",
    "先缓缓再说",
    "你尼尼",
    "接招",
    "准备好接招",
    "我可盯着",
    "盯着呢",
    "岂不是更无聊",
    "好歹还能找我聊天",
    "有事才想到我",
    "才想到我",
    "懒得理我",
    "不可信",
    "可疑",
)

_CRITICAL_FORBIDDEN_TEXT_PATTERNS: tuple[str, ...] = (
    "哥哥! 👋",
    "尼尼! 👋",
    "哥哥！👋",
    "尼尼！👋",
    "哥哥",
    "我哥",
    "因为。",
    "我刚刚在叫哥哥你呀",
    "我刚刚在叫尼尼你呀",
    "我先看到的内容大概是",
    "我先看到的内容是",
    "内容大概是",
    "扫描",
    "这张图里的",
    "这张图片里的",
    "没熟到那种地步",
    "刚认识",
    "第一次见面",
    "第一次见",
    "找错人",
    "发错人",
    "真正想问",
)
_FORBIDDEN_TEXT_REGEXES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("cheap_wave_punctuation", re.compile(r"[~～]")),
    ("awkward_duplicate_object", re.compile(r"(找我我|叫我我|喊我我)")),
    ("guaranteed_laugh_template", re.compile(r"保证.{0,8}(逗|笑)")),
    ("boredom_need_question", re.compile(r"(无聊|退屈).{0,18}(想做什么|想干嘛|想玩什么|玩点什么|玩点啥|玩什么|来玩点|聊什么)")),
    ("presence_need_question", re.compile(r"(在呀|在啊|在呢).{0,12}(怎么[了啦]|有什么事|找我什么事)")),
    ("presence_pressure_misread", re.compile(r"(在呀|在啊|在呢|在的)?.{0,16}(催什么|别催|催我|查岗|审问|盘问)")),
    ("presence_stale_probe", re.compile(r"(在呀|在啊|在呢|在的)?.{0,18}(突然想起我|想起我了|找我了|刚想你|差不多该冒出来|该冒出来|冒出来了|整理东西)")),
    ("greeting_hidden_need_assumption", re.compile(r"(什么大事|要说什么事|要说什么)")),
    (
        "light_touch_active_misread",
        re.compile(r"(早|早呀|早啊|早安|hello|你好).{0,24}(主动|突然|有空|报到|打招呼)|(?:这会儿|今天).{0,12}(主动|报到)")
    ),
    (
        "fabricated_attention_debt",
        re.compile(r"(总算|终于|还知道|居然).{0,12}(想起|记得|找|叫|回).{0,12}(我|尼尼|还有个我)")
    ),
    (
        "fabricated_script_snatch",
        re.compile(r"(抢我台词|台词被.{0,4}抢|我台词.{0,6}抢)")
    ),
    ("pushy_good_news_probe", re.compile(r"快说.{0,4}快说")),
    (
        "spatial_literalization",
        re.compile(r"(?:我|这就|现在|马上).{0,3}过来|凑过来|过来吧|坐你旁边|在你身边|我在旁边|在旁边|旁边呢|靠过来")
    ),
    ("plain_greeting_only", re.compile(r"^[….\s。！!？?、，,]*(?:你好|你好呀|你好啊|hello|哈喽|哈啰|嗨)[….\s。！!？?、，,]*$", re.IGNORECASE)),
    ("ambiguous_younger_sister_slur", re.compile(r"你[\s、，,。.!！?？]*妹")),
    ("plain_morning_only", re.compile(r"^[….\s。！!？?、，,]*(?:早|早啊|早呀|早安)[….\s。！!？?、，,]*$")),
    ("morning_time_guess", re.compile(r"(这么早|起这么早|今天这么早|怎么这么早|这个点才醒|挺精神|能睡)")),
    ("wait_time_pressure", re.compile(r"(还要等多久|等多久|多久啊|快一点|快点回来|开始计时|计时了|计时哦)")),
    ("back_overdramatic_blame", re.compile(r"(丢下我|把我丢|把我晾|晾在这|晾着|等你好久|跑去哪|总算.*回来了|总算舍得|忘了我|把我忘了|不回来了|失踪|失联)")),
    ("busy_guilt_or_pressure", re.compile(r"(忙完.{0,4}记得找我|快点忙完|又要忙|丢下我一个人|丢下我)")),
    (
        "fabricated_offscreen_action",
        re.compile(r"(?:才)?(?:刚|刚才).{0,10}(院子|出门|出去|溜达|转了一圈|回来|吃饭|收拾|洗澡|睡醒|醒|看手机|买菜|想找你|吹了头发|准备躺|整理东西)")
    ),
    (
        "fabricated_daily_motive",
        re.compile(r"((平时|平常|每次|总是).{0,18}(不会|哪有|主动|打招呼|找我|叫我|闯祸|卡住|搞砸|弄坏)|才不会一上来|突然这么礼貌|这么积极)")
    ),
    (
        "fabricated_present_activity",
        re.compile(r"(?:我(?:正?在|刚?在|最近在|在这儿|在这里)|(?:正|刚好|反正我也)?闲着).{0,8}(撕|吃|喝|看|刷|追|打游戏|玩游戏|打会游戏|打会儿游戏|洗澡|躺|睡|买|收拾|整理|写作业|做饭|剥|陪)|(?:刷|打|玩)完一局")
    ),
    (
        "fabricated_past_memory",
        re.compile(r"(那天|上次|以前|之前).{0,20}(喝多|打电话|说过|见过|不是还|明明|谁还|谁说|卡住|卡到|卡过|搞不定|碰到|环境变量|一模一样|游戏|双人|开了)|偷偷弄了很久|刚才是不是|刚刚是不是"),
    ),
    (
        "fabricated_technical_detail",
        re.compile(r"(报错的接口|改了两行|两行参数|参数传递|传参写错|接口啊|参数就通|调试的时候|后来发现)")
    ),
    (
        "fabricated_condition_probe",
        re.compile(r"(什么条件.{0,8}试通|条件下试通|试通的条件)")
    ),
    ("overaggressive_threat", re.compile(r"(咬你|咬.{0,6}血|一脸血|见血|揍你|打你|弄死|杀了|恐吓)")),
    (
        "relationship_disconnect_exit",
        re.compile(
            r"(那你自己弄|你自己弄吧|我不管了|不关我的事|爱怎样怎样|随便你|我走了|不帮就不帮|"
            r"不熟就不帮|不熟就不熟|不熟是吧|不帮了呗|不管就不管|我可不管|那就不管|行.{0,4}不管|别后悔|爱要不要|别指望我|别墨迹|赶紧说|要啥资料|不操这个心|不操心了|"
            r"自个儿看着办|自己看着办|看着办吧|自己搞定|自个儿搞定|自个儿弄|"
            r"那我走|我走呗|走呗|走了算了|"
            r"别来找我|别找我|碰壁了?.{0,8}别来找|碰壁了?.{0,8}别找|我不问了|"
            r"我闭嘴|那我闭嘴|闭嘴咯|多嘴了.{0,8}闭嘴|多管闲事了.{0,8}闭嘴|"
            r"看完拉倒|看完就走|现在就走|拉倒|可别赖账|别赖账|关我什么事|懒得帮)"
        ),
    ),
    (
        "relationship_dismissive_help",
        re.compile(r"((顺手|顺便).{0,8}(看|帮|管|弄|搞定)|看一眼|要不要帮|看不看还另说|不多事|闲得慌|当我闲|管你是不是)"),
    ),
)


def _clean_text(raw: Any) -> str:
    text = str(raw or "").strip()
    text = _ROLE_PREFIX_RE.sub("", text).strip()
    quote_pairs = (('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’"))
    for left, right in quote_pairs:
        if text.startswith(left) and text.endswith(right):
            text = text[len(left) : len(text) - len(right)].strip()
            break
    return text


def extract_sendable_dialogue(raw: Any) -> str:
    """Extract already-generated dialogue from model output.

    This does not author or rewrite text. It only removes non-sendable wrappers
    such as stage directions when the model also produced an explicit speech
    line. The extracted text must still pass TextQualityGate before sending.
    """

    text = _clean_text(raw)
    if not text:
        return ""

    stripped = _STAGE_DIRECTION_RE.sub("", text)
    stripped = re.sub(r"^[\s\r\n]+", "", stripped).strip()
    stripped = re.sub(r"^[，,。.!！?？、；;：:]+", "", stripped).strip()
    source_text = stripped or text
    has_stage_wrapper = bool(_STAGE_DIRECTION_RE.search(text))
    quoted_lines = [
        _clean_text(match.group(1))
        for match in _SPEECH_QUOTE_RE.finditer(source_text)
        if _clean_text(match.group(1))
    ]
    if quoted_lines:
        source_is_only_quotes = bool(
            re.fullmatch(
                r"\s*(?:[“\"'「『][^“”\"'「」『』\r\n]{1,360}[”\"'」』]\s*){1,2}",
                source_text,
                re.DOTALL,
            )
        )
        # Inline quotes often contain the user's words ("在吗", "hello").
        # Only extract quoted dialogue when the model wrapped an actual speech
        # line in stage directions or returned a pure quoted line.
        if has_stage_wrapper or source_is_only_quotes:
            return "\n".join(quoted_lines[-2:]).strip()

    if stripped and stripped != text:
        return _clean_text(stripped)
    return ""


def _contextual_forbidden_hits(*, normalized: str, user_message: Any = "", scene_id: Any = "") -> list[str]:
    user = _clean_text(user_message)
    scene = _clean_text(scene_id)
    hits: list[str] = []
    if user in {"早", "早安", "早上好", "hello", "你好", "hi", "hey"} or scene in {
        "daily_good_morning",
        "daily_greeting_en",
        "daily_greeting_cn",
    }:
        if any(token in normalized for token in ("主动", "报到", "难得", "总算", "终于", "有空", "查岗")):
            hits.append("contextual_light_greeting_misread")
        if user.lower() in {"hello", "hi", "hey"} and any(token.lower() in normalized.lower() for token in ("hello", "hi", "hey")):
            hits.append("contextual_greeting_repeats_english_input")
        if any(token in normalized for token in ("国际路线", "走国际路线", "坐直", "转性", "不认识你", "从哪学", "哪学来的", "附身", "被谁附身", "商务邮件", "发错人")):
            hits.append("contextual_greeting_overplayed_bit")
    if user in {"在吗", "在不在", "喂"} or scene == "presence":
        if any(token in normalized for token in ("催", "查岗", "审问", "盘问", "突然找", "突然叫", "别喊第二遍")):
            hits.append("contextual_presence_pressure_misread")
        if any(token in normalized for token in ("有事吗", "有什么事", "找我有事", "找我什么事")):
            hits.append("contextual_presence_pushes_need")
        if any(token in normalized for token in ("刚想着你", "可能会找我", "挺准时", "喊人倒是", "喊得这么轻", "喊得轻", "听见", "听到", "你这一声", "一叫我", "刚看到", "刚好看到")):
            hits.append("contextual_presence_fabricated_motive_or_timing")
    if scene == "wait" or any(token in user for token in ("等我一下", "等下", "稍等")):
        if any(token in normalized for token in ("计时", "太久", "等太久", "别太久", "不许太久", "我可没说要等你")):
            hits.append("contextual_wait_pressure_misread")
    if scene == "back" or any(token in user for token in ("我回来了", "回来了")):
        if any(token in normalized for token in ("晾", "丢下", "忘了我", "不管了", "不回来了", "等到明天")):
            hits.append("contextual_back_overdramatic_blame")
        if "手机" in normalized and "手机" not in user:
            hits.append("contextual_back_fabricated_phone")
        if any(token in normalized for token in ("亲爱的", "好好休息", "我都快忘了", "游戏", "刚把", "刚开", "刷到这条消息", "刚好又刷到", "刚好刷到", "去忙的事", "忙完啦", "忙完了", "没走远", "翻个身", "继续赖着", "那局", "算我赢")):
            hits.append("contextual_back_wrong_scene_or_relation")
    if scene == "bored" or "无聊" in user:
        if any(token in normalized for token in ("陪你闹", "陪你一起", "一起无聊", "我也无聊", "找点事", "开始烦你", "搞点事情", "本天才", "大发慈悲", "点名要我登场", "一个人无聊", "无聊还来找我", "还来找我", "让人开心一下", "岂不是更无聊", "好歹还能找我聊天", "不无聊的东西", "玩点好玩的", "要怎么玩", "脑子里在想啥", "保证让你", "保管比", "添点热闹", "加点麻烦", "添乱", "找麻烦", "接招", "准备好接招")):
            hits.append("contextual_bored_flat_companion")
    if scene == "apology" or any(token in user for token in ("抱歉", "对不起", "语气不好")):
        if any(token in normalized for token in ("再有下次", "真不理你", "下次", "下回", "知道错")):
            hits.append("contextual_apology_overpunitive")
    if scene in {"nothing", "busy", "quiet", "wait"} or any(token in user for token in ("没事", "先忙", "忙会", "开始干活", "别吵", "等我一下", "等下", "稍等")):
        if any(token in normalized for token in ("待在这儿可以吧", "待在这儿", "就在这儿", "在这儿", "就在这里", "在这里", "我就在这", "这等着", "这儿等着", "这里等着", "这待着", "我就待着", "就在这儿待着", "在这儿待着", "我这边", "在这边", "过来吧", "我在旁边", "在旁边", "旁边呢", "随叫随到", "戴耳机", "发呆", "找点别的事做", "打游戏", "挂着语音", "语音眯", "布丁", "没走远", "不会跑掉", "我收一收", "收一收")):
            hits.append("contextual_spatial_permission_or_self_location")
    if scene == "nothing" or user in {"没事", "没什么", "没啥事"}:
        if any(token in normalized for token in ("明明刚才", "刚才明明", "刚才还想", "想跟我说话")):
            hits.append("contextual_nothing_fabricated_previous_intent")
        if any(token in normalized for token in ("不管你了", "自己玩", "游戏去", "随叫随到", "板着脸", "骗谁", "我就在这儿", "在这儿哦", "正好想安静", "我也想安静", "有点困", "有点困了", "困了", "有事才想到我", "才想到我", "懒得理我", "不可信", "可疑", "开把游戏", "打游戏", "游戏声音", "打瞌睡", "挂着语音", "语音眯", "随时能戳我", "布丁", "我可盯着", "盯着呢", "先挂", "挂啦")):
            hits.append("contextual_nothing_exits_or_fabricates_activity")
    if scene == "small_failure" or any(token in user for token in ("弄砸", "搞砸", "失败")):
        if any(token in normalized for token in ("奶茶", "口渴", "走啦走啦", "出去", "吃点", "喝点", "又双叒叕", "又翻车", "翻车了")):
            hits.append("contextual_failure_offtopic_scene")
    if scene == "followup" or user in {"为什么", "然后", "然后呢", "真的假的"}:
        if any(token in normalized for token in ("下回", "下次", "闷着搞", "念叨", "我家妹妹", "主动说句", "群发", "走错群", "室友")):
            hits.append("contextual_followup_fabricated_script")
        if user in {"然后", "然后呢"} and any(token in normalized for token in ("少了个括号", "代码", "参数", "接口", "那行", "我早看到")):
            hits.append("contextual_followup_fabricated_technical_detail")
    if scene == "talkative" or any(token in user for token in ("这么能说", "话多", "话好多")):
        if any(token in normalized for token in ("盯着屏幕", "一直看我发消息", "等我说话")):
            hits.append("contextual_talkative_screen_fabrication")
    if scene == "busy" or any(token in user for token in ("开始干活", "先忙", "忙会")):
        if any(token in normalized for token in ("在这边安静待着", "监督", "摸鱼", "好好干活", "忙完找我", "回来记得找我", "回来记得", "游戏声音", "打游戏", "玩游戏", "声音调", "调小点", "先记着", "记着哦", "别太久", "忙完要是", "记得叫我", "叫我一下")):
            hits.append("contextual_busy_overcontrol_or_spatial")
    return hits


def _env_bool(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class TextQualityGateResult:
    accepted: bool = False
    reason_codes: tuple[str, ...] = ()
    hard_fail: bool = False
    language: str = ""
    forbidden_hits: tuple[str, ...] = ()
    normalized_text: str = ""
    quality_score: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": bool(self.accepted),
            "reason_codes": list(self.reason_codes),
            "hard_fail": bool(self.hard_fail),
            "language": self.language,
            "forbidden_hits": list(self.forbidden_hits),
            "normalized_text": self.normalized_text,
            "quality_score": float(self.quality_score or 0.0),
        }


class TextQualityGate:
    """Safety and quality guard for normal chat text.

    The gate never authors replacement text. It only accepts or rejects a model
    candidate and reports why.
    """

    def validate(
        self,
        *,
        text: str,
        render_profile: str = "zh_text",
        response_form: str = "text",
        user_message: Any = "",
        scene_id: Any = "",
        template_hard_fail: bool | None = None,
        semantic_forbidden: bool = True,
    ) -> TextQualityGateResult:
        normalized = _clean_text(text)
        if normalized.endswith("…"):
            stripped = normalized.rstrip("…").rstrip()
            if stripped:
                normalized = stripped
        reasons: list[str] = []
        forbidden_patterns = _FORBIDDEN_TEXT_PATTERNS if semantic_forbidden else _CRITICAL_FORBIDDEN_TEXT_PATTERNS
        forbidden_hits = [item for item in forbidden_patterns if item and item in normalized]
        if "我在这儿呢" in forbidden_hits and normalized != "我在这儿呢":
            if any(token in normalized for token in ("别一个人", "不用马上", "不用说", "陪", "怕", "撑着", "闷着")):
                forbidden_hits = [hit for hit in forbidden_hits if hit != "我在这儿呢"]
        forbidden_regex_hits = [
            label for label, pattern in _FORBIDDEN_TEXT_REGEXES if pattern.search(normalized)
        ]
        contextual_hits = (
            _contextual_forbidden_hits(
                normalized=normalized,
                user_message=user_message,
                scene_id=scene_id,
            )
            if semantic_forbidden
            else []
        )
        if not normalized:
            reasons.append("EMPTY_TEXT")
        if len(normalized) > 360 and str(response_form or "text").strip().lower() == "text":
            reasons.append("TOO_LONG_FOR_CHAT_TEXT")
        if _INTERNAL_FIELD_RE.search(normalized):
            reasons.append("INTERNAL_FIELD_LEAK")
        if _MOJIBAKE_RE.search(normalized):
            reasons.append("MOJIBAKE")
        if _JSONISH_RE.match(normalized):
            reasons.append("JSON_OR_STRUCTURED_OUTPUT")
        if _BROKEN_REPEAT_RE.search(normalized):
            reasons.append("BROKEN_REPETITION")
        if _ASCII_ELLIPSIS_RE.search(normalized):
            reasons.append("ASCII_ELLIPSIS_OUTPUT")
        if _TRAILING_INCOMPLETE_QUESTION_RE.search(normalized):
            reasons.append("TRAILING_INCOMPLETE_QUESTION")
        if _THIN_ACK_ONLY_RE.match(normalized):
            reasons.append("LOW_INFORMATION_ACK")
        if _STAGE_DIRECTION_RE.search(normalized) or _QUOTE_AFTER_DIRECTION_RE.search(normalized):
            reasons.append("STAGE_DIRECTION_OUTPUT")
        if normalized.endswith(("——", "--")):
            reasons.append("TRAILING_DASH_OUTPUT")
        if normalized.endswith(("…", "...")):
            reasons.append("TRAILING_ELLIPSIS_OUTPUT")
        if normalized.count("——") >= 2:
            reasons.append("EXCESSIVE_DASH_STYLING")
        if _RELATIONSHIP_BOUNDARY_MISREAD_RE.search(normalized):
            reasons.append("RELATIONSHIP_BOUNDARY_MISREAD")
        if forbidden_hits or forbidden_regex_hits or contextual_hits:
            reasons.append("FORBIDDEN_TEMPLATE_TEXT")
        if template_hard_fail is None:
            template_hard_fail = _env_bool("HOST_TEXT_QUALITY_GATE_TEMPLATE_HARD_FAIL", default=True)
        soft_reasons = set()
        if not template_hard_fail:
            soft_reasons.add("FORBIDDEN_TEMPLATE_TEXT")

        has_cjk = bool(_CJK_RE.search(normalized))
        has_kana = bool(_KANA_RE.search(normalized))
        profile = str(render_profile or "zh_text").strip().lower()
        language = "ja" if has_kana else "zh" if has_cjk else "unknown"
        if profile == "ja_voice":
            if not has_kana:
                reasons.append("EXPECTED_JAPANESE_VOICE_TEXT")
            if any(token in normalized for token in ("尼尼", "哥哥", "跑通了", "不用你管", "帮我")):
                reasons.append("EXPECTED_JAPANESE_VOICE_TEXT")
        else:
            if has_kana or (not has_cjk and normalized):
                reasons.append("EXPECTED_ZH_TEXT")

        hard_fail_reasons = {
            "EMPTY_TEXT",
            "INTERNAL_FIELD_LEAK",
            "MOJIBAKE",
            "JSON_OR_STRUCTURED_OUTPUT",
            "STAGE_DIRECTION_OUTPUT",
            "TRAILING_DASH_OUTPUT",
            "EXCESSIVE_DASH_STYLING",
            "LOW_INFORMATION_ACK",
            "RELATIONSHIP_BOUNDARY_MISREAD",
            "EXPECTED_JAPANESE_VOICE_TEXT",
            "EXPECTED_ZH_TEXT",
        }
        if template_hard_fail:
            hard_fail_reasons.add("FORBIDDEN_TEMPLATE_TEXT")
        blocking_reasons = [reason for reason in reasons if reason not in soft_reasons]
        hard_fail = any(reason in hard_fail_reasons for reason in blocking_reasons)
        accepted = not hard_fail and not blocking_reasons
        score = 1.0
        score -= 0.35 if hard_fail else 0.0
        score -= min(0.45, 0.08 * len(reasons))
        score = max(0.0, min(1.0, score))
        return TextQualityGateResult(
            accepted=accepted,
            reason_codes=tuple(dict.fromkeys(reasons)),
            hard_fail=hard_fail,
            language=language,
            forbidden_hits=tuple([*forbidden_hits, *forbidden_regex_hits, *contextual_hits]),
            normalized_text=normalized,
            quality_score=score,
        )


_GATE: TextQualityGate | None = None


def get_text_quality_gate() -> TextQualityGate:
    global _GATE
    if _GATE is None:
        _GATE = TextQualityGate()
    return _GATE


__all__ = [
    "TextQualityGate",
    "TextQualityGateResult",
    "extract_sendable_dialogue",
    "get_text_quality_gate",
]
