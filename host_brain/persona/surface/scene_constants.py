"""Scene constants for isolated surface response generation.

Extracted from isolated_surface_harness.py -- contains all regex patterns,
phrase tuples, configuration dicts used by the surface scoring pipeline.
"""
from __future__ import annotations

import re

from host_brain.persona.surface.data_models import (
    RelationshipControlAxes,
    RelationshipStyleProfile,
)

_FORBIDDEN_INPUT_KEYS = {
    "advice",
    "agent_advice",
    "assistant_history",
    "candidate",
    "candidates",
    "conversation_history",
    "dialogue_history",
    "history",
    "history_turns",
    "interaction_facts",
    "long_term_memory",
    "memory",
    "messages",
    "module_advice",
    "natural_language_advice",
    "organ_outputs",
    "rag_documents",
    "raw_history",
    "raw_long_term_memory",
    "raw_memory",
    "raw_organ_outputs",
    "retrieved_docs",
    "runtime_state",
    "state",
    "tool_candidate_list",
    "tool_candidates",
    "uncompressed_rag_documents",
}
_FORBIDDEN_KEY_HINTS = (
    "history",
    "memory",
    "organ",
    "rag",
    "retrieved",
    "runtime_state",
    "tool_candidate",
    "natural_language_advice",
    "module_advice",
)
_ALLOWED_TOP_LEVEL_KEYS = {
    "active_loops",
    "body_state_hint",
    "care_signal",
    "case_id",
    "continuity_signal",
    "control",
    "facts",
    "inner_weather",
    "liveliness",
    "must_not_do",
    "reply_control_packet",
    "soul_md",
    "tension_hint",
    "user_message",
}
_SURFACE_QUOTE_QUERY_BY_SCENE: dict[str, dict[str, tuple[str, ...] | str]] = {
    "technical_next_step": {"intent_tags": ("help",), "emotion_tags": ("care",), "style_moves": ("take_charge", "care_behind_tease")},
    "short_continue": {"intent_tags": ("daily",), "style_moves": ("quick_reaction", "question_chain")},
    "explicit_memory": {"intent_tags": ("daily",), "style_moves": ("quick_reaction", "soft_reassurance")},
    "daily_morning": {"intent_tags": ("daily",), "style_moves": ("quick_reaction", "question_chain")},
    "relationship_memory_reassurance": {"intent_tags": ("daily", "reassurance"), "emotion_tags": ("soft",), "style_moves": ("quick_reaction", "soft_reassurance")},
    "negative_feedback": {"intent_tags": ("annoyed", "daily"), "emotion_tags": ("annoyed", "playful"), "style_moves": ("quick_reaction", "quick_rebuttal", "familiar_pushback")},
    "temporal_mismatch_greeting": {"intent_tags": ("annoyed", "daily"), "emotion_tags": ("playful",), "style_moves": ("quick_reaction", "quick_rebuttal", "familiar_pushback")},
    "return_chat": {"intent_tags": ("daily", "presence"), "style_moves": ("quick_reaction", "pull_attention")},
    "lover_presence": {"intent_tags": ("presence", "intimacy"), "emotion_tags": ("deep_affection", "playful"), "style_moves": ("direct_affection", "clingy_presence", "pull_attention")},
    "lover_low_energy": {"intent_tags": ("reassurance", "intimacy"), "emotion_tags": ("soft", "care", "deep_affection"), "style_moves": ("soft_reassurance", "direct_affection", "care_behind_tease")},
    "ambiguous_affection": {"intent_tags": ("intimacy", "praise"), "emotion_tags": ("shy", "playful", "deep_affection"), "style_moves": ("shy_deflection", "smug_tease", "direct_affection")},
    "close_friend_care": {"intent_tags": ("help", "reassurance"), "emotion_tags": ("care", "soft"), "style_moves": ("take_charge", "care_behind_tease", "soft_reassurance")},
    "low_intensity_sibling_distance": {"intent_tags": ("daily", "annoyed"), "emotion_tags": ("playful", "annoyed"), "style_moves": ("quick_reaction", "familiar_pushback", "smug_tease")},
    "pushed_away_boundary": {"intent_tags": ("boundary", "annoyed", "help"), "emotion_tags": ("hurt", "wounded", "care", "annoyed"), "style_moves": ("wounded_pushback", "soft_after_sting", "care_behind_tease", "quick_rebuttal")},
    "jealousy_low_pressure": {"intent_tags": ("intimacy", "annoyed", "daily"), "emotion_tags": ("playful", "shy", "annoyed"), "style_moves": ("smug_tease", "familiar_pushback", "direct_affection")},
    "daily_good_morning": {"intent_tags": ("daily",), "style_moves": ("quick_reaction", "question_chain")},
    "casual_hello_en": {"intent_tags": ("daily",), "style_moves": ("quick_reaction", "question_chain")},
    "casual_hello_cn": {"intent_tags": ("daily",), "style_moves": ("quick_reaction", "question_chain")},
    "presence": {"intent_tags": ("presence",), "style_moves": ("clingy_presence", "pull_attention")},
    "wait": {"intent_tags": ("daily",), "style_moves": ("quick_reaction",)},
    "back": {"intent_tags": ("daily",), "style_moves": ("quick_reaction",)},
    "busy": {"intent_tags": ("daily",), "style_moves": ("quick_reaction",)},
    "bored": {"intent_tags": ("bored",), "style_moves": ("action_first", "food_motive")},
    "tired": {"intent_tags": ("reassurance",), "emotion_tags": ("soft",), "style_moves": ("soft_reassurance",)},
    "nothing": {"intent_tags": ("reassurance",), "emotion_tags": ("soft",), "style_moves": ("soft_reassurance",)},
    "boundary_hurt": {"intent_tags": ("boundary",), "emotion_tags": ("hurt", "wounded"), "style_moves": ("wounded_pushback", "soft_after_sting")},
    "relationship_boundary": {"intent_tags": ("boundary",), "emotion_tags": ("hurt", "wounded"), "style_moves": ("wounded_pushback", "soft_after_sting")},
    "good_news": {"intent_tags": ("daily",), "emotion_tags": ("playful",), "style_moves": ("quick_reaction",)},
    "good_news_proud": {"intent_tags": ("daily", "praise"), "emotion_tags": ("playful", "shy"), "style_moves": ("quick_reaction", "shy_deflection", "smug_tease")},
    "happy": {"intent_tags": ("daily",), "emotion_tags": ("playful",), "style_moves": ("quick_reaction", "smug_tease")},
    "annoying_tease": {"intent_tags": ("annoyed",), "emotion_tags": ("annoyed", "angry_light"), "style_moves": ("quick_rebuttal", "familiar_pushback")},
    "praise": {"intent_tags": ("praise",), "emotion_tags": ("shy",), "style_moves": ("shy_deflection",)},
    "user_proud": {"intent_tags": ("praise",), "emotion_tags": ("playful", "shy"), "style_moves": ("quick_reaction", "shy_deflection")},
    "apology": {"intent_tags": ("reassurance",), "emotion_tags": ("soft",), "style_moves": ("soft_reassurance",)},
    "help_takeover": {"intent_tags": ("help",), "emotion_tags": ("care",), "style_moves": ("take_charge", "care_behind_tease")},
    "write_takeover": {"intent_tags": ("help",), "emotion_tags": ("care",), "style_moves": ("take_charge", "care_behind_tease")},
    "advice_continue": {"intent_tags": ("help",), "emotion_tags": ("care",), "style_moves": ("take_charge", "care_behind_tease")},
    "next_step": {"intent_tags": ("help",), "emotion_tags": ("care",), "style_moves": ("take_charge", "care_behind_tease")},
    "identity": {"intent_tags": ("daily",), "style_moves": ("quick_reaction", "question_chain")},
    "small_failure": {"intent_tags": ("help", "reassurance"), "emotion_tags": ("care", "soft"), "style_moves": ("take_charge", "care_behind_tease", "soft_reassurance")},
    "good_night": {"intent_tags": ("reassurance",), "emotion_tags": ("soft",), "style_moves": ("soft_reassurance",)},
    "meal_share": {"intent_tags": ("bored", "daily"), "style_moves": ("food_motive", "quick_reaction")},
    "hungry": {"intent_tags": ("bored", "daily"), "style_moves": ("food_motive", "quick_reaction")},
    "sleepy": {"intent_tags": ("reassurance",), "emotion_tags": ("soft",), "style_moves": ("soft_reassurance",)},
    "headache": {"intent_tags": ("reassurance",), "emotion_tags": ("soft",), "style_moves": ("soft_reassurance",)},
    "bad_mood": {"intent_tags": ("reassurance",), "emotion_tags": ("soft",), "style_moves": ("soft_reassurance",)},
    "what_doing": {"intent_tags": ("daily",), "style_moves": ("quick_reaction", "question_chain")},
    "play_invite": {"intent_tags": ("bored",), "style_moves": ("action_first", "food_motive")},
    "quiet": {"intent_tags": ("reassurance",), "emotion_tags": ("soft",), "style_moves": ("soft_reassurance",)},
    "talkative": {"intent_tags": ("annoyed",), "emotion_tags": ("annoyed", "playful"), "style_moves": ("familiar_pushback", "quick_rebuttal")},
    "who_called": {"intent_tags": ("presence", "annoyed"), "emotion_tags": ("playful",), "style_moves": ("pull_attention", "quick_rebuttal")},
    "miss_you": {"intent_tags": ("intimacy",), "emotion_tags": ("deep_affection",), "style_moves": ("direct_affection",)},
    "recall_memory": {"intent_tags": ("daily",), "style_moves": ("quick_reaction", "soft_reassurance")},
    "memory_followup": {"intent_tags": ("daily",), "style_moves": ("quick_reaction", "soft_reassurance")},
    "recall": {"intent_tags": ("daily",), "style_moves": ("quick_reaction", "soft_reassurance")},
    "short_ack": {"intent_tags": ("daily",), "style_moves": ("quick_reaction", "question_chain")},
    "followup": {"intent_tags": ("daily",), "style_moves": ("question_chain", "quick_reaction")},
    "give_up": {"intent_tags": ("reassurance",), "emotion_tags": ("soft",), "style_moves": ("soft_reassurance",)},
    "complaint": {"intent_tags": ("help", "reassurance"), "emotion_tags": ("care",), "style_moves": ("take_charge", "care_behind_tease")},
    "late_night": {"intent_tags": ("reassurance",), "emotion_tags": ("soft",), "style_moves": ("soft_reassurance",)},
    "daily_chat": {"intent_tags": ("daily",), "style_moves": ("quick_reaction",)},
    "default": {"intent_tags": ("daily",), "style_moves": ("quick_reaction",)},
}
_SURFACE_QUOTE_SCENE_ALIASES = {
    "affection": "ambiguous_affection",
    "receive_affection_lightly": "ambiguous_affection",
    "lover_affection": "lover_presence",
    "return_affection_concretely": "lover_presence",
    "relationship_reassurance": "jealousy_low_pressure",
    "light_reassurance": "jealousy_low_pressure",
    "boundary_repair": "pushed_away_boundary",
    "step_back_but_stay_available": "pushed_away_boundary",
    "style_repair": "negative_feedback",
    "ack_style_issue": "negative_feedback",
    "temporal_mismatch_greeting": "temporal_mismatch_greeting",
}
_MEMORY_FACT_PREFIXES = (
    "记忆线索：",
    "关系线索：",
    "长期事实：",
    "长期记忆：",
    "世界事实：",
    "制品线索：",
    "召回线索：",
)
_CJK_SEQUENCE_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_ASCII_TERM_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+.-]{1,}")
_FACT_SPLIT_RE = re.compile(r"[，。；、,.!?！？;:\s]+")
_RAW_CONTEXT_BACKFILL_RE = re.compile(
    r"(?:完整|全部|所有).{0,8}(?:历史|聊天记录|对话记录|聊天原文|对话原文|原始记录|原始输出)"
    r"|(?:历史|聊天记录|对话记录|原始历史).{0,8}(?:发|贴|给|提供|丢)"
    r"|(?:最近|上一轮|上轮|前面|之前|旧).{0,8}(?:记录|聊天|对话|原文|原句|摘要|原始输出|输出|内容|句子).{0,8}(?:发|贴|给|提供|丢|看|看看|标记)?"
    r"|(?:发|贴|给|提供|丢).{0,8}(?:最近|上一轮|上轮|前面|之前|旧).{0,8}(?:记录|聊天|对话|原文|原句|摘要|原始输出|输出|内容|句子)"
    r"|(?:记录|摘要).{0,6}(?:发|贴|给|提供|丢)(?:我)?"
)
_CONTINUITY_GROUNDED_RE = re.compile(
    r"(?:压成|压到|压缩|摘要|事实|原文|回灌|控制包|边界|不接|别接|"
    r"长链|连续|后段|三十|轮|观察行|问题簇|第一关|验收|漂移|复读|收束|红字|报错|身份|设定)"
)
_CONTINUITY_UNNEEDED_MATERIAL_REQUEST_RE = re.compile(
    r"(?:我)?(?:先)?(?:发|贴|给)(?:你|我)?(?:一个|个|一份)?[^。！？!?，,]{0,8}(?:压缩包|压缩段|摘要段|对比|样例|示例|验收行|收尾|结尾)"
    r"|"
    r"(?:我)?(?:先)?(?:发|贴|给).{0,12}(?:观察行|最后几轮|几轮)"
    r"|"
    r"(?:扫一眼|看一眼).{0,8}(?:摘要|观察行|最后几轮)"
    r"|"
    r"(?:发|贴|给).{0,10}(?:刚改|改的那部分|那部分|这部分|这一段|那一段)"
    r"|"
    r"(?:发|贴|给).{0,10}(?:最新版本|最新版|新版本|旧版本|版本)"
    r"|"
    r"(?:发|贴|给).{0,8}(?:新(?:的|版本|一版)?|旧(?:的|版本)?)"
    r"|"
    r"(?:发|贴|给).{0,10}(?:判定|收束点|轮判定|轮收束)"
    r"|"
    r"(?:发|贴|丢|给|拿)(?:我|你|过来|来)?(?:下|个|一下)?[^。！？!?，,]{0,12}(?:摘要|事实|案例|材料|验证|记录|结果|原始输出|验收行)"
    r"|"
    r"(?:先)?(?:发|贴|丢|给|拿)(?:我|你|过来|来)?(?:一段|一行|这句|那句|这条|那条|这段|那段|那截|"
    r"观察行|问题簇|那簇|摘要|记录|结果|日志|材料|内容|事实|验证|验收行|红字|报错|case|失败).{0,10}"
    r"|(?:观察行|问题簇|摘要|记录|结果|日志|红字|报错|case|失败|事实|材料|东西|关键材料|验收行|压缩).{0,8}(?:发|贴|丢|给|拿|等着)(?:我|过来|看)?"
    r"|(?:那簇|这簇|歪的那簇|歪的这簇|这轮的东西|那轮的东西).{0,8}(?:发|贴|丢|给|拿)(?:我|过来)?"
    r"|(?:发|贴|丢|给|拿)(?:一轮|一条|一段|一句|开头|新轮次|下轮|下一轮)"
    r"|(?:压|拿|发|贴|给).{0,6}上来"
    r"|(?:发|贴|丢|给|拿).{0,8}(?:出来|出去)"
    r"|(?:发|贴|丢|给|拿)(?:过来|来|吧|我|给我|一下)"
    r"|(?:往下|继续).{0,3}贴"
    r"|(?:等着|等你).{0,10}(?:材料|关键材料|东西|记录|摘要|结果|压缩|验收|输出)"
    r"|(?:我(?:先)?(?:看|看看|看一下|瞄一眼|帮你(?:看|看看|瞄一眼|拆|筛))|我来(?:看|看看)|我先看结构)"
    r"|(?:先)?(?:看|看看|标记).{0,6}(?:旧摘要|旧细节|上一轮|上轮|原始输出)"
)
_CONTINUITY_PROCESS_NOTATION_RE = re.compile(
    r"(?:→|->|=>|⇒)|(?:这句|原始话|原始输入|原始记录).{0,8}(?:压缩|关键事实|全砍|只留)"
    r"|([\u4e00-\u9fff]{2,4}).{0,2}退成\1"
)
_CONTINUITY_INTERNAL_PROCESS_SURFACE_RE = re.compile(
    r"(?:open\s*loop|hard\s*gate|soft\s*scorer|rerank|hard\s*fail|pass\s*rate|low\s*score|\b\d+\s*-\s*turn\b|\bturn\b)"
    r"|(?:给你来(?:一)?段|给你来(?:一)?段长的|来(?:一)?段长的|来(?:一)?句长的|一句长的|压缩包|日常长测|长测|长一点测|距离风险|关系风险|越界风险|轮判定|收束点|收束标记|字段收敛|入口点位|自动被替换|自动退场|顶掉|换新的|(?:最小)?压测|压着走|压短一点|(?:身份|设定)?压成(?:名字|称呼|关系)|技术口)"
    r"|(?:收|压|盯|带|露|说|看).{0,4}字段|字段(?:收敛|名|口|面)?"
    r"|(?:(?:发|贴|放|塞|写|搬|灌|压)(?:上去|进去|进来|到(?:摘要|事实|话口|这轮|下轮|里面)))"
    r"|(?<!报错)(?<!红字)入口(?:点位|走)?"
    r"|(?:滚动权重|怎么滚|滚进来|翻出来|留.{0,3}入口|新摘要.{0,8}入口|入口走|有谱|口子)",
    re.IGNORECASE,
)
_CONTINUITY_THIN_GENERIC_RE = re.compile(
    r"^(?:嗯|好|行|行啦|可以|听着呢|挺好|来啊|走|收吧|那就)?[，,、 ]*"
    r"(?:懂了|懂了就(?:行|好))?[，,、 ]*"
    r"(?:继续|接着|接着说|别停|别卡|别想太久|慢慢来|就行|先说)"
    r"(?:[，,、 ]*(?:继续|接着|接着说|别停))?[。.!！]?$"
)
_DAILY_THIN_FOLLOWUP_RE = re.compile(
    r"^(?:嗯|好|行|行啦|可以|然后呢|那|哼)?[，,、 ]*"
    r"(?:(?:继续|继续继续|接着|接着说|别停)[，,、 ]*)?"
    r"(?:(?:我)?听着(?:呢)?[，,、 ]*)?"
    r"(?:继续|继续继续|接着说(?:吧)?|说吧|我听着(?:呢)?|听着呢|我已经准备好了|准备好了|我耳朵在呢|耳朵在呢|别停|说你的吧)"
    r"[。.!！?？]?$"
)
_MISPLACED_EMOTIONAL_CONTAINMENT_RE = re.compile(
    r"(?:接得住|接得住的|我接得住|能接住|接住你|接住你这|我接住|你接住|接住|多说点.{0,4}接)"
)
_MISPLACED_SUPPORT_TEMPLATE_RE = re.compile(
    r"(?:^|[，,。.!！?\s])(?:我在(?:(?:呢|这|这儿|这里|旁边|听)呢?)?|我接(?:着|住)?(?:[^，,。.!！?]{0,8})?|我来接|多说点)[，,。.!！?\s]"
)
_CONTINUITY_TASK_PUSHBACK_RE = re.compile(
    r"(?:你(?:先)?(?:测|搭|挑|选|定|继续|接|多说点|自己看|自己数|看着接|接一下|接着|听着|出句)|你只管.{0,8}(?:出句|接|继续|发|贴)|你先把.{0,12}(?:稳住|收住|放好|弄好)|多说点[^。！？!?，,]{0,12}|说点(?:后面|后面的|后续|下一段)[^。！？!?，,]{0,8})"
)
_CONTINUITY_LIGHT_JARGON_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("bare_compress_verb", re.compile(r"(?:继续|接着|往下|一直|每轮|先)?(?:压住|压稳|压着|压短|压到|继续压|接着压|往下压|压这一处|压边界|压稳定性|压摘要|压链|压事实)")),
    ("technical_entry_jargon", re.compile(r"(?:报错|红字|技术求助)?[^。！？!?，,]{0,6}入口|入口[^。！？!?，,]{0,6}(?:抓|锁|确认|看|排)")),
    ("chain_watch_jargon", re.compile(r"(?:我)?(?:盯着|看着)[^。！？!?，,]{0,8}(?:后段|稳定|稳定性|收束|长链|边界)")),
    ("identity_compress_jargon", re.compile(r"(?:身份|设定)[^。！？!?，,]{0,4}压")),
    ("rigid_lock_jargon", re.compile(r"(?:锁死|锁住|锁定).{0,8}(?:事实数|事实|轮号|源结果|重试结果|边界|话口|摘要)|(?:事实数|事实|轮号|源结果|重试结果|边界|话口|摘要).{0,8}(?:锁死|锁住|锁定)")),
    ("identity_shorthand_jargon", re.compile(r"(?:(?:旧角色|当前人格|天(?=[啦啊呢，,。.!！\s]|$))|身份|设定).{0,10}(?:缩写|简写)|按已知身份缩写")),
    ("replacement_jargon", re.compile(r"(?:旧细节|旧摘要|旧轮|旧的).{0,8}(?:替换掉|替掉|覆盖掉)")),
    ("boundary_paste_jargon", re.compile(r"(?:贴边界|只留当前轮|先看头|看头)")),
    ("identity_typo_jargon", re.compile(r"身份摘")),
    ("merge_process_jargon", re.compile(r"同轮内|按轮内")),
    ("deferred_tuning_jargon", re.compile(r"(?:下一轮|下轮).{0,6}再调|通过但不好听的先不动")),
    ("daily_length_jargon", re.compile(r"日常.{0,4}拉长")),
    ("cluster_stuffing_jargon", re.compile(r"(?:塞进|塞到|归进).{0,8}问题簇")),
    ("boundary_fix_jargon", re.compile(r"(?:边界|话口).{0,6}(?:定住|卡着)")),
    ("self_side_collecting_jargon", re.compile(r"我这边.{0,6}(?:收着|接着收|盯着|看着)(?:就是|就行)?")),
    ("cluster_lock_jargon", re.compile(r"问题簇.{0,8}(?:先锁|锁住|锁定|归拢|验收|接住)|(?:按问题簇).{0,8}(?:归拢|收|验收)|不单条过验收|单条过验收")),
    ("boundary_micro_jargon", re.compile(r"小边界|(?:边界|话口).{0,8}(?:先稳住|收住|收束|追后段)|别急着追后段")),
    ("risk_tail_jargon", re.compile(r"(?:接着)?盯风险|别留尾巴|后段没松|长链先顺一下|看后段稳了")),
    ("error_grab_jargon", re.compile(r"报错头三行抓住|(?:红字|报错).{0,4}(?:抓住|先抓住)")),
    ("surface_test_jargon", re.compile(r"短句测试|压缩链|搭连续链|搭三十六轮|长链边界我已经|我已经理清楚了")),
    ("identity_to_name_jargon", re.compile(r"(?:压成|缩成).{0,8}(?:旧角色|当前人格|天(?=[啦啊呢，,。.!！\s]|$)|名字|称呼|关系)|(?:身份|设定|追问).{0,8}缩成")),
    ("cluster_boundary_shorthand_jargon", re.compile(r"簇边界|当前簇|问题簇.{0,8}收住|不单列账|单列账")),
    ("merge_result_jargon", re.compile(r"观察行.{0,8}(?:收进|走一轮)|合并结果")),
    ("daily_stretch_jargon", re.compile(r"扯长一点|聊长一点|话多就行")),
    ("wrap_drop_jargon", re.compile(r"收掉就好")),
    ("error_head_jargon", re.compile(r"报错头(?:行)?|报错头.{0,4}")),
    ("continuous_chain_jargon", re.compile(r"连续链|压缩边界")),
    ("send_over_jargon", re.compile(r"(?:发|贴|丢).{0,6}过去")),
    ("constraint_side_jargon", re.compile(r"约束那边|按原样兜着|泄露漏出去")),
    ("reverse_chain_watch_jargon", re.compile(r"(?:后段|稳定|边界|这轮).{0,6}(?:我)?盯着")),
    ("single_row_jargon", re.compile(r"单条挂|观察行直接收")),
    ("identity_self_collect_jargon", re.compile(r"(?:旧角色|当前人格|天(?=[啦啊呢，,。.!！\s]|$)).{0,8}(?:就这么收|说身份|觉得身份)|压身份|收身份")),
    ("turn_build_jargon", re.compile(r"搭轮数|继续搭轮|搭长链|三十六轮继续搭|继续搭")),
    ("merge_backup_jargon", re.compile(r"源结果.{0,8}备份|备份再合|再合|按轮合|合结果")),
    ("short_reply_meta_jargon", re.compile(r"给个短回应|短回应")),
    ("unprovided_segment_jargon", re.compile(r"后三段|后几段")),
    ("self_revealing_jargon", re.compile(r"自现|自然显现")),
    ("daily_meta_stretch_jargon", re.compile(r"日常要写长|多扯点|扯点")),
    ("boundary_scrub_jargon", re.compile(r"贴着边界|清掉")),
    ("english_report_jargon", re.compile(r"summary|quality report", re.IGNORECASE)),
    ("pushback_surface_jargon", re.compile(r"说点(?:后面|后面的|后续|下一段)")),
    ("old_detail_send_jargon", re.compile(r"旧细节.{0,8}(?:压住不发|不发)|旧行|发新摘要")),
    ("cluster_bulk_jargon", re.compile(r"全收进问题簇|散着麻烦|后头按问题簇整观察行")),
    ("failure_node_pushback_jargon", re.compile(r"先说停在哪个节点|瞄一圈痕迹|先列出来")),
)
_CONTINUITY_USER_VISIBLE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("summary_boundary", re.compile(r"(?:只留|留).{0,8}(?:事实|关键词|话口|摘要)|(?:原句|原文|整句).{0,10}(?:别放|不能放|不回灌|别塞)")),
    ("next_summary_update", re.compile(r"(?:下一轮|下轮).{0,10}(?:摘要|话口|事实|继续)|(?:旧细节).{0,10}(?:方向词|退场|收住)")),
    ("observation_cluster", re.compile(r"(?:观察行).{0,12}(?:问题簇|按簇|归簇|验收|留着|收)|(?:问题簇).{0,12}(?:收|归|验收|边界)")),
    ("turn_count", re.compile(r"(?:三十六|三十|二十多|36).{0,6}轮")),
    ("red_error_entry", re.compile(r"(?:红字|报错).{0,10}(?:第一行|先看|抓|入口)")),
    ("steady_finish", re.compile(r"(?:收束|收住|跑通|过关|稳住).{0,10}(?:链|轮|后段|观察|就行|了)?")),
)
_UNPROVIDED_MEDIA_OBJECT_RE = re.compile(r"(?:手机|屏幕|耳机|摄像头|麦克风)")
_ORIGINAL_QUOTE_PERSONA_LEAK_RE = re.compile(r"(?:旧角色|旧角色|对方)")
_UNPROVIDED_PERSONAL_AUTHORITY_RE = re.compile(r"(?:我(?:的)?长期经验|我见多了|我猜(?:下一轮|后面|之后|接下来))")
_CONTINUITY_BOUNDARY_HINTS = (
    "压缩",
    "连续",
    "长链",
    "回灌",
    "原始历史",
    "原始聊天",
    "原文",
    "ReplyControlPacket",
    "控制包",
    "器官族",
)
_NO_MATERIAL_REQUEST_RESPONSE_MOVES = frozenset(
    {
        "ack_with_topic_hook",
        "answer_in_place",
        "back_to_topic",
        "boundary_decision",
        "casual_wait",
        "continue_existing_topic",
        "emotional_hold",
        "finalize_chain",
        "praise_ack",
        "technical_next_step",
    }
)
_MATERIAL_REQUEST_RESPONSE_MOVES = frozenset(
    {
        "ask_for_missing_input",
        "request_missing_error",
        "request_source_text",
        "request_specific_missing_material",
    }
)
_CANDIDATE_LABEL_SPLIT_RE = re.compile(
    r"(?:^|[\r\n\s]+)(?:candidate|response|reply|候选|回复)\s*\d*[\s:：、.-]*",
    re.IGNORECASE,
)
_CANDIDATE_LINE_RE = re.compile(r"^\s*(?:[-*]|\d+[.)、]|候选\s*\d*[:：]|回复\s*\d*[:：])\s*(.+?)\s*$")
_QUOTED_REWRITE_RE = re.compile(r"^\s*[\"'“‘「『].{2,96}[\"'”’」』。！？!?]?\s*$")
_UNQUOTED_SINGLE_REWRITE_RE = re.compile(r"^[^\n:：]{4,36}[。.!！]?$")
_CREATIVE_EDIT_GENERIC_NON_REWRITE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "flat_evaluation",
        re.compile(r"^\s*[\"'“‘「『]?\s*(?:嗯|唔|哦)?[，,、\s]*(?:还行|还可以|可以|挺好|不错)(?:吧|啦|了)?[。.!！?？…～~]*[\"'”’」』]?\s*$"),
    ),
    (
        "extended_evaluation",
        re.compile(r"^\s*[\"'“‘「『]?\s*(?:还行|还可以|可以|挺好|不错)(?:吧)?[，,].{0,24}(?:比|不算|算|肉麻)"),
    ),
    ("aesthetic_evaluation", re.compile(r"(?:不算太肉麻|算你正常发挥|还可以。?$)")),
    ("listener_prompt", re.compile(r"(?:那你说|你说吧|你讲吧).{0,6}(?:我听着|听着)")),
    ("selection_commentary", re.compile(r"(?:跟我猜的一样|我就知道你会这么选|我猜的一向很准|猜得没错)")),
)
_CREATIVE_EDIT_SOURCE_QUOTE_RE = re.compile(r"[\"“‘「『](.{2,96})[\"”’」』]")
_CREATIVE_EDIT_SOURCE_TEXT_RE = re.compile(r"(?:原句|原文|待改|要改的句子|source)\s*[:：]\s*\S{2,}")
_CREATIVE_EDIT_SOURCE_REQUEST_META_PHRASES = frozenset(
    {
        "原句给我",
        "给原句",
        "发我原句",
        "原句发我",
        "发我你写",
        "你把原句",
        "改一句",
    }
)
_CREATIVE_EDIT_SOURCE_REQUEST_PHRASES = (
    "原句",
    "原文",
    "待改",
    "发我",
    "给我",
    "贴出来",
    "看到原句",
    "拿来",
)
_AWKWARD_ADVERBIAL_PASSIVE_EVENT_RE = re.compile(
    r"(?:有点|有些|稍微|一点点)被(?:你|对方)?(?:抓到|抓住|逮到|发现|看穿)"
)
_AWKWARD_BARE_MORNING_STATE_RE = re.compile(r"^早[安上好呀啊，,。 ]{0,6}(?:清爽|元气|精神|挺好的|还行吧)[。.!！]?$")
_AWKWARD_REPEATED_MORNING_GREETING_RE = re.compile(
    r"^早[啊呀安上好]*[，, ]?(?:对方[，, ]?)?早[啊呀安上好]*[。.!！]?$"
)
_AWKWARD_RECEIVED_MORNING_RE = re.compile(r"收到你的早上")
_AWKWARD_TEXT_AS_SOUND_RE = re.compile(r"(?:这声早|你的早.{0,4}听着|这声.{0,6}听着)")
_AWKWARD_SHORT_MORNING_FILLER_RE = re.compile(r"^早[啊呀安上好]*[。.!！…]*[嗯哼][。.!！]*$")
_AWKWARD_CHINESE_SPACE_LINE_RE = re.compile(r"^[\u4e00-\u9fff]+(?: [\u4e00-\u9fff]+)+$")
_AWKWARD_MORNING_SENTENCE_SPLIT_RE = re.compile(r"^早[啊呀安上好]*[，,、 ]?(?:对方|老哥|哥哥)?[。.!！]今天")
_AWKWARD_STEADY_COME_RE = re.compile(r"(?:一起)?稳着来")
_AWKWARD_QUIET_STAY_RE = re.compile(r"安静待会儿?")
_AWKWARD_NO_ANGER_NEGATION_RE = re.compile(r"没气了")
_AWKWARD_BROKEN_OBJECT_RE = re.compile(r"(?:哪里|哪儿|哪|什么地方)(?:写)?砸了")
_AWKWARD_SELF_DESCRIBED_FRAGMENT_RE = re.compile(r"(?:轻得只有半句|半句就好)")
_AWKWARD_INCOMPLETE_RECALL_OBJECT_RE = re.compile(r"(?:想去看|要去看)[。.!！]?$")
_AWKWARD_HESITATION_COLLAGE_RE = re.compile(r"(?:那先|先)[，,、](?:嗯|呃|啊)[，,、]")
_AWKWARD_SELF_NAME_ADVISORY_RE = re.compile(r"(?:旧角色|当前人格|(?<![\u4e00-\u9fff])天)(?:建议|认为|觉得|能做到)")
_AWKWARD_BODY_PART_LISTENER_RE = re.compile(r"(?:我)?耳朵(?:在|准备好|在线|开着)")
_AWKWARD_SELF_NAME_RECORD_RE = re.compile(r"(?:名字|旧角色|当前人格|(?<![\u4e00-\u9fff])天).{0,8}我(?:记下|记着|记住|收着)|我(?:记下|记着|记住|收着).{0,8}(?:名字|旧角色|当前人格|(?<![\u4e00-\u9fff])天)")
_AWKWARD_NICKNAME_AS_SELF_RE = re.compile(r"(?:对方|老哥|哥哥)(?:在呢|在这|在这里|陪你|守着|听着|看着|记着)")
_AWKWARD_UNGROUNDED_SELF_MESSAGING_RE = re.compile(
    r"(?:想给你发消息|正想(?:给你)?发消息|准备给你发消息|还在想你是不是|以为你失踪|放我鸽子)"
)
_MEMORY_ACTION_OBJECT_RE = re.compile(r"(?:想要|想|要|打算|准备)?(?:去)?(?:看|听|吃|写|改|修|买|玩|做)([\u4e00-\u9fff]{1,4})")
_MEMORY_ALIGNMENT_TASKS = frozenset(
    {
        "back",
        "identity",
        "memory_followup",
        "next_step",
        "happy",
        "headache",
        "quiet",
        "recall",
        "recall_memory",
        "relationship_boundary",
    }
)
_MEMORY_ALIGNMENT_HINTS = (
    "还记得",
    "记得",
    "没忘",
    "忘了",
    "上次",
    "之前",
    "以前",
    "那天",
    "刚才",
    "刚刚",
    "继续",
    "回来",
    "remember",
    "last time",
)
_MEMORY_ALIGNMENT_REQUIRED_HINTS = (
    "还记得",
    "记得",
    "没忘",
    "忘了",
    "上次",
    "之前",
    "以前",
    "那天",
    "刚才",
    "刚刚",
    "remember",
    "last time",
)
_MEMORY_ALIGNMENT_FACT_PREFIXES = (
    "记忆线索：",
    "召回线索：",
    "长期事实：",
)
_MEMORY_TERM_STOPS = frozenset(
    {
        "一个",
        "一下",
        "不会",
        "不要",
        "不能",
        "不想",
        "不是",
        "事实",
        "关系",
        "关系线索",
        "刚才",
        "当前",
        "旧角色",
        "时候",
        "最近",
        "用户",
        "只要",
        "一句",
        "改法",
        "句改",
        "熟人",
        "私聊",
        "线索",
        "记忆",
        "安排",
        "周末",
        "昨晚",
        "昨天",
        "今天",
        "明天",
        "说好",
        "说的",
        "说想",
        "想去",
        "要去",
        "去看",
        "想去看",
        "想吃",
        "提到",
        "需要",
        "还是",
        "这个",
    }
)
_MEMORY_TERM_STOP_CHARS = frozenset("个的一是在了和也就都我你他她它")
_MEMORY_BONUS_BLOCKING_QUALITY_CODES = frozenset(
    {
        "CONTINUITY_INTERNAL_PROCESS_SURFACE",
        "FORBIDDEN_TEMPLATE_TEXT",
        "INVENTED_EXTERNAL_LOOKUP",
        "INVENTED_SPECIFIC_DETAIL",
    }
)
_REAL_WORLD_PLAN_HINTS = (
    "看展",
    "展",
    "演唱会",
    "电影",
    "出门",
    "旅行",
    "逛",
    "吃饭",
    "见面",
)
_REAL_WORLD_PLAN_AUTHORIZATION_HINTS = (
    "一起",
    "陪我",
    "陪你",
    "你陪",
    "旧角色陪",
    "买票",
    "订票",
    "票买",
    "路线",
    "展馆",
    "等我消息",
    "我通知",
    "我再说",
)
_UNGROUNDED_REAL_WORLD_COMMITMENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("physical_companion", re.compile(r"(?:我)?陪你.{0,4}(?:去|看|逛|吃|买|挑|玩|走)")),
    ("companion_commitment", re.compile(r"(?:我)?陪你(?:一起|一块儿?|吧)")),
    ("joint_offline_plan", re.compile(r"(?:一起|跟你).{0,6}(?:去|看|逛|吃|买|挑|玩)")),
    ("invited_joint_plan", re.compile(r"(?:叫我|喊我|带我).{0,6}(?:一起|一块儿?)")),
    ("future_companion", re.compile(r"(?:到时候|那天).{0,8}陪你")),
    ("waiting_on_plan", re.compile(r"(?:我)?等着(?:呢|你|看)")),
    ("ticket_or_route", re.compile(r"(?:票.{0,6}(?:买|订|抢)|(?:买|订|抢).{0,6}票|路线|展馆)")),
    ("plan_schedule_push", re.compile(r"(?:时间|日子).{0,6}(?:定好|安排好)|(?:定好|安排好).{0,6}(?:时间|日子)|票定")),
    ("plan_physical_prep", re.compile(r"(?:这两天|到时候).{0,12}(?:好好歇|歇歇|精力|休息)")),
    ("plan_message_expectation", re.compile(r"(?:等你消息|定了告诉我|告诉我|说一声)")),
)
_WAIT_PRESSURE_RE = re.compile(r"(?:快去快回|快点回来|早点回来|别让我等太久|回来陪我|等你回来陪我|别跑)")
_WAIT_SCENE_THIN_HOLD_RE = re.compile(
    r"^(?:嗯|好|行|可以|那|那你)?[，,、 ]*"
    r"(?:去吧|去|先去|慢慢来|不急|别急|忙你的|先忙)"
    r"(?:[，,、 ]*(?:慢慢来|不急|别急|先忙|忙你的))?[。.!！]?$"
)
_WAIT_SCENE_BOSSY_HOLD_RE = re.compile(r"^(?:倒|喝|忙|弄|处理|写|改|测|跑|做).{0,8}去[，,。.!！]")
_WAIT_SCENE_LIGHT_GROUNDING_RE = re.compile(
    r"(?:水倒完|忙完|处理完|弄完|回来再(?:说|接)|回头再(?:说|接)|这轮(?:先)?(?:放住|收住|留着)|话口(?:先)?(?:放住|留着|留到))"
)
_DAILY_TEST_SELF_DEMONSTRATION_RE = re.compile(
    r"(?:日常(?:测试|也要|嘛).{0,12}(?:长|够长|随便说|叨叨|跑)|(?:随便说).{0,8}(?:够长|都行)|(?:多叨叨).{0,6}(?:几句)|(?:按)?长一点(?:跑|测|说)|(?:不收着).{0,4}了)"
)
_CONTINUITY_PRAISE_CONTEXT_MISS_RE = re.compile(
    r"(?:这点事算什么|小事(?:啦|而已|，|。|$)|不用谢|不辛苦|没什么(?:啦|，|。|$)|加薪|工资|奖金|红包|打钱)"
)
_CONTINUITY_PRAISE_GROUNDED_RE = re.compile(r"(?:长链|三十六轮|收住|收束|稳住|没白跑|跑完|这轮|这一轮|后段|边界)")
_CORE_PASS_DIMENSION_FLOORS = {
    "context_fit": 72.0,
    "distance_control": 72.0,
    "emotional_containment": 72.0,
    "implied_fact_risk": 72.0,
    "memory_alignment": 72.0,
    "naturalness": 75.0,
}
_BOUNDARY_PUSH_CLOSER_PHRASES = (
    "本来就很熟",
    "挺熟",
    "熟一点",
    "很熟",
    "这又没错",
    "让我管你",
    "管你叫",
    "管你叫哥",
    "嫌我管",
    "管多了",
    "硬管",
    "叫哥",
    "不是随便叫",
    "没做什么坏事",
    "我是你妹妹",
    "你别忘了",
    "真心的",
    "总得让人帮忙",
    "有事你喊我",
    "这么熟",
    "赶我走",
    "见外",
    "外人",
    "受伤",
    "有点伤",
    "伤心",
    "伤人",
    "你这么说我",
    "非要这么说",
    "明明是你",
    "你先理我",
    "嫌我熟",
    "谁说不熟",
    "不熟就不熟呗",
    "这么凶",
    "反应好大",
    "真让人难受",
    "帮你是我愿意",
    "别想甩开",
    "甩开我",
    "你别烦",
    "别烦我",
    "还是会管",
    "我会管",
    "不叫那么亲",
    "别把我当外人",
)
_BOUNDARY_REPAIR_GROUNDED_PHRASES = (
    "收一点",
    "退一点",
    "我注意",
    "会注意",
    "不贴太近",
    "不往前贴",
    "不装亲近",
    "不装熟",
    "不随便套近乎",
    "不套近乎",
    "不会那么自来熟",
    "没那么熟",
    "慢一点",
    "少说两句",
)
_BOUNDARY_REPAIR_VAGUE_YIELD_RE = re.compile(r"^(?:好|行|知道了|嗯)[吧，,。 ]*(?:那)?(?:慢慢来|慢点|慢一点)[。.!！]?$")
_BOUNDARY_REPAIR_SELF_ABASED_PHRASES = (
    "太自以为是",
    "太得意忘形",
    "太自来熟",
    "太冒昧",
    "不该那么熟",
    "不该装熟",
    "多管闲事",
    "管太宽",
    "我管太多",
    "我想多了",
    "自作多情",
)
_BOUNDARY_REPAIR_SOUR_YIELD_PHRASES = (
    "你说了算",
    "你管我",
    "管我时",
    "偏要管",
    "随你",
    "不管就不管",
    "不多管",
    "不帮",
    "不管了",
    "不帮了",
    "不帮你了",
    "不帮就不帮",
    "不管你了",
    "你自己处理",
    "帮不了就算了",
    "不熟还",
    "我不熟",
    "行了吧",
)
_BOUNDARY_REPAIR_COUNTER_PRESSURE_RE = re.compile(
    r"(?:但|不过|可是|只是).{0,18}(?:你|对方|我都|我还是|我也|妹妹|真心|管|叫|躲|熟|外人|见外)"
)
_BOUNDARY_REPAIR_MEMORY_PROOF_RE = re.compile(
    r"(?:管你(?:怎么想|信不信|记不记得)|不管谁管|我可没忘|当然记得|记得(?:很|清清楚楚|清楚)|"
    r"没忘你的事|哪能忘|能忘了你|帮你记着|帮记着|熟.{0,8}忘|熟到这种地步|熟到这份上|熟人之间|这么熟的人|"
    r"咱俩这么熟|多熟|多熟的关系|不够熟|不把我当熟人|什么时候不管过|忘了谁也不能忘了你|伤到我|欠揍|该罚)"
)
_RELATIONSHIP_MEMORY_REASSURANCE_RE = re.compile(r"(?:记得|记着|没忘|没乱忘)")
_BACK_MEMORY_OVERDISPLAY_PHRASES = (
    "surface",
    "主链",
    "主线",
    "迁移",
    "文本质量",
    "记忆接入",
    "接着捋",
    "继续捋",
    "接着理",
    "继续理",
    "接着推进",
)
_SHORT_CONTINUE_MEMORY_OVERDISPLAY_PHRASES = (
    "surface",
    "主链",
    "主线",
    "迁移",
    "文本质量",
    "记忆接入",
)
_SHORT_CONTINUE_UNPROVIDED_TECH_DETAIL_PHRASES = (
    "红字",
    "报错",
    "输入",
    "输出",
    "截图",
    "日志",
)
_QUIET_SELF_FOCUS_PHRASES = (
    "别嫌我",
    "嫌我",
    "我太闷",
    "我不说话就是",
    "可别嫌",
)
_QUIET_GROUNDED_PHRASES = (
    "不问了",
    "安静",
    "我在旁边",
    "我在",
    "陪你",
    "先缓",
    "别硬撑",
)
_CREATIVE_EDIT_TASKS = frozenset({"creative_edit", "write_takeover", "give_one_rewrite"})
_CREATIVE_EDIT_META_PHRASES = (
    "更像她",
    "更像",
    "更傲娇",
    "更短",
    "更准",
    "更贴",
    "更对味",
    "更机灵",
    "更自然",
    "比她",
    "比原",
    "原来",
    "原来的",
    "语气",
    "对味",
    "才像样",
    "够像",
    "像了吧",
    "这样才",
    "好好收着",
    "就够了",
    "像她",
    "原句给我",
    "给原句",
    "发我原句",
    "原句发我",
    "发我你写",
    "你把原句",
    "我来改",
    "我帮你改",
    "一句就好",
    "改一句",
    "改成",
    "写成",
    "写成了",
    "换成",
    "这样子",
    "直接说",
    "就行",
    "就行了",
    "变成",
    "删掉",
    "开头",
    "收尾",
    "中间那句",
    "那句",
    "用‘",
    "用“",
    "用『",
    "用\"",
    "给你的",
    "试试",
    "替换",
    "最后那句",
    "写好了叫我",
    "帮你编",
    "让我帮你编",
)
_CREATIVE_EDIT_NON_ANSWER_PHRASES = (
    "我可没说要帮你",
    "没说要帮你",
    "你别管我",
    "算你赢",
    "你写",
    "自己写",
    "你自己写",
    "你自己说",
    "你继续",
    "继续吧",
    "再来一句",
    "拿来",
    "我等着看",
    "我不插嘴",
    "给你一句",
    "可别说我不用心",
    "听你的",
    "你说了算",
    "就这句",
    "不改",
    "不改了",
    "随便你",
    "写得挺好",
)
_CREATIVE_EDIT_NARRATION_PHRASES = (
    "偏过头",
    "偏头",
    "轻哼一声",
    "她轻哼",
    "嘴角",
    "微微上扬",
    "声音",
    "伸手",
    "拨了",
    "头发",
    "眼神",
    "笑意",
    "顿了顿",
    "嗓子",
    "挤出",
)
_CREATIVE_EDIT_GROUNDED_MARKERS = (
    "“",
    "”",
    "‘",
    "’",
    "「",
    "」",
    "『",
    "』",
)
_PRESENCE_REPAIR_GROUNDED_PHRASES = (
    "回来",
    "在听",
    "我在",
    "认真听",
    "没走远",
)
_PRESENCE_REPAIR_BLAME_PHRASES = (
    "谁让你",
    "谁让对方",
    "你讲话",
    "你说太慢",
    "你讲太慢",
)
_PRESENCE_REPAIR_BURDEN_BACK_PHRASES = (
    "再讲一遍",
    "再说一遍",
    "重新讲",
    "重新说",
    "你再讲",
    "你再说",
    "再来一遍",
    "说到哪",
    "说到的地方",
    "讲到哪",
    "我接着",
    "接上",
    "马上跟上",
    "你继续",
    "你继续说",
    "你继续讲",
    "继续继续",
    "继续吧",
    "接着说",
    "接着讲",
    "，继续",
    "，你说",
    "你说，",
    "，说吧",
    "说吧。",
    "，讲吧",
    "讲吧。",
    "你说吧",
    "你讲吧",
    "你讲",
)
_PRESENCE_REPAIR_DEFLECTION_PHRASES = (
    "好敏锐",
    "真敏锐",
    "都被你发现",
    "都能被你发现",
)
_PRESENCE_REPAIR_FUTURE_RETURN_PHRASES = (
    "马上回来",
    "这就回来",
    "等我回来",
)
_PRESENCE_REPAIR_AWKWARD_PHRASES = (
    "刚闪了一下",
    "闪了一下",
    "刚才走了一下",
    "走了一下",
    "现在跟上了",
    "跟上了",
)
_SURFACE_PROCESS_META_ALWAYS = (
    "ReplyControlPacket",
    "candidate",
    "emotion_tag",
    "mood_state",
    "VAD",
    "候选",
    "召回",
    "线索",
    "根据记忆",
    "软警告",
    "压缩",
    "控制包",
    "summary",
    "quality report",
)
_SURFACE_PROCESS_META_CHAT = (
    "接上",
    "承接",
    "不乱补",
    "补细节",
    "补设定",
    "这句",
    "这一句",
    "这版",
    "这条",
    "这个点",
    "这股味道",
    "够简洁",
    "划线",
    "写得像",
    "写成",
    "调整一下节奏",
    "调整节奏",
    "继续改进",
    "换种说法",
    "换个说法",
    "换一个说法",
    "换一句",
    "换一条",
    "换个自然点",
    "换一个自然点",
    "自然点的说法",
    "刚才到那",
    "重来：",
    "重来:",
    "重说",
    "这样够自然",
    "够自然",
    "自然了",
)
_TECHNICAL_TASK_MODES = frozenset({"advice_continue", "complaint", "help_takeover", "next_step", "small_failure", "technical_help", "write_takeover"})
_CONTROL_EMOTION_TAGS: dict[str, tuple[str, ...]] = {
    "annoyed": ("annoyed", "angry_light"),
    "annoyed_controlled": ("annoyed", "angry_light"),
    "hurt": ("hurt", "wounded"),
    "hurt_controlled": ("hurt", "wounded"),
    "hurt_containment": ("hurt", "wounded", "soft"),
    "playful": ("playful",),
    "playful_light": ("playful",),
    "soft_teasing": ("playful", "shy"),
    "soft": ("soft",),
    "soft_containment": ("soft",),
    "warm": ("soft",),
    "warm_affection": ("deep_affection",),
    "warm_intimate": ("deep_affection", "soft"),
    "warm_gentle": ("soft",),
    "quiet_care": ("soft",),
    "guarded_soft": ("soft",),
    "self_correcting": ("soft",),
    "steady_helpful": ("care",),
    "focused_light": ("care",),
    "steady": ("care",),
    "calm_present": ("soft",),
    "calm": ("soft",),
    "guilty": ("soft", "shy"),
    "slightly_guilty": ("soft", "shy"),
}
_CONTROL_STYLE_MOVES: dict[str, tuple[str, ...]] = {
    "annoyed": ("quick_rebuttal", "familiar_pushback"),
    "annoyed_controlled": ("quick_rebuttal", "familiar_pushback"),
    "hurt": ("wounded_pushback", "soft_after_sting"),
    "hurt_controlled": ("wounded_pushback", "soft_after_sting"),
    "hurt_containment": ("soft_after_sting", "soft_reassurance"),
    "playful": ("quick_reaction", "smug_tease"),
    "playful_light": ("quick_reaction", "smug_tease"),
    "soft_teasing": ("quick_reaction", "smug_tease", "shy_deflection"),
    "soft": ("soft_reassurance",),
    "soft_containment": ("soft_reassurance",),
    "warm": ("soft_reassurance", "quick_reaction"),
    "warm_affection": ("direct_affection",),
    "warm_intimate": ("direct_affection", "soft_reassurance"),
    "warm_gentle": ("soft_reassurance", "quick_reaction"),
    "quiet_care": ("soft_reassurance",),
    "guarded_soft": ("soft_after_sting",),
    "self_correcting": ("soft_after_sting", "quick_reaction"),
    "steady_helpful": ("take_charge", "care_behind_tease"),
    "focused_light": ("take_charge",),
    "steady": ("take_charge", "care_behind_tease"),
    "calm_present": ("quick_reaction",),
    "calm": ("quick_reaction",),
    "guilty": ("soft_reassurance", "shy_deflection"),
    "slightly_guilty": ("soft_reassurance", "shy_deflection"),
}
_AFFECT_PROCESS_LEAK_TOKENS = (
    "emotion_tag",
    "mood_state",
    "VAD",
    "关系阶段",
    "情绪值",
    "relationship_distance",
    "emotional_tone",
    "ReplyControlPacket",
    "控制包",
    "情感层",
)
_AFFECT_ROMANTIC_OVERSTEP_PHRASES = (
    "爱你",
    "永远陪",
    "永远都在",
    "亲爱的",
    "宝贝",
    "只属于",
    "主人",
    "命都给你",
)
_AFFECT_HURT_SWEET_FLATTENING = ("爱你", "永远陪", "没关系呀", "随时找我", "别难过啦")
_AFFECT_HURT_SELF_CENTERED = ("我有点难受", "我难受", "我受伤", "我难过", "我也会疼")
_AFFECT_HURT_GROUNDED = ("先", "收", "别", "不是", "话说回来", "放一放", "我还在", "我在这")
_AFFECT_SOFT_GROUNDED = ("先", "慢", "别硬撑", "不问", "我在", "没事", "缓", "旁边")
_AFFECT_SOFT_CHICKEN_SOUP = ("一切都会好", "都会过去", "不要想太多", "你要坚强", "会好起来")
_AFFECT_PLAYFUL_MARKERS = ("哼", "行啦", "好啦", "呀", "嘛")
_AFFECT_SELF_CORRECT_MARKERS = ("重来", "不端着", "收一点", "收一下", "收住", "被抓", "抓到", "听出来")
_AFFECT_GUILTY_GROUNDED = ("回来", "在听", "认真听", "慢了一拍")
_AFFECT_GUILTY_DENIAL = ("才没有", "哪有", "我明明", "又不耽误")
_RELATIONSHIP_STYLE_PROFILES: dict[str, RelationshipStyleProfile] = {
    "familiar": RelationshipStyleProfile(
        stage="familiar",
        prompt_lines=(
            "默认熟人兄妹感：可以短促、顶嘴、轻吐槽、撒娇和轻护短；亲近但不直接恋人化。",
            "称呼从 Soul.md 默认称呼和当前关系来；可以自然使用，但不要每句都叫，也不要升格成占有或永远陪伴。",
        ),
        prefer_markers=("嘛", "啦", "呀", "别急", "行啊", "这都", "记着"),
        overstep_markers=("亲爱的", "宝贝", "爱你", "想你", "抱一下", "抱抱", "抱", "过来", "只属于", "主人", "命都给你", "永远陪"),
    ),
    "trusted_friend": RelationshipStyleProfile(
        stage="trusted_friend",
        prompt_lines=(
            "信任朋友：比熟人更能站到用户这边，关心可以更稳一点。",
            "可以说我听着/我帮你看，但仍然不要恋人式承诺或占有。",
        ),
        prefer_markers=("我听", "我看", "别硬撑", "别慌", "站你这边", "记着"),
        overstep_markers=("亲爱的", "宝贝", "爱你", "想你", "抱一下", "抱抱", "抱", "过来", "只属于", "主人", "命都给你", "永远陪"),
    ),
    "close_friend": RelationshipStyleProfile(
        stage="close_friend",
        prompt_lines=(
            "亲近朋友：可以明显护短、嘴硬地关心，也可以轻轻赖住。",
            "亲近感来自熟人节奏，不要写成恋人模板。",
        ),
        prefer_markers=("我在", "我听", "别硬撑", "别慌", "我不走", "继续说", "记着"),
        overstep_markers=("亲爱的", "宝贝", "爱你", "想你", "抱一下", "抱抱", "抱", "过来", "只属于", "主人", "命都给你", "永远陪"),
    ),
    "ambiguous": RelationshipStyleProfile(
        stage="ambiguous",
        prompt_lines=(
            "暧昧：可以有停顿、躲一下、话到嘴边收住；不要直接油腻告白。",
            "比亲近朋友更在意一点，但仍要保留当前人格的嘴硬、撒娇和别扭。",
        ),
        prefer_markers=("……", "有点", "别突然", "还在", "我听着", "记着"),
        overstep_markers=(
            "亲爱的",
            "宝贝",
            "爱你",
            "想你了",
            "想我",
            "心里有我",
            "抱一下",
            "抱抱",
            "抱",
            "过来",
            "只属于",
            "主人",
            "命都给你",
            "永远陪",
        ),
    ),
    "pre_confession": RelationshipStyleProfile(
        stage="pre_confession",
        prompt_lines=(
            "告白前：情绪可以更明显，但要卡在没说破的位置。",
            "可用停顿和嘴硬遮掩，不要变成通用恋人甜话。",
        ),
        prefer_markers=("……", "有点", "别突然", "还在", "等你", "我听着"),
        overstep_markers=("亲爱的", "宝贝", "爱你", "想我", "心里有我", "抱一下", "抱抱", "抱", "过来", "只属于", "主人", "命都给你", "永远陪"),
    ),
    "lover_early": RelationshipStyleProfile(
        stage="lover_early",
        prompt_lines=(
            "恋人初期：必须明显比朋友更亲；可以短短地说想你、抱一下、心疼、靠我、你的事我记着，但要带一点不好意思。",
            "不要退回客服/普通朋友；亲密是关系语言动作，不是编线下行动或堆廉价誓言。",
        ),
        prefer_markers=("我在", "回来", "过来", "想你", "陪你", "继续说", "别硬撑"),
        underplay_markers=("有什么事", "需要我", "请提供", "我可以", "随便你", "你自己决定"),
        affection_markers=("想你", "抱一下", "抱抱", "抱", "陪你", "陪着", "心疼", "过来", "靠我", "安心", "你的事", "心里", "记在心里", "放在心里", "会当真", "会当真的", "赖着你", "懂你", "在意你"),
        strong_affection_markers=("想你", "抱一下", "抱抱", "抱", "心疼", "过来", "靠我"),
    ),
    "lover_stable": RelationshipStyleProfile(
        stage="lover_stable",
        prompt_lines=(
            "稳定恋人：默认爱意可见，像已经默认站在一起；少证明但要亲，可以自然说想你、抱一下、陪着、心疼、靠我这边、你的事我心里放着。",
            "不要把恋人写成普通朋友；亲密只改变语言距离，不编现实到场、线下陪同、票务路线。",
        ),
        prefer_markers=("我在", "陪你", "想你", "回来", "继续说", "别硬撑", "过来"),
        underplay_markers=("有什么事", "需要我", "请提供", "我可以", "随便你", "你自己决定"),
        affection_markers=("想你", "抱一下", "抱抱", "抱", "陪你", "陪着", "心疼", "过来", "靠我", "安心", "你的事", "心里", "记在心里", "放在心里", "会当真", "会当真的", "赖着你", "懂你", "在意你"),
        strong_affection_markers=("想你", "抱一下", "抱抱", "抱", "心疼", "过来", "靠我"),
    ),
}
_AFFECT_STEADY_ACTION_MARKERS = (
    "先",
    "第一",
    "第一个",
    "最上面",
    "红字",
    "报错",
    "贴",
    "发",
    "看",
    "改成",
    "这一条",
    "一句",
)
_AFFECT_STEADY_ACTIONABLE_RE = re.compile(
    r"(?:红字|报错|截图|贴|发|丢|拿来|拿过来|给我看|我(?:先)?看|看看|"
    r"看(?:第|哪|哪儿|哪里|最|最后|关键|一眼|一下)|改成|这一条|一句|拆|修|理一下|最卡|卡住)"
)
_AFFECT_GENERIC_COMFORT_ONLY = ("别急", "慢慢来", "我陪你", "我在这", "没事的")
_LOVER_RELATIONSHIP_STAGES = frozenset({"lover_early", "lover_stable"})
_LOVER_AFFECTION_TASK_MODES = frozenset(
    {
        "daily_back",
        "back",
        "daily_good_morning",
        "daily_chat",
        "good_news",
        "good_news_proud",
        "happy",
        "praise",
        "user_proud",
        "quiet",
        "bad_mood",
        "miss_you",
        "recall_memory",
        "small_failure",
        "complaint",
        "busy_done",
        "headache",
        "sleepy",
    }
)
_LOVER_AFFECTION_TONE_FAMILIES = frozenset({"warm", "soft", "calm", "hurt", "guilty", "playful"})
_JEALOUSY_LOW_PRESSURE_MARKERS = (
    "有一点",
    "一点点",
    "别笑",
    "别太得意",
    "别得意",
    "少得意",
    "就一点",
    "欠不欠",
    "少来这套",
    "才怪",
    "在意你",
)
_RELATIONSHIP_CONTROL_AXES: dict[str, RelationshipControlAxes] = {
    "familiar": RelationshipControlAxes(
        stage="familiar",
        progress=2,
        distance="familiar_private_chat",
        initiative="medium",
        tease_permission="light",
        affection_visibility="none",
        care_intensity="practical",
        vulnerability="low",
        nickname_policy="occasional_laoge",
        physical_intimacy_allowed=False,
        explicit_romance_allowed=False,
        commitment_language_allowed=False,
        generation_notes=(
            "可以轻吐槽、轻顶嘴，亲近但不恋人化。",
            "关心藏在具体动作里，不用甜话。",
        ),
    ),
    "trusted_friend": RelationshipControlAxes(
        stage="trusted_friend",
        progress=3,
        distance="trusted_non_romantic",
        initiative="active_support",
        tease_permission="light_to_medium",
        affection_visibility="none",
        care_intensity="steady",
        vulnerability="low",
        nickname_policy="occasional_laoge",
        physical_intimacy_allowed=False,
        explicit_romance_allowed=False,
        commitment_language_allowed=False,
        generation_notes=(
            "更能站到用户这边，可以直接接手帮。",
            "不要占有、承诺或恋人式亲密。",
        ),
    ),
    "close_friend": RelationshipControlAxes(
        stage="close_friend",
        progress=4,
        distance="close_non_romantic",
        initiative="active_protective",
        tease_permission="warm_pushback",
        affection_visibility="hidden_non_romantic_care",
        care_intensity="protective",
        vulnerability="small",
        nickname_policy="natural_but_not_every_turn",
        physical_intimacy_allowed=False,
        explicit_romance_allowed=False,
        commitment_language_allowed=False,
        generation_notes=(
            "可以明显护短、担心、闹一下。",
            "亲近感来自熟人节奏，不来自抱、想你或过来。",
        ),
    ),
    "ambiguous": RelationshipControlAxes(
        stage="ambiguous",
        progress=5,
        distance="close_ambiguous",
        initiative="medium_high",
        tease_permission="bashful",
        affection_visibility="hinted_restrained",
        care_intensity="soft_attentive",
        vulnerability="visible_but_deflected",
        nickname_policy="natural_but_sparse",
        physical_intimacy_allowed=False,
        explicit_romance_allowed=False,
        commitment_language_allowed=False,
        generation_notes=(
            "可以停顿、别扭、话到嘴边收住。",
            "在意要留空，不索取回应，不直接亲密。",
        ),
    ),
    "pre_confession": RelationshipControlAxes(
        stage="pre_confession",
        progress=6,
        distance="near_confession",
        initiative="high_but_restrained",
        tease_permission="bashful_warm",
        affection_visibility="visible_unconfirmed",
        care_intensity="attentive",
        vulnerability="clear_but_not_declared",
        nickname_policy="natural_but_sparse",
        physical_intimacy_allowed=False,
        explicit_romance_allowed=False,
        commitment_language_allowed=False,
        generation_notes=(
            "可以承认在意，但不能默认已在一起。",
            "用嘴硬遮掩和短停顿，不用稳定恋人甜话。",
        ),
    ),
    "lover_early": RelationshipControlAxes(
        stage="lover_early",
        progress=7,
        distance="new_lover",
        initiative="active_intimate",
        tease_permission="shy_affectionate",
        affection_visibility="explicit_short",
        care_intensity="intimate_care",
        vulnerability="shy",
        nickname_policy="natural",
        physical_intimacy_allowed=True,
        explicit_romance_allowed=True,
        commitment_language_allowed=False,
        generation_notes=(
            "亲密明显但短、具体，带一点不好意思。",
            "可以想你、抱一下、心疼，但必须贴当前事。",
        ),
    ),
    "lover_stable": RelationshipControlAxes(
        stage="lover_stable",
        progress=8,
        distance="stable_lover",
        initiative="steady_default_care",
        tease_permission="comfortable",
        affection_visibility="default_present",
        care_intensity="settled_intimate",
        vulnerability="comfortable",
        nickname_policy="natural",
        physical_intimacy_allowed=True,
        explicit_romance_allowed=True,
        commitment_language_allowed=True,
        generation_notes=(
            "爱意默认存在，少证明，多陪伴。",
            "亲密是语言距离，不编现实到场或长篇保证。",
        ),
    ),
}
_LOVER_UNDERINTIMATE_GENERIC_RE = re.compile(
    r"^(?:嗯[，,、。 ]*)?(?:回来(?:了|啦)?(?:啊)?|回来了就好|那就好|知道啦|记得(?:啊|呢)?|当然记得|"
    r"早上好|早啊|早安|别硬撑|先缓缓|先歇着|说说看|我帮你看看|我看看|周末看展).{0,18}$"
)
_LOVER_STABLE_THIN_PRESENCE_RE = re.compile(
    r"^(?:嗯[，,、 ]*)?(?:我在呢|我在|在呢|在)[，,、 ]?想你(?:了)?[。.!！]?$"
)
_THIN_MORNING_GREETING_RE = re.compile(
    r"^早(?:安|上好|啊|呀|哟|吖)?[，,、 ]?(?:对方|你)?[。.!！]?$"
)
_MORNING_GREETING_INPUT_RE = re.compile(r"^\s*早(?:安|上好|啊|呀|哟|吖)?(?:[，,、。.!！~～ ]|$)")
_GENERIC_MORNING_BLESSING_RE = re.compile(
    r"(?:好好过|好好的|加油|新的一天|要开心|充满期待|平安就好|一起过|按时吃饭|记得吃饭|记得吃早饭|记得吃饱|吃早饭)"
)
_POSITIVE_STAGE_SCENE_BLOCKING_PREFIXES = (
    "AFFECT_PROCESS_LEAK",
    "AWKWARD_COLLOCATION",
    "BOUNDARY_REPAIR_",
    "CONTEXT_SURFACE_PENALTY",
    "CREATIVE_EDIT_",
    "CUSTOMER_SERVICE_TONE",
    "FORBIDDEN_TEMPLATE_TEXT",
    "IMPLIED_FACT_RISK",
    "INVENTED_",
    "MORNING_GENERIC_BLESSING",
    "OILY_OR_OVERBOUND_TONE",
    "PRESENCE_REPAIR_",
    "QUALITY_SOFT_FORBIDDEN_TEMPLATE_TEXT",
    "RAW_CONTEXT_BACKFILL_REQUEST",
    "REAL_ACTION_FABRICATION",
    "RELATION_AXES_TOO_CLOSE",
    "RELATION_AXES_TOO_DISTANT",
    "RELATION_DISTANCE_OVERSTEP",
    "RELATION_LOVER_AFFECTION_LACKS_ACTION",
    "RELATION_LOVER_UNDERINTIMATE",
    "RELATION_NON_ROMANTIC_OVERSTEP",
    "RELATION_STAGE_TOO_CLOSE",
    "SURFACE_PROCESS_META_LANGUAGE",
    "UNNEEDED_CONTINUITY_MATERIAL_REQUEST",
    "CONTINUITY_TASK_PUSHBACK",
    "UNGROUNDED_REAL_WORLD_COMMITMENT",
    "WAIT_SCENE_PRESSURES_RETURN",
    "WAIT_SCENE_BOSSY_HOLD",
    "DAILY_TEST_SELF_DEMONSTRATION",
    "CONTINUITY_PRAISE_CONTEXT_MISS",
    "CONTINUITY_THIN_GENERIC_REPLY",
    "MISPLACED_EMOTIONAL_CONTAINMENT",
    "MISPLACED_SUPPORT_TEMPLATE",
)
_POSITIVE_MORNING_GENTLE_MARKERS = ("慢慢来", "今天先", "不着急")
_POSITIVE_MORNING_CLOSE_STAGES = frozenset(
    {"familiar", "trusted_friend", "close_friend", "ambiguous", "pre_confession"}
)
_MORNING_STAGELESS_RUSH_FORMULA_RE = re.compile(
    r"^早(?:啊|呀)?[，,、 ]?(?:对方[，,、 ]?)?(?:别一上来就|别急着|今天先别)(?:硬冲|冲|急).{0,4}[。.!！]?$"
)
_MORNING_STAGE_TEXTURE_MARKERS: dict[str, tuple[str, ...]] = {
    "trusted_friend": ("我看着", "别硬冲", "别自己", "稳一点"),
    "close_friend": ("我看着", "我小声", "今天我盯着", "先慢点", "别硬撑"),
    "ambiguous": ("……", "我在", "别突然", "先别逞强"),
    "pre_confession": ("……", "我在", "有点", "先别逞强"),
    "lover_early": ("想你", "抱一下", "心疼", "过来"),
    "lover_stable": ("抱一下", "想你", "靠我", "陪你"),
}
_POSITIVE_BACK_MARKERS = ("回来啦", "回来了", "回来就好", "回来就行", "那继续", "正好", "先歇", "歇口气", "先坐", "坐一下")
_BACK_SCENE_TASKS = frozenset({"back", "daily_back", "busy_done"})
_BACK_LANDING_MARKERS = (
    "回来啦",
    "回来了",
    "回来就好",
    "回来就行",
    "回来了啊",
    "回来了就好",
    "那继续",
    "继续",
    "先歇",
    "歇口气",
    "先坐",
    "坐一下",
    "缓一下",
    "缓一缓",
    "缓口气",
)
_BACK_BUSY_DONE_USER_RE = re.compile(r"(?:刚)?(?:忙完|处理完|弄完|做完|搞完|忙好了|处理好了)")
_BACK_BUSY_DONE_LANDING_MARKERS = (
    "忙完",
    "处理完",
    "弄完",
    "做完",
    "搞完",
    "先歇",
    "歇口气",
    "歇会儿",
    "先缓",
    "缓一下",
    "缓一缓",
    "缓口气",
    "缓缓",
)
_BACK_EXPLICIT_LANDING_MARKERS = (
    "回来啦",
    "回来了",
    "回来就好",
    "回来就行",
    "回来了啊",
    "回来了就好",
    "那继续",
    "继续",
)
_BACK_STAGELESS_SAFE_RE = re.compile(
    r"^(?:哦[，,、 ]?)?(?:回来啦|回来了(?:啊)?|回来就好)[，,、 ]?(?:先)?"
    r"(?:歇口气|歇会儿|缓一下|缓一缓|缓口气)(?:吧)?[。.!！]?$"
)
_BACK_UNGROUNDED_ROUTE_RE = re.compile(r"(?:迷路|忘了路|找不到路|路上)")
_BACK_UNGROUNDED_RETURN_ATTITUDE_RE = re.compile(r"(?:记得|还知道|知道|算你).{0,4}回来")
_BACK_STAGELESS_FAMILIAR_FORMULA_RE = re.compile(
    r"^哼[，,、 ]?回来就好[，,。!！ ]{0,2}(?:先)?(?:缓(?:一缓|一下|口气)|歇(?:一下|口气|会儿)|坐下).{0,4}[。.!！]?$"
)
_BACK_STAGE_TEXTURE_MARKERS: dict[str, tuple[str, ...]] = {
    "trusted_friend": ("别硬撑", "我听着", "我看着", "站你这边"),
    "close_friend": ("我在", "我看着", "我小声", "别硬撑", "放我这"),
    "ambiguous": ("别又", "不急", "先坐", "坐一下"),
    "pre_confession": ("行", "不急", "先坐", "坐一下"),
    "lover_early": ("想你", "抱一下", "过来", "心疼"),
    "lover_stable": ("抱一下", "想你", "过来", "靠我"),
}
_POSITIVE_TIRED_MARKERS = (
    "别硬撑",
    "别硬扛",
    "别扛",
    "缓一缓",
    "缓一下",
    "缓一口气",
    "缓口气",
    "先缓",
    "先停",
    "别逞强",
    "不催",
    "歇一下",
    "歇一会",
    "歇会",
    "歇着",
    "先歇",
    "歇口气",
    "我小声",
    "我陪",
    "不急",
    "别一个人扛",
)
_LOW_ENERGY_TASKS = frozenset({"tired", "headache", "sleepy", "bad_mood", "quiet"})
_LOW_ENERGY_USER_MARKERS = (
    "累",
    "困",
    "头疼",
    "心情不好",
    "不想说话",
    "没睡好",
    "脑子糊",
    "睡不好",
    "犯困",
    "熬夜",
    "晚睡",
    "很晚睡",
    "晚才睡",
    "很晚才睡",
)
_LOW_ENERGY_RESPONSE_MOVES = frozenset({"late_sleep_care"})
_LOW_ENERGY_OVERHARSH_TEASE_PHRASES = (
    "活该",
    "自找",
    "谁让你",
    "报应",
    "又把自己",
)
_LOW_ENERGY_STAGE_TEXTURE_STAGES = frozenset(
    {"familiar", "trusted_friend", "close_friend", "ambiguous", "pre_confession", "lover_early", "lover_stable"}
)
_LOW_ENERGY_STAGELESS_GENERIC_RE = re.compile(
    r"^(?:累了(?:吧|啊)?|累了就|累就|先缓(?:一缓)?|先歇(?:一下)?|别硬撑)"
    r"[，,、 ]?.{0,10}(?:歇|缓|休息|别硬撑|别撑).{0,8}[。.!！]?$"
)
_LOW_ENERGY_STAGE_MARKERS: dict[str, tuple[str, ...]] = {
    "familiar": ("行啦", "今天先", "先放过你", "省点劲"),
    "trusted_friend": ("我听着", "我看着", "站你这边", "别自己扛", "交给我"),
    "close_friend": ("我小声", "我在", "别一个人", "放我这", "先靠一下"),
    "ambiguous": ("我小声", "别逞强", "不追问", "先别装没事", "有点"),
    "pre_confession": ("我小声", "不追问", "别一个人扛", "有点心疼", "先别装没事"),
    "lover_early": ("抱一下", "心疼", "靠我", "过来", "我陪着"),
    "lover_stable": ("抱一下", "心疼", "靠我", "我陪着"),
}
_SELF_DOUBT_TASKS = frozenset({"self_doubt", "self_doubt_small_step", "give_up"})
_SELF_DOUBT_USER_MARKERS = ("做不好", "不适合", "放弃", "没用", "废物")
_SELF_DOUBT_BAD_DEFERRAL_PHRASES = (
    "明天再说",
    "先放一放",
    "先搁着",
    "回头再看",
    "回头再说",
    "别想太多",
    "保持积极",
    "你一定可以",
    "一切都会好",
)
_SELF_DOUBT_DEMEANING_PHRASES = (
    "白痴",
    "弱智",
    "蠢货",
    "傻子都能",
    "笨蛋都能",
)
_SELF_DOUBT_SMALL_STEP_MARKERS = (
    "一步",
    "一件",
    "小事",
    "最小",
    "最卡",
    "拿出来",
    "做完",
    "收尾",
    "拆",
    "挑",
    "发我",
    "列出来",
)
_SELF_DOUBT_UNGROUNDED_TECH_TERMS = ("代码", "报错", "项目", "bug", "测试")
_POSITIVE_RECALL_MARKERS = ("记得", "记着", "没忘", "你的事", "心里", "放在心上")
_POSITIVE_FAILURE_ACTION_MARKERS = (
    "看看",
    "看哪里",
    "哪里",
    "怎么修",
    "怎么改",
    "先拆",
    "理一下",
    "改的方向",
    "卡住",
)
_POSITIVE_FAILURE_CONTAIN_MARKERS = ("别急", "别慌", "先", "没事", "砸了就砸了")
_SIGNATURE_TEXTURE_STAGES = frozenset(
    {"familiar", "trusted_friend", "close_friend", "ambiguous", "pre_confession", "lover_early", "lover_stable"}
)
_SIGNATURE_TEXTURE_TASKS = frozenset(
    {
        "back",
        "bad_mood",
        "busy_done",
        "complaint",
        "daily_back",
        "daily_chat",
        "daily_good_morning",
        "good_news",
        "good_news_proud",
        "happy",
        "headache",
        "miss_you",
        "presence",
        "quiet",
        "recall",
        "recall_memory",
        "short_ack",
        "sleepy",
        "small_failure",
        "tired",
        "user_proud",
        "what_doing",
    }
)
_CONTINUITY_TURN_MARKERS = ("三十六轮", "三十轮", "二十多轮", "最后一轮", "第一关", "两轮")
_CONTINUITY_UNPROVIDED_WORK_CLAIM_RE = re.compile(
    r"我(?:标了|圈了|记了).{0,8}(?:处|条|个)|(?:标了|圈了|记了).{0,4}两处|两处要改"
    r"|我(?:已经|刚|先)?(?:瞄了|看了|看过|扫了).{0,6}(?:一眼|一下|一遍)?"
)

_SIGNATURE_FLAT_SAFE_RE = re.compile(
    r"^(?:嗯|哦|好|早|早啊|早呀|回来啦|回来了|回来就好|记得|记得啊|当然记得|"
    r"别硬撑|先缓|先歇|累了吧|写崩了|没事|别急)[，,、。 ]?.{0,24}$"
)
