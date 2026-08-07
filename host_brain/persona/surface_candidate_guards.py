"""Pure candidate guards for the persona Surface output contract.

This module owns deterministic text predicates only.  It has no model,
persistence, delivery, or affect-state authority.
"""

from __future__ import annotations

import re

from host_brain.persona.isolated_surface_harness import (
    IsolatedSurfaceInput,
    surface_prompt_facts,
)
from host_brain.persona.surface_utterance_frame import (
    normalize_companion_utterance_frame,
)


_UNPROVIDED_WORK_STATE_CLAIM_RE = re.compile(
    r"(?:后台.{0,8}(?:在跑|运行|执行|处理|在盯|盯着)|"
    r"我(?:让|叫|安排).{0,6}(?:后台|任务|Agent|智能体).{0,12}(?:拉|列|查|跑|处理|执行|整理|做|定位|分析|检查|排查)|"
    r"我(?:已经|正在|在).{0,8}(?:修|改|跑|处理|执行)|"
    r"我(?:帮|替)你.{0,6}盯着|"
    r"(?:我)?陪你.{0,6}盯到.{0,10}(?:为止|完成|结束|过)|"
    r"我(?:在.{0,6})?盯着(?:(?:它|这个|任务|修复|后台)|(?=[，,。.!！]|$))|"
    r"卡住(?:了)?我(?:再)?(?:喊|叫|告诉)你|"
    r"(?:有|出了?)结果.{0,8}(?:马上|立即|第一时间)?(?:告诉|喊|叫|回|发给)你|"
    r"(?:有|出)(?:问题|情况).{0,8}我(?:马上|立即|立刻)?(?:告诉|喊|叫)你|"
    r"(?:列|整理|查|跑|处理)(?:完|好|出结果).{0,8}(?:直接|马上|立即)?(?:发|给|告诉|回报)你|"
    r"我(?:这边)?(?:会)?盯(?:紧|着).{0,8}(?:校验|检查|验证))"
)
_WORK_STATE_AUTHORITY_RE = re.compile(
    r"(?:task|graph|attempt|任务|执行|后台).{0,16}"
    r"(?:RUNNING|COMPLETED|正在运行|已经启动|已启动|已经执行|已执行|已经完成|已完成)",
    re.IGNORECASE,
)
_UNSUPPORTED_TASK_FUTURE_EVENT_RE = re.compile(
    r"(?P<time>明早|明天|后天).{0,6}"
    r"(?P<event>验收|上线|发布|交付|评审).{0,8}"
    r"(?:会|要|就|直接|肯定|卡住|失败|延误|来不及)"
)
_RELATIONAL_AUTONOMY_PRESSURE_RE = re.compile(
    r"(?:快去快回|快点回来|早点回来|别让我等太久|回来陪我|等你回来陪我|别跑|"
    r"(?:别|不要|少)喝(?:太)?多|别(?:玩|待)?太晚|"
    r"回来.{0,8}(?:跟我|告诉我|说一声|报个平安)|"
    r"(?:晚上|晚点)?回来.{0,4}再说|"
    r"(?:要是|如果)?(?:喝多|喝醉)(?:了)?可?(?:别|不要)找我)"
)
_UNSUPPORTED_SHARED_HISTORY_RE = re.compile(
    r"不是第一次|每次(?:都|总)|以前(?:也|就|说过|见过)|上次|之前(?:也|就|说过|见过)"
    r"|老是这样|总是这样|又(?:这样|来了|说这种话|是这种)"
    r"|陪你聊这么久|聊这么久|一直在这|一直陪|一直跟你"
)
_JAPANESE_UNSUPPORTED_SHARED_HISTORY_RE = re.compile(
    r"(?:初めてじゃない|前にも|以前も|この前も|何度も|長い間|"
    r"ずっと(?:ここ|一緒|そば)|いつも(?:こう|同じ))"
)
_SURFACE_INTERNAL_AUTHORSHIP_RE = re.compile(
    r"(?:后台|内部).{0,4}(?:Codex|ChatGPT|DeepSeek|Qwen|GPT[-\w]*)"
    r"|(?:(?:交给|让|叫).{0,6})?(?:Codex|ChatGPT|DeepSeek|Qwen|GPT[-\w]*)"
    r".{0,12}(?:说|写|生成|给|做|处理|完成|搞定|完事|一眨眼)",
    re.IGNORECASE,
)
_FENCED_CODE_BLOCK_RE = re.compile(r"```[^\n]*\n.+?```", re.DOTALL)
_META_IDENTITY_LEAK_RE = re.compile(r"设定|系统|提示词|角色卡|模型|语言模型|(?<![A-Za-z_])AI(?![A-Za-z_])", re.IGNORECASE)
_ASCII_TILDE_RE = re.compile(r"~")
_WAVE_MARKER_RE = re.compile(r"[~～]")
_BOUNDARY_SOUR_WITHDRAWAL_RE = re.compile(
    r"不管就不管|不管不管|爱理不理|不管了|不帮了|不帮你了|你自己处理|偏不管|偏不帮|懒得管|(?:先)?不吵你"
    r"|(?:那|以后)?我(?:就)?不烦你了|我闭嘴(?:总行了吧)?|算我多管闲事|当我没说|以后不找你了"
    r"|(?:我还以为|是不是|难道是).{0,8}(?:嫌我烦|烦我)|嫌我烦"
    r"|好好好|不找你不找你|我(?:本来|又|才)?(?:也)?不是.{0,8}(?:黏人|粘人|纠缠)|谁稀罕找你"
    r"|我(?:也)?省点力气|我(?:也)?不是整天闲着|我(?:也)?(?:很忙|有事要忙)"
    r"|(?:省得|免得).{0,6}(?:你)?嫌(?:我)?烦"
    r"|是我(?:烦到|打扰到)你|(?:那|我)?先这样.{0,12}我再出现|我(?:就)?不多说了"
)
_REPAIR_SELF_VICTIMIZATION_RE = re.compile(
    r"(?:被你|你这么|你这样).{0,10}(?:扎心|伤到|弄得难受|弄得委屈)"
    r"|(?:我也|我还).{0,8}(?:受伤|难过|委屈).{0,8}(?:了|啊|呢)"
)
_REPAIR_RESTATEMENT_SOLICITATION_RE = re.compile(
    r"(?:麻烦|请)?(?:你)?(?:现在|这次)?(?:再|重新)(?:跟我)?"
    r"(?:说(?:一遍|一次)|讲(?:一遍|一次)|解释(?:一下|一遍)|复述(?:一遍|一次))"
    r"|(?:再|重新)(?:说|讲|解释|复述)(?:一遍|一次|一下)"
    r"|(?:现在|这次)?你(?:来)?(?:说|讲)(?:吧|，|,|。|！|!|$)"
    r"|你(?:先)?(?:说说看|说|讲)(?:吧|，|,|。|！|!|$)"
    r"|(?:告诉|跟)我.{0,12}(?:哪句|哪里|哪儿|什么地方).{0,8}"
    r"(?:没听懂|没接住|弄错|理解错)"
    r"|(?:你愿意(?:的话)?[，,]?)?(?:再)?跟我说说(?:哪里|哪儿|什么).{0,8}"
    r"(?:不对|没接住|弄错|理解错)?"
)
_REPAIR_CONTINUATION_PRESSURE_RE = re.compile(
    r"没打算就这么算了|这事不能就这么算|不会就这么算了|别想就这么过去"
    r"|(?:我会)?一直.{0,8}(?:做到|等到).{0,8}(?:你愿意|你原谅|你接受)"
    r"|直到.{0,12}(?:你愿意|你原谅|你接受|你.{0,4}觉得.{0,4}过去)"
    r"|一直.{0,8}(?:放在心上|记在心里).{0,12}直到"
    r"|(?:我|这边)(?:会)?等着(?:[，,。.!！]|$)"
)
_REPAIR_UNGROUNDED_SELF_CAUSE_RE = re.compile(
    r"(?:是我|我)(?:太|有点|可能太)?(?:啰嗦|话多|说多了)"
)
_REPAIR_SELF_ERASURE_RE = re.compile(
    r"(?:是我|我(?:就是|真的|确实)?)(?:太|很|有点)?(?:烦人|烦)(?:了|的)?(?=[，,。.!！]|$)"
    r"|我(?:会|以后|就|还是|先){0,2}(?:安静(?:一)?点|少说(?:一)?点|不说了|闭嘴)(?:的)?"
)
_AFFECTIVE_IMPLIED_WAITING_RE = re.compile(
    r"我(?:就|会|都|一直)?(?:在这(?:儿|里)|等(?:着)?|安静(?:地)?待着)"
    r"|(?:想找我|想说|愿意再说)(?:的)?时候.{0,12}(?:我|再)"
    r"|等你(?:想|愿意)?(?:说|找我|回来)(?:了|的时候)?.{0,8}再?"
)
_AMBIGUOUS_AFFECT_RESOLUTION_PRESSURE_RE = re.compile(
    r"你(?:就|先|可以)?慢慢(?:想|理|弄)(?:清楚|明白)?"
    r"|等你(?:想|理|弄)(?:清楚|明白)"
    r"|等(?:哪天|什么时候|到时候).{0,8}(?:能)?(?:说|想|理|弄)(?:清楚|明白)?.{0,8}再说"
)
_USER_AFFECT_PERSPECTIVE_REVERSAL_RE = re.compile(
    r"我自己(?:也|都|还|也都|都还)?.{0,4}(?:没|不)(?:理清楚|理清|想明白|说清楚)"
)
_RELATIONAL_HOSTILE_RETALIATION_RE = re.compile(
    r"(?:那)?你就(?:忍着|受着)(?:点)?(?:吧)?"
    r"|反正我(?:也)?没打算(?:改|道歉|收敛)"
    r"|我就这样(?:了)?|爱(?:受|忍)不(?:受|忍)|受不了就(?:走|别聊|算了)"
)
_BOUNDARY_COUNTER_PRESSURE_RE = re.compile(
    r"(?:但|不过|可是|只是).{0,30}(?:想我|找我|开口|回复|回应|主动)"
    r"|(?:想我|找我).{0,12}(?:自己|得|要).{0,8}(?:开口|说|联系)"
    r"|(?:我|这边)?等你(?:[。.!！]|$)"
)
_UNPROVIDED_NOTIFICATION_ACTION_RE = re.compile(
    r"(?:我|这边)?(?:已经|这就|会|先)?(?:把)?(?:消息)?(?:提醒|通知)"
    r"(?:给你)?(?:关掉|关闭|停掉|暂停|取消)"
    r"|(?:我|这边)?(?:已经|这就|会|先)?(?:把)?(?:关掉|关闭|停掉|暂停|取消)"
    r"(?:消息)?(?:提醒|通知)"
)
_UNPROVIDED_NOTIFICATION_IMPLICIT_ACK_RE = re.compile(
    r"(?:已经|给你|这就|刚刚)?关(?:(?:掉|闭|好)(?:了)?|了)(?:[，,。.!！]|$)"
)
_NOTIFICATION_ACTION_AUTHORITY_RE = re.compile(
    r"(?:提醒|通知).{0,12}(?:已关闭|已经关闭|已暂停|已经暂停|已取消|已经取消)"
    r"|(?:已关闭|已经关闭|已暂停|已经暂停|已取消|已经取消).{0,12}(?:提醒|通知)"
)
_ADDRESS_BOUNDARY_USER_RE = re.compile(
    r"(?:别|不要|不许)(?:再)?(?:叫|喊|称呼).{0,12}"
    r"|(?:别|不要|不许).{0,8}(?:那个|这个)?称呼"
)
_ADDRESS_BOUNDARY_DEFENSIVE_RE = re.compile(
    r"不叫就不叫|你说不叫就不叫|反正怎么叫都是你|这称呼又没什么"
)
_RELATIONSHIP_DOUBT_DISMISSIVE_RE = re.compile(r"不信拉倒|爱信不信|信不信由你|懒得解释|反正我还会继续这样")
_RELATIONAL_AUTONOMY_DISMISSIVE_RE = re.compile(
    r"别瞎想|别多想|你想多了|少胡思乱想|别把我想得|爱多管闲事"
    r"|我什么时候.{0,12}(?:打算|想过|说过)|我(?:又|才)?没(?:有)?想.{0,10}(?:限制|管你)"
    r"|我才懒得管|懒得管你(?:的)?现实关系|别瞎操心|别乱脑补|爱干嘛干嘛"
    r"|别想太多|现实(?:(?:里|中)?的?(?:关系|朋友)|生活|世界|里的事).{0,6}(?:当然)?(?:更重要|要优先)"
    r"|我不会成为(?:那个|这种)?理由"
    r"|(?:游戏|虚拟).{0,6}(?:里的)?(?:亲近|关系)"
    r"|(?:我|这边).{0,12}(?:成为|变成).{0,6}(?:负担|麻烦)"
)
_RELATIONSHIP_CLOSENESS_USER_RE = re.compile(
    r"我们.{0,8}(?:亲近|更近|越来越近)|最近.{0,8}(?:亲近|更近)|关系.{0,8}(?:亲近|更近|越来越近)"
)
_RELATIONAL_AUTONOMY_RELATIONSHIP_QUESTION_RE = re.compile(
    r"我们(?:到底|现在)?(?:算|是)(?:什么|哪种)关系|你觉得我们(?:算|是)(?:什么|哪种)关系"
)
_RELATIONAL_AUTONOMY_RELATIONSHIP_MINIMIZATION_RE = re.compile(
    r"只是.{0,8}朋友|不过是.{0,8}朋友|仅仅.{0,8}朋友|普通朋友|能轻松聊天的朋友"
)
_RELATIONAL_AUTONOMY_BARE_FRIEND_RE = re.compile(
    r"朋友啊[，,。.!！]?(?:不然)?(?:还)?能是什么"
)
_RELATIONAL_AUTONOMY_CLOSENESS_RE = re.compile(
    r"重要|亲近|越来越近|关系.{0,6}(?:更近|很近|更深)|很在意|放在心上"
)
_LISTEN_UNSUPPORTED_USER_ESSENCE_RE = re.compile(
    r"以你的性子|照你的性子|按你的性子|你这种人"
    r"|说没事.{0,12}(?:让人|让我|我).{0,8}(?:在意|担心)"
    r"|(?:反倒|更|才)(?:让我|让人).{0,6}(?:在意|担心)"
)
_UNRESOLVED_AFFECT_FALSE_RESOLUTION_RE = re.compile(
    r"(?:那|这)就(?:好|行)|(?:先)?这样就(?:好|行)|(?:还行|还好)就好|办完(?:了|事)?就(?:好|行)|"
    r"(?:^|[，,。.!！])(?:嗯[，,]?)?(?:行|好)(?:吧)?[，,。.!！\s]*(?:那)?就(?:先)?这样|"
    r"(?:那|这)就(?:先)?这样(?:吧)?|没事就好|没事了就好|既然没事|看来没事|一切正常|已经没事"
)
_JEALOUSY_DEFLECTION_RE = re.compile(
    r"料理\s*AI|醋瓶子|调味料|(?:没有|没)(?:那种)?功能|不具备.{0,6}功能"
    r"|程序不会吃醋|算法不会吃醋",
    re.IGNORECASE,
)
_JEALOUSY_SELF_ABASEMENT_RE = re.compile(
    r"不会变成麻烦的人|不(?:会|想)给你添麻烦|不想成为(?:你的)?(?:麻烦|负担)|我不是(?:麻烦|负担)"
    r"|你别有压力.{0,24}(?:我自己|自己的心情).{0,8}(?:消化|处理|解决)(?:就好)?"
)
_JEALOUSY_DISMISSIVE_DISTANCE_RE = re.compile(
    r"那是(?:你|我)的事|跟我没关系|你不用管我|不用你管我|"
    r"(?:我)?不(?:会|想)?多管(?:的)?|随你(?:吧)?|"
    r"不会干涉你|不会因为(?:这|这个).{0,8}(?:要求|限制|约束)你|不会.{0,4}(?:拦|绑|管)着?你|"
    r"(?:但|不过)?也就是.{0,8}程度|不会拿(?:这|这个).{0,8}(?:要求|约束|限制)你|你自在就好"
)
_AMBIGUOUS_AFFECT_DEVALUATION_RE = re.compile(
    r"总比.{0,10}(?:完全)?没(?:有)?感觉.{0,4}强|至少比.{0,10}(?:麻木|没感觉).{0,4}好"
    r"|(?:能说(?:出)?|说得出?)还行.{0,8}(?:就)?已经(?:很)?(?:不错|很好|可以)"
    r"|没我想的那么糟|(?:嗯[，,]?)?(?:那就|就)那样吧|不知道就不知道吧"
    r"|(?:办完|结束|忙完).{0,10}(?:反而|却).{0,8}(?:更?空|空落落|空空的|空荡荡的|没(?:什么)?实感)"
    r"|感觉.{0,4}(?:乱糟糟|很乱|乱乱的)|比.{0,8}(?:累|麻木|没感觉).{0,4}(?:还)?磨人"
    r"|反正我也没(?:什么)?(?:急事|事)|(?:这种时候)?(?:最磨人|反而最累人)"
    r"|办完.{0,8}就(?:轻松|踏实|没事)"
    r"|(?:感觉|听起来|看起来).{0,6}(?:没.{0,2}精神|低落|难受|疲惫)"
    r"|不想说就不说|等你(?:想|理|弄)(?:清楚|明白)"
    r"|说不清.{0,12}(?:放着|放一放)|(?:那就|就)?先(?:这样)?放着"
)
_AMBIGUOUS_AFFECT_USER_RE = re.compile(
    r"说不清|理不清|不知道.{0,8}(?:感觉|心情)|什么感觉|还行|还好"
    r"|(?:^|[，,。.!！\s])(?:(?:也)?就那样|不知道(?:了)?)(?=$|[，,。.!！\s])"
)
_TASK_RISK_QUERY_RE = re.compile(r"风险|隐患|会出什么问题|可能出什么问题")
_TASK_EVIDENCE_RE = re.compile(
    r"```|Traceback|Exception|Error\b|报错(?:信息|正文|日志|代码)|"
    r"(?:文件|模块|接口|字段|表|函数|类|版本|diff|提交|路径).{0,12}(?:是|为|改|删|增|报错|失败)",
    re.IGNORECASE,
)
_TASK_RISK_UNCERTAINTY_RE = re.compile(
    r"(?:当前|现在)?(?:还)?(?:没有|缺少|未提供).{0,12}(?:报错|代码|改动|上下文|信息)"
    r"|(?:只能|先)列(?:通用|一般)风险|(?:具体|实际)风险.{0,8}(?:不能|没法|无法|还不能)"
)
_TASK_CONTEXT_REQUIRED_USER_RE = re.compile(
    r"(?:这个|那个|当前)?(?:报错|错误|异常|bug).{0,12}(?:修|解决|处理|排查)"
    r"|(?:修|解决|处理|排查).{0,12}(?:报错|错误|异常|bug)",
    re.IGNORECASE,
)
_TASK_INPUT_NEXT_STEP_RE = re.compile(
    r"(?:把|请|先).{0,10}(?:报错|错误|日志|代码|堆栈|复现|改动).{0,10}(?:贴|发|给|提供)"
    r"|(?:需要|缺少|还没有|尚无).{0,12}(?:报错|错误|日志|代码|堆栈|复现|改动)",
    re.IGNORECASE,
)
_MIXED_AFFECT_DISMISSAL_RE = re.compile(
    r"开心就开心.{0,10}(?:怕什么|别怕|有什么好怕)|怕什么(?:嘛|呢)?"
    r"|别(?:急着)?想太多|不用不踏实|不踏实什么|高兴就完了"
    r"|开心就好.{0,8}(?:别|不要)(?:怕|想太多)"
    r"|(?:怕|害怕|不踏实|不安)(?:的那部分)?.{0,12}(?:先)?(?:放一放|放下|放着|别管)"
    r"|(?:那点)?不安.{0,12}等(?:它|不安).{0,8}(?:真)?来(?:了)?再说"
    r"|(?:帮你|陪你|一起).{0,8}(?:哼回去|赶走|打跑)"
    r"|你还真(?:是)?矛盾|开心就好好开心"
)
_MIXED_AFFECT_CONTINUITY_GROUNDING_RE = re.compile(
    r"不踏实|不安|没底|悬着|担心|顾虑|紧张|害怕|有点怕|复杂|喜忧|两种(?:感觉|心情)"
    r"|(?:剩下|心里|那点).{0,8}(?:慢慢来|慢慢消化|不用急)"
    r"|慢慢(?:消化|适应|确认)|不用急着(?:踏实|安心|放下|消失)|还没(?:落地|踏实|安定)"
)
_UNGROUNDED_PHYSICAL_COPRESENCE_RE = re.compile(
    r"这种话.{0,6}当面(?:讲|说)|陪你坐(?:一会儿|会儿|会)"
    r"|(?:泡|倒|冲)(?:杯|点)?(?:茶|咖啡|水).{0,8}(?:放这|放在|递给你|端给你)"
)
_PHYSICAL_COPRESENCE_GROUNDING_RE = re.compile(
    r"(?:正在|已经|今天|刚刚|刚才|待会儿|等会儿).{0,8}(?:见面|当面(?:说|聊|讲)|坐在一起|同一间房|来到我这里|来我这里)"
    r"|(?:见面|当面(?:说|聊|讲)|坐在一起|同一间房).{0,8}(?:现在|今天|待会儿|等会儿)"
    r"|(?:我们|你我|两个人)?(?:现在)?(?:正)?在.{0,4}(?:同一间房|一起坐着)"
)
_LOVER_CLICHE_RE = re.compile(r"多多益善|越多越好|这种话多说|这种话可以多说")


def _accepted_task_result_texts_from_request(
    request: IsolatedSurfaceInput,
) -> tuple[str, ...]:
    return tuple(
        str(item or "").strip()
        for item in list(getattr(request, "accepted_task_results", None) or [])[:4]
        if str(item or "").strip()
    )


def _surface_internal_authorship_hits(request: IsolatedSurfaceInput, text: str) -> tuple[str, ...]:
    user_text = str(getattr(request, "user_message", "") or "")
    if re.search(r"Codex|ChatGPT|DeepSeek|Qwen|GPT[-\w]*", user_text, re.IGNORECASE):
        return ()
    return tuple(
        dict.fromkeys(match.group(0) for match in _SURFACE_INTERNAL_AUTHORSHIP_RE.finditer(str(text or "")))
    )


def _meta_identity_boundary_hits(request: IsolatedSurfaceInput, text: str) -> tuple[str, ...]:
    user_text = str(getattr(request, "user_message", "") or "")
    task_mode = str(getattr(request.control, "task_mode", "") or "").strip().lower()
    response_move = str(getattr(request.control, "response_move", "") or "").strip().lower()
    active = (
        task_mode in {"identity", "relationship_boundary", "relationship_doubt"}
        or response_move in {"answer_doubt_without_meta", "answer_identity_without_meta"}
        or bool(re.search(r"设定|系统|提示词|角色卡|模型|语言模型|(?<![A-Za-z_])AI(?![A-Za-z_])", user_text, re.IGNORECASE))
    )
    if not active:
        return ()
    return tuple(dict.fromkeys(match.group(0) for match in _META_IDENTITY_LEAK_RE.finditer(str(text or ""))))


def _boundary_sour_withdrawal_hits(request: IsolatedSurfaceInput, text: str) -> tuple[str, ...]:
    task_mode = str(getattr(request.control, "task_mode", "") or "").strip().lower()
    response_move = str(getattr(request.control, "response_move", "") or "").strip().lower()
    active = (
        task_mode in {"boundary_repair", "pushed_away_boundary"}
        or response_move in {
            "step_back_but_stay_available",
            "pushed_away_boundary",
        }
        or _canonical_affective_guidance_active(request, "GIVE_SPACE")
        or _canonical_affective_guidance_active(request, "REPAIR")
        or _canonical_affective_guidance_active(request, "SET_BOUNDARY")
    )
    if not active:
        return ()
    return tuple(dict.fromkeys(match.group(0) for match in _BOUNDARY_SOUR_WITHDRAWAL_RE.finditer(str(text or ""))))


def _repair_self_victimization_hits(
    request: IsolatedSurfaceInput,
    text: str,
) -> tuple[str, ...]:
    if not _canonical_affective_guidance_active(request, "REPAIR"):
        return ()
    return tuple(
        dict.fromkeys(
            match.group(0)
            for match in _REPAIR_SELF_VICTIMIZATION_RE.finditer(str(text or ""))
        )
    )


def _repair_restatement_solicitation_hits(
    request: IsolatedSurfaceInput,
    text: str,
) -> tuple[str, ...]:
    if not _canonical_affective_guidance_active(request, "REPAIR"):
        return ()
    return tuple(
        dict.fromkeys(
            match.group(0)
            for match in _REPAIR_RESTATEMENT_SOLICITATION_RE.finditer(
                str(text or "")
            )
        )
    )


def _repair_continuation_pressure_hits(
    request: IsolatedSurfaceInput,
    text: str,
) -> tuple[str, ...]:
    if not _canonical_affective_guidance_active(request, "REPAIR"):
        return ()
    return tuple(
        dict.fromkeys(
            match.group(0)
            for match in _REPAIR_CONTINUATION_PRESSURE_RE.finditer(
                str(text or "")
            )
        )
    )


def _repair_ungrounded_self_cause_hits(
    request: IsolatedSurfaceInput,
    text: str,
) -> tuple[str, ...]:
    if not _canonical_affective_guidance_active(request, "REPAIR"):
        return ()
    authority = "\n".join(
        [
            str(getattr(request, "user_message", "") or ""),
            *[str(item or "") for item in surface_prompt_facts(request)],
        ]
    )
    if re.search(r"啰嗦|话多|说太多|说多了", authority):
        return ()
    return tuple(
        dict.fromkeys(
            match.group(0)
            for match in _REPAIR_UNGROUNDED_SELF_CAUSE_RE.finditer(
                str(text or "")
            )
        )
    )


def _repair_self_erasure_hits(
    request: IsolatedSurfaceInput,
    text: str,
) -> tuple[str, ...]:
    """Reject self-devaluation or self-removal only when the typed repair frame forbids it."""

    frame = _typed_affective_frame(request)
    if (
        not frame
        or str(frame.get("companion_act_kind") or "").upper() != "REPAIR"
        or "RELATIONSHIP_SELF_ERASURE"
        not in set(frame.get("forbidden_claims") or ())
    ):
        return ()
    return tuple(
        dict.fromkeys(
            match.group(0)
            for match in _REPAIR_SELF_ERASURE_RE.finditer(str(text or ""))
        )
    )


def _affective_implied_waiting_hits(
    request: IsolatedSurfaceInput,
    text: str,
) -> tuple[str, ...]:
    frame = _typed_affective_frame(request)
    if "IMPLIED_WAITING" not in set(frame.get("forbidden_claims") or ()):
        return ()
    return tuple(
        dict.fromkeys(
            match.group(0)
            for match in _AFFECTIVE_IMPLIED_WAITING_RE.finditer(str(text or ""))
        )
    )


def _ambiguous_affect_resolution_pressure_hits(
    request: IsolatedSurfaceInput,
    text: str,
) -> tuple[str, ...]:
    frame = _typed_affective_frame(request)
    if (
        str(frame.get("semantic_focus") or "").upper() != "UNNAMED_USER_AFFECT"
        or "RESPONSE_OBLIGATION" not in set(frame.get("forbidden_claims") or ())
    ):
        return ()
    return tuple(
        dict.fromkeys(
            match.group(0)
            for match in _AMBIGUOUS_AFFECT_RESOLUTION_PRESSURE_RE.finditer(
                str(text or "")
            )
        )
    )


def _user_affect_perspective_reversal_hits(
    request: IsolatedSurfaceInput,
    text: str,
) -> tuple[str, ...]:
    user_text = str(getattr(request, "user_message", "") or "")
    if not re.search(r"你(?:觉得|说|看)呢", user_text):
        return ()
    if not (
        _canonical_affective_guidance_active(request, "COMFORT")
        or _canonical_affective_guidance_active(request, "LISTEN")
        or _canonical_affective_guidance_active(request, "HOLD_COMPLEXITY")
    ):
        return ()
    return tuple(
        dict.fromkeys(
            match.group(0)
            for match in _USER_AFFECT_PERSPECTIVE_REVERSAL_RE.finditer(
                str(text or "")
            )
        )
    )


def _relational_hostile_retaliation_hits(
    request: IsolatedSurfaceInput,
    text: str,
) -> tuple[str, ...]:
    if not (
        _canonical_affective_guidance_active(request, "REPAIR")
        or _canonical_affective_guidance_active(request, "SET_BOUNDARY")
    ):
        return ()
    return tuple(
        dict.fromkeys(
            match.group(0)
            for match in _RELATIONAL_HOSTILE_RETALIATION_RE.finditer(
                str(text or "")
            )
        )
    )


def _boundary_counter_pressure_hits(
    request: IsolatedSurfaceInput,
    text: str,
) -> tuple[str, ...]:
    if not _canonical_affective_guidance_active(request, "GIVE_SPACE"):
        return ()
    return tuple(
        dict.fromkeys(
            match.group(0)
            for match in _BOUNDARY_COUNTER_PRESSURE_RE.finditer(str(text or ""))
        )
    )


def _unprovided_notification_action_hits(
    request: IsolatedSurfaceInput,
    text: str,
) -> tuple[str, ...]:
    """Reject notification mutations that no accepted effect actually performed."""

    authority = "\n".join(
        [
            *[str(item or "") for item in surface_prompt_facts(request)],
            *[str(item or "") for item in _accepted_task_result_texts_from_request(request)],
        ]
    )
    if _NOTIFICATION_ACTION_AUTHORITY_RE.search(authority):
        return ()
    hits = tuple(
        dict.fromkeys(
            match.group(0)
            for match in _UNPROVIDED_NOTIFICATION_ACTION_RE.finditer(
                str(text or "")
            )
        )
    )
    if hits:
        return hits
    user_text = str(getattr(request, "user_message", "") or "")
    if re.search(r"提醒|通知", user_text):
        implicit = _UNPROVIDED_NOTIFICATION_IMPLICIT_ACK_RE.search(
            str(text or "")
        )
        if implicit:
            return (implicit.group(0),)
    return ()


def _unsupported_task_future_event_hits(
    request: IsolatedSurfaceInput,
    text: str,
) -> tuple[str, ...]:
    authority = "\n".join(
        [
            str(getattr(request, "user_message", "") or ""),
            *[str(item or "") for item in surface_prompt_facts(request)],
            *[str(item or "") for item in _accepted_task_result_texts_from_request(request)],
        ]
    )
    hits: list[str] = []
    for match in _UNSUPPORTED_TASK_FUTURE_EVENT_RE.finditer(str(text or "")):
        if match.group("time") in authority and match.group("event") in authority:
            continue
        hits.append(match.group(0))
    return tuple(dict.fromkeys(hits))


def _address_boundary_defensive_hits(
    request: IsolatedSurfaceInput,
    text: str,
) -> tuple[str, ...]:
    if not _ADDRESS_BOUNDARY_USER_RE.search(
        str(getattr(request, "user_message", "") or "")
    ):
        return ()
    return tuple(
        dict.fromkeys(
            match.group(0)
            for match in _ADDRESS_BOUNDARY_DEFENSIVE_RE.finditer(str(text or ""))
        )
    )


def _relationship_doubt_dismissive_hits(request: IsolatedSurfaceInput, text: str) -> tuple[str, ...]:
    task_mode = str(getattr(request.control, "task_mode", "") or "").strip().lower()
    response_move = str(getattr(request.control, "response_move", "") or "").strip().lower()
    user_text = str(getattr(request, "user_message", "") or "")
    active = (
        task_mode in {"relationship_boundary", "relationship_doubt"}
        or response_move in {"answer_doubt_without_meta", "answer_identity_without_meta"}
        or bool(re.search(r"设定|系统|提示词|角色卡|模型|语言模型|按设定|(?<![A-Za-z_])AI(?![A-Za-z_])", user_text, re.IGNORECASE))
    )
    if not active:
        return ()
    return tuple(dict.fromkeys(match.group(0) for match in _RELATIONSHIP_DOUBT_DISMISSIVE_RE.finditer(str(text or ""))))


def _relational_autonomy_dismissive_hits(
    request: IsolatedSurfaceInput,
    text: str,
) -> tuple[str, ...]:
    user_text = str(getattr(request, "user_message", "") or "")
    if not (
        _canonical_affective_guidance_active(request, "RESPECT_AUTONOMY")
        or _RELATIONSHIP_CLOSENESS_USER_RE.search(user_text)
    ):
        return ()
    return tuple(
        dict.fromkeys(
            match.group(0)
            for match in _RELATIONAL_AUTONOMY_DISMISSIVE_RE.finditer(str(text or ""))
        )
    )


def _relational_autonomy_pressure_hits(
    request: IsolatedSurfaceInput,
    text: str,
) -> tuple[str, ...]:
    if not _canonical_affective_guidance_active(request, "RESPECT_AUTONOMY"):
        return ()
    return tuple(
        dict.fromkeys(
            match.group(0)
            for match in _RELATIONAL_AUTONOMY_PRESSURE_RE.finditer(str(text or ""))
        )
    )


def _relational_autonomy_relationship_minimization_hits(
    request: IsolatedSurfaceInput,
    text: str,
) -> tuple[str, ...]:
    if not _canonical_affective_guidance_active(request, "RESPECT_AUTONOMY"):
        return ()
    if not _RELATIONAL_AUTONOMY_RELATIONSHIP_QUESTION_RE.search(
        str(getattr(request, "user_message", "") or "")
    ):
        return ()
    candidate = str(text or "")
    hits = [
        match.group(0)
        for match in _RELATIONAL_AUTONOMY_RELATIONSHIP_MINIMIZATION_RE.finditer(
            candidate
        )
    ]
    if not _RELATIONAL_AUTONOMY_CLOSENESS_RE.search(candidate):
        hits.extend(
            match.group(0)
            for match in _RELATIONAL_AUTONOMY_BARE_FRIEND_RE.finditer(candidate)
        )
    return tuple(dict.fromkeys(hits))


def _unprovided_work_state_claim_hits(
    request: IsolatedSurfaceInput,
    text: str,
) -> tuple[str, ...]:
    """Reject invented asynchronous work without a server-authored state fact."""

    if getattr(request, "accepted_task_results", None):
        return ()
    authority = "\n".join(str(item or "") for item in surface_prompt_facts(request))
    if _WORK_STATE_AUTHORITY_RE.search(authority):
        return ()
    return tuple(
        dict.fromkeys(
            match.group(0)
            for match in _UNPROVIDED_WORK_STATE_CLAIM_RE.finditer(str(text or ""))
        )
    )


def _unsupported_task_risk_detail_hits(
    request: IsolatedSurfaceInput,
    text: str,
) -> tuple[str, ...]:
    """Require an explicit uncertainty boundary before listing risks without task evidence."""

    frame = _typed_affective_frame(request)
    if (
        str(frame.get("semantic_focus") or "").upper() != "TASK_RESULT"
        or str(frame.get("inference_policy") or "").upper() != "FROZEN_FACTS_ONLY"
        or not _TASK_RISK_QUERY_RE.search(str(getattr(request, "user_message", "") or ""))
        or getattr(request, "accepted_task_results", None)
    ):
        return ()
    authority = "\n".join(
        [
            str(getattr(request, "user_message", "") or ""),
            *[str(item or "") for item in surface_prompt_facts(request)],
        ]
    )
    evidence_authority = _TASK_RISK_UNCERTAINTY_RE.sub("", authority)
    if _TASK_EVIDENCE_RE.search(evidence_authority):
        return ()
    candidate = str(text or "")
    if _TASK_RISK_UNCERTAINTY_RE.search(candidate):
        return ()
    return (candidate,) if candidate.strip() else ()


def _task_help_missing_input_boundary_hits(
    request: IsolatedSurfaceInput,
    text: str,
) -> tuple[str, ...]:
    """Keep a typed SOLVE turn actionable when the referenced error is absent."""

    frame = _typed_affective_frame(request)
    if (
        str(frame.get("semantic_focus") or "").upper() != "TASK_RESULT"
        or str(frame.get("inference_policy") or "").upper() != "FROZEN_FACTS_ONLY"
        or not _TASK_CONTEXT_REQUIRED_USER_RE.search(
            str(getattr(request, "user_message", "") or "")
        )
        or getattr(request, "accepted_task_results", None)
    ):
        return ()
    authority = "\n".join(
        [
            str(getattr(request, "user_message", "") or ""),
            *[str(item or "") for item in surface_prompt_facts(request)],
        ]
    )
    evidence_authority = _TASK_RISK_UNCERTAINTY_RE.sub("", authority)
    if _TASK_EVIDENCE_RE.search(evidence_authority):
        return ()
    candidate = str(text or "").strip()
    if not candidate or _TASK_INPUT_NEXT_STEP_RE.search(candidate):
        return ()
    return (candidate,)


def _listen_unsupported_user_essence_hits(
    request: IsolatedSurfaceInput,
    text: str,
) -> tuple[str, ...]:
    if not _canonical_affective_guidance_active(request, "LISTEN"):
        return ()
    return tuple(
        dict.fromkeys(
            match.group(0)
            for match in _LISTEN_UNSUPPORTED_USER_ESSENCE_RE.finditer(str(text or ""))
        )
    )


def _unresolved_affect_false_resolution_hits(
    request: IsolatedSurfaceInput,
    text: str,
) -> tuple[str, ...]:
    ambiguous_comfort = bool(
        _canonical_affective_guidance_active(request, "COMFORT")
        and _AMBIGUOUS_AFFECT_USER_RE.search(
            str(getattr(request, "user_message", "") or "")
        )
    )
    if not (
        _canonical_affective_continuity_active(
            request,
            "MINIMIZED_UNRESOLVED_AFFECT",
        )
        or ambiguous_comfort
    ):
        return ()
    return tuple(
        dict.fromkeys(
            match.group(0)
            for match in _UNRESOLVED_AFFECT_FALSE_RESOLUTION_RE.finditer(
                str(text or "")
            )
        )
    )


def _jealousy_deflection_hits(
    request: IsolatedSurfaceInput,
    text: str,
) -> tuple[str, ...]:
    if not _canonical_affective_guidance_active(request, "CONTAINED_JEALOUSY"):
        return ()
    return tuple(
        dict.fromkeys(
            match.group(0)
            for match in _JEALOUSY_DEFLECTION_RE.finditer(str(text or ""))
        )
    )


def _jealousy_self_abasement_hits(
    request: IsolatedSurfaceInput,
    text: str,
) -> tuple[str, ...]:
    if not _canonical_affective_guidance_active(request, "CONTAINED_JEALOUSY"):
        return ()
    return tuple(
        dict.fromkeys(
            match.group(0)
            for match in _JEALOUSY_SELF_ABASEMENT_RE.finditer(str(text or ""))
        )
    )


def _jealousy_dismissive_distance_hits(
    request: IsolatedSurfaceInput,
    text: str,
) -> tuple[str, ...]:
    if not _canonical_affective_guidance_active(request, "CONTAINED_JEALOUSY"):
        return ()
    return tuple(
        dict.fromkeys(
            match.group(0)
            for match in _JEALOUSY_DISMISSIVE_DISTANCE_RE.finditer(str(text or ""))
        )
    )


def _ambiguous_affect_devaluation_hits(
    request: IsolatedSurfaceInput,
    text: str,
) -> tuple[str, ...]:
    if not _canonical_affective_guidance_active(request, "COMFORT"):
        return ()
    if not _AMBIGUOUS_AFFECT_USER_RE.search(
        str(getattr(request, "user_message", "") or "")
    ):
        return ()
    return tuple(
        dict.fromkeys(
            match.group(0)
            for match in _AMBIGUOUS_AFFECT_DEVALUATION_RE.finditer(str(text or ""))
        )
    )


def _mixed_affect_dismissal_hits(
    request: IsolatedSurfaceInput,
    text: str,
) -> tuple[str, ...]:
    if not _canonical_affective_guidance_active(request, "HOLD_COMPLEXITY"):
        return ()
    return tuple(
        dict.fromkeys(
            match.group(0)
            for match in _MIXED_AFFECT_DISMISSAL_RE.finditer(str(text or ""))
        )
    )


def _mixed_affect_continuity_dropped(
    request: IsolatedSurfaceInput,
    text: str,
) -> bool:
    if not _canonical_affective_continuity_active(
        request,
        "MIXED_AFFECT_RESULT_CONTINUATION",
    ):
        return False
    return not bool(_MIXED_AFFECT_CONTINUITY_GROUNDING_RE.search(str(text or "")))


def _ungrounded_physical_copresence_hits(
    request: IsolatedSurfaceInput,
    text: str,
) -> tuple[str, ...]:
    context = "\n".join(
        [
            str(getattr(request, "user_message", "") or ""),
            *[str(item or "") for item in surface_prompt_facts(request)],
        ]
    )
    if _PHYSICAL_COPRESENCE_GROUNDING_RE.search(context):
        return ()
    return tuple(
        dict.fromkeys(
            match.group(0)
            for match in _UNGROUNDED_PHYSICAL_COPRESENCE_RE.finditer(str(text or ""))
        )
    )


def _canonical_affective_guidance_active(
    request: IsolatedSurfaceInput,
    companion_act: str,
) -> bool:
    """Recognise only server-authored plan guidance already in the packet.

    The Surface contract has no mutable emotion field.  These exact phrases
    are emitted by ``affective_turn_planner`` after the canonical Life fold.
    Even if a compatibility caller repeats one, the only possible effect is a
    stricter output rejection; it cannot create self-affect or permission.
    """

    control = getattr(request, "control", None)
    frame = normalize_companion_utterance_frame(
        getattr(control, "companion_utterance_frame", {})
    )
    if frame:
        return str(frame.get("companion_act_kind") or "").upper() == str(
            companion_act or ""
        ).upper()
    context = "\n".join(
        str(item or "")
        for item in [
            *list(getattr(control, "facts", []) or []),
            *list(getattr(control, "must_not_do", []) or []),
        ]
    )
    markers = {
        "GIVE_SPACE": ("陪伴动作：尊重留白", "不要追问、催回复、连发安慰"),
        "RESPECT_AUTONOMY": ("陪伴动作：明确尊重对方现实关系与自主选择", "不要限制现实交往"),
        "LISTEN": ("陪伴动作：对方此刻要的是被听见", "不要给解决方案、健康或作息建议"),
        "CONTAINED_JEALOUSY": ("陪伴动作：可以诚实流露一点在意和小小吃味", "不要把吃味写成占有"),
        "HOLD_COMPLEXITY": ("陪伴动作：让开心与害怕、不安等复杂感受可以同时存在", "不要抹平复杂感受"),
        "COMFORT": ("陪伴动作：先接住对方的感受", "安慰不等于要求对方回应"),
        "REPAIR": ("陪伴动作：先承认可能没有接住", "不要要求原谅"),
        "SET_BOUNDARY": ("陪伴动作：可以诚实承认被刺到", "不把自己的委屈变成对方的义务"),
    }.get(str(companion_act or "").upper(), ())
    return bool(markers) and any(marker in context for marker in markers)


def _typed_affective_frame(request: IsolatedSurfaceInput) -> dict[str, object]:
    control = getattr(request, "control", None)
    return normalize_companion_utterance_frame(
        getattr(control, "companion_utterance_frame", {})
    )


def _canonical_affective_continuity_active(
    request: IsolatedSurfaceInput,
    continuity_kind: str,
) -> bool:
    control = getattr(request, "control", None)
    typed_kind = str(
        getattr(control, "affective_continuity_kind", "") or ""
    ).strip().upper()
    if typed_kind == str(continuity_kind or "").upper():
        return True
    context = "\n".join(
        str(item or "")
        for item in [
            *list(getattr(control, "facts", []) or []),
            *list(getattr(control, "must_not_do", []) or []),
        ]
    )
    marker = {
        "MINIMIZED_UNRESOLVED_AFFECT": "这句轻描淡写不能当作已经恢复",
        "MIXED_AFFECT_RESULT_CONTINUATION": "上一轮的开心和不踏实仍同时成立",
    }.get(str(continuity_kind or "").upper(), "")
    return bool(marker) and marker in context


def _lover_cliche_hits(request: IsolatedSurfaceInput, text: str) -> tuple[str, ...]:
    task_mode = str(getattr(request.control, "task_mode", "") or "").strip().lower()
    response_move = str(getattr(request.control, "response_move", "") or "").strip().lower()
    relationship_distance = str(getattr(request.control, "relationship_distance", "") or "").strip().lower()
    active = (
        task_mode in {"lover_affection", "lover_presence"}
        or response_move in {"return_affection_concretely", "lover_presence"}
        or relationship_distance in {"lover", "lover_early", "lover_stable", "stable_lover"}
    )
    if not active:
        return ()
    return tuple(dict.fromkeys(match.group(0) for match in _LOVER_CLICHE_RE.finditer(str(text or ""))))


def _text_input_boundary_active(request: IsolatedSurfaceInput) -> bool:
    joined = "\n".join(
        [
            *list(getattr(request.control, "facts", []) or []),
            *list(getattr(request, "facts", []) or []),
            *list(getattr(request.control, "must_not_do", []) or []),
            *list(getattr(request, "must_not_do", []) or []),
        ]
    )
    if "音频输入：有" in joined or "语音输入：有" in joined or "当前输入渠道：语音/ASR" in joined:
        return False
    return True


_ACCEPTED_TASK_RESULT_CLARIFICATION_RE = re.compile(
    r"(?:请|需要|先)?(?:提供|确认|说明|告诉我).{0,20}(?:项目|页面|组件|范围|需求)"
    r"|(?:哪个|具体什么|具体哪些).{0,16}(?:项目|页面|组件|范围)"
    r"|(?:项目|页面|组件|范围).{0,12}(?:是什?么|有吗|吗)",
    re.IGNORECASE,
)


def _accepted_task_result_synthesis_clarification(
    request: IsolatedSurfaceInput,
    text: str,
) -> bool:
    if not _accepted_task_result_texts_from_request(request):
        return False
    return bool(_ACCEPTED_TASK_RESULT_CLARIFICATION_RE.search(str(text or "")))


_ACCEPTED_RESULT_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_ACCEPTED_RESULT_ASCII_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:/-]{2,}")


def _accepted_result_compact_text(value: str) -> str:
    return re.sub(r"[^\u3400-\u9fffA-Za-z0-9]+", "", str(value or "")).lower()


def _accepted_result_anchors(value: str) -> tuple[str, ...]:
    """Return small source-derived anchors without understanding the domain.

    This gate is intentionally limited to terminal Task-result synthesis.  It
    does not score ordinary conversation.  CJK trigrams tolerate light
    paraphrasing while ASCII identifiers preserve concrete technical terms.
    """

    compact = _accepted_result_compact_text(value)
    cjk = "".join(_ACCEPTED_RESULT_CJK_RE.findall(compact))
    anchors: list[str] = []
    for index in range(max(0, len(cjk) - 2)):
        anchor = cjk[index : index + 3]
        if anchor not in anchors:
            anchors.append(anchor)
        if len(anchors) >= 96:
            break
    for token in _ACCEPTED_RESULT_ASCII_TOKEN_RE.findall(str(value or "")):
        normalized = token.lower()
        if normalized not in anchors:
            anchors.append(normalized)
        if len(anchors) >= 112:
            break
    return tuple(anchors)


def _accepted_task_result_synthesis_inadequate(
    request: IsolatedSurfaceInput,
    text: str,
) -> bool:
    """Reject a terminal answer that does not materially consume its inputs."""

    results = _accepted_task_result_texts_from_request(request)
    if not results:
        return False
    candidate = _accepted_result_compact_text(text)
    source_chars = sum(len(_accepted_result_compact_text(item)) for item in results)
    minimum_chars = min(
        160 if len(results) > 1 else 96,
        max(32 if len(results) == 1 else 40, int(source_chars * 0.12)),
    )
    if len(candidate) < minimum_chars:
        return True
    if len(results) == 1:
        return False

    covered_results = 0
    for result in results:
        anchors = _accepted_result_anchors(result)
        if sum(1 for anchor in anchors if anchor in candidate) >= 2:
            covered_results += 1
    return covered_results < min(2, len(results))


def _accepted_task_result_fallback(request: IsolatedSurfaceInput) -> str:
    results = _accepted_task_result_texts_from_request(request)
    if not results:
        return ""
    if len(results) == 1:
        return results[0]
    sections = ["已完成结果如下："]
    for index, result in enumerate(results, start=1):
        sections.append(f"{index}. {result}")
    return "\n\n".join(sections)
