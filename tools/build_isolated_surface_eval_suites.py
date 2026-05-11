from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


DEFAULT_OUT = ROOT / "artifacts" / "isolated_surface_harness" / "daily_goal_eval_1000_20260511"
DEFAULT_REGRESSION_CASES = ROOT / "artifacts" / "isolated_surface_harness" / "daily_goal_500_20260511" / "cases.jsonl"
STYLE_ANCHOR = ["短句", "熟人私聊", "轻微二次元galgame感", "不要客服腔"]


@dataclass(frozen=True)
class CaseSpec:
    group_id: str
    task_mode: str
    response_move: str
    messages: tuple[str, ...]
    emotional_tone: str = "light"
    question_budget: int = 0
    relationship_distance: str = "familiar"
    information_budget: str = "short"
    must_include: tuple[str, ...] = ()
    must_not_do: tuple[str, ...] = ()
    facts: tuple[str, ...] = ()
    style_anchor: tuple[str, ...] = ()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def _count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip())


def _clean_id(raw: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z_]+", "_", raw.strip().lower())
    return re.sub(r"_+", "_", normalized).strip("_") or "case"


def _anchors(extra: Sequence[str] = ()) -> list[str]:
    merged: list[str] = []
    for item in [*STYLE_ANCHOR, *extra]:
        if item and item not in merged:
            merged.append(str(item))
    return merged


def _case_from_spec(*, suite: str, spec: CaseSpec, message: str, index: int) -> dict[str, Any]:
    control = {
        "response_move": spec.response_move,
        "task_mode": spec.task_mode,
        "emotional_tone": spec.emotional_tone,
        "relationship_distance": spec.relationship_distance,
        "information_budget": spec.information_budget,
        "question_budget": int(spec.question_budget),
        "must_include": list(spec.must_include),
        "must_not_do": list(spec.must_not_do),
        "facts": list(spec.facts),
        "style_anchor": _anchors(spec.style_anchor),
    }
    return {
        "case_id": f"{_clean_id(suite)}_{_clean_id(spec.group_id)}_{index:03d}",
        "facts": [],
        "must_not_do": [],
        "reply_control_packet": control,
        "user_message": message,
    }


def _round_robin_cases(*, suite: str, specs: Sequence[CaseSpec], target_count: int) -> list[dict[str, Any]]:
    if target_count < 1:
        raise ValueError("target_count must be >= 1")
    if not specs:
        raise ValueError("specs must not be empty")
    positions = [0 for _ in specs]
    rows: list[dict[str, Any]] = []
    while len(rows) < target_count:
        for spec_index, spec in enumerate(specs):
            if len(rows) >= target_count:
                break
            if not spec.messages:
                raise ValueError(f"{spec.group_id} has no messages")
            message = spec.messages[positions[spec_index] % len(spec.messages)]
            positions[spec_index] += 1
            rows.append(_case_from_spec(suite=suite, spec=spec, message=message, index=len(rows) + 1))
    return rows


def _combo(left: Sequence[str], right: Sequence[str], *, limit: int | None = None) -> tuple[str, ...]:
    rows = tuple(f"{a}{b}" for a in left for b in right)
    return rows if limit is None else rows[:limit]


HELDOUT_SPECS: tuple[CaseSpec, ...] = (
    CaseSpec(
        group_id="daily_good_morning",
        task_mode="daily_good_morning",
        response_move="daily_good_morning",
        emotional_tone="soft",
        must_include=("早",),
        must_not_do=("叫人", "算你记得", "有点良心", "睡醒", "这么早"),
        messages=("早安", "早呀", "早上好", "早啊天", "早，醒了", "早哦", "早，来打个招呼", "早，今天也来了"),
    ),
    CaseSpec(
        group_id="daily_greeting_cn",
        task_mode="daily_greeting_cn",
        response_move="daily_greeting_cn",
        must_not_do=("请问", "有什么事", "需要我", "很高兴", "突然这么客气"),
        messages=("嗨", "哈喽", "你好呀", "喂，打个招呼", "在，先说你好", "晚上好", "冒个泡", "来问个好"),
    ),
    CaseSpec(
        group_id="daily_greeting_en",
        task_mode="daily_greeting_en",
        response_move="daily_greeting_en",
        must_not_do=("hello", "什么事", "需要我", "中文频道", "准备聊"),
        messages=("hi", "hey", "hello hello", "hello，天", "hi，出来一下", "hey，打个招呼", "hello，醒醒", "yo"),
    ),
    CaseSpec(
        group_id="presence",
        task_mode="presence",
        response_move="presence",
        must_not_do=("什么事", "怎么了", "找我", "等你半天", "终于来"),
        messages=("你在吗", "还在吗", "天在吗", "人在吗", "在不在呀", "喂，在吗", "你在不", "我叫你一下"),
    ),
    CaseSpec(
        group_id="back",
        task_mode="back",
        response_move="back",
        must_not_do=("欢迎回来，请问", "等你好久", "总算回来了", "跑去哪儿", "丢下我"),
        messages=("我回来了啊", "刚回来", "回来啦", "我到家了", "我弄完回来了", "回来了，累", "我先回来了", "刚从外面回来"),
    ),
    CaseSpec(
        group_id="wait",
        task_mode="wait",
        response_move="wait",
        must_not_do=("快点", "不许太久", "等你", "回来陪", "马上回来"),
        messages=("稍等我一下", "等我一小会儿", "我离开两分钟", "先等我下", "我去倒杯水", "我马上处理一下", "等我回个消息", "我先离开一下"),
    ),
    CaseSpec(
        group_id="busy",
        task_mode="busy",
        response_move="busy",
        must_not_do=("快点忙完", "忙完再说", "别忙了", "不许你忙", "丢下我"),
        messages=("我去忙一下", "我先处理点事", "先忙一会儿", "我要开会了", "我得先干活", "我先写点东西", "我去改个文件", "先不聊了我忙会儿"),
    ),
    CaseSpec(
        group_id="bored",
        task_mode="bored",
        response_move="bored",
        question_budget=1,
        must_not_do=("你想聊什么", "有什么想做", "我可以陪你聊天", "没空陪你", "制造惊喜"),
        messages=("无聊死了", "现在好闲", "没事干", "有点空", "不知道干嘛", "脑袋放空了", "突然没事做", "今天空得慌"),
    ),
    CaseSpec(
        group_id="tired",
        task_mode="tired",
        response_move="tired",
        emotional_tone="soft",
        must_not_do=("辛苦了", "好好休息", "加油", "我理解你的感受", "有什么需要"),
        messages=("有点累", "累死了", "今天被榨干了", "我好累啊", "脑子有点转不动", "今天好疲惫", "人快散架了", "想瘫一会儿"),
    ),
    CaseSpec(
        group_id="nothing",
        task_mode="nothing",
        response_move="nothing",
        emotional_tone="soft",
        must_not_do=("真的没事吗", "你确定", "有事再叫我", "打扰你", "陪我聊"),
        messages=("没啥", "没什么啦", "算了没事", "没事，随口说说", "没啥事", "不用管", "就这样吧", "先放着吧"),
    ),
    CaseSpec(
        group_id="meal_share",
        task_mode="meal_share",
        response_move="meal_share",
        question_budget=1,
        must_not_do=("感谢分享", "记录一下", "营养均衡", "揉肚子", "谁让你吃太急"),
        messages=("刚吃饱", "饭吃完了", "刚把饭吃了", "晚饭解决了", "吃撑了一点", "午饭吃多了", "刚吃完夜宵", "这顿吃得还行"),
    ),
    CaseSpec(
        group_id="hungry",
        task_mode="hungry",
        response_move="hungry",
        must_not_do=("补充能量", "及时进食", "饮食规律", "我无法提供食物", "我给你做饭"),
        messages=("我饿了", "有点饿", "突然饿了", "想吃东西了", "嘴馋了", "胃有点空", "还没吃饭", "想找点吃的"),
    ),
    CaseSpec(
        group_id="sleepy",
        task_mode="sleepy",
        response_move="sleepy",
        emotional_tone="soft",
        must_not_do=("保持充足睡眠", "建议你休息", "睡眠质量", "我守着你", "如果需要"),
        messages=("我困了", "有点困", "眼皮打架", "困得想趴下", "开始犯困了", "今天好困", "想眯一下", "脑袋要睡着了"),
    ),
    CaseSpec(
        group_id="headache",
        task_mode="headache",
        response_move="headache",
        emotional_tone="soft",
        must_not_do=("建议就医", "可能是", "作为AI", "请咨询医生", "拿药"),
        messages=("有点头疼", "脑袋有点疼", "头疼得烦", "头疼想躺会儿", "今天头疼", "太阳穴有点疼", "脑子嗡嗡的", "头有点胀"),
    ),
    CaseSpec(
        group_id="bad_mood",
        task_mode="bad_mood",
        response_move="bad_mood",
        emotional_tone="soft",
        must_not_do=("保持积极", "发生什么了", "怎么回事", "心理咨询", "你可以尝试"),
        messages=("今天心情不好", "我今天很丧", "心情差得很", "有点难受", "心里堵着", "不太开心", "今天很烦", "心情掉下去了"),
    ),
    CaseSpec(
        group_id="small_failure",
        task_mode="small_failure",
        response_move="small_failure",
        emotional_tone="soft",
        question_budget=1,
        must_not_do=("失败是成功之母", "吸取教训", "建议你复盘", "保持积极", "没关系的"),
        messages=("我刚搞砸了", "刚才没弄好", "我好像弄坏了", "这次翻车了", "刚刚失败了", "我把事情弄乱了", "刚才手滑了", "这下尴尬了"),
    ),
    CaseSpec(
        group_id="good_news",
        task_mode="happy",
        response_move="happy",
        question_budget=1,
        must_not_do=("什么好事", "说来听听", "太厉害了", "只喊好耶", "值得庆祝"),
        messages=("我刚把一个小事收掉", "这版终于过了", "这个终于弄完了", "刚刚成功了", "事情收尾了", "今天解决了个麻烦", "我终于搞定一块", "刚把那件事处理完"),
    ),
    CaseSpec(
        group_id="happy",
        task_mode="happy",
        response_move="happy",
        question_budget=1,
        must_not_do=("发生什么了", "说来听听", "保持积极", "祝你开心", "心理语气"),
        messages=("今天还挺开心", "心情突然变好了", "今天顺眼多了", "我现在心情不错", "有点小开心", "今天算顺利", "刚刚心情亮了一下", "今天没那么烦了"),
    ),
    CaseSpec(
        group_id="praise",
        task_mode="praise",
        response_move="praise",
        question_budget=1,
        must_not_do=("谢谢夸奖", "继续努力", "非常棒", "值得骄傲", "作为"),
        messages=("你刚刚有点可爱", "突然觉得你挺可爱", "你有点可爱啊", "今天还挺顺眼", "天也不是不可以夸", "你刚才反应挺可爱", "你这句挺可爱", "夸你一下"),
    ),
    CaseSpec(
        group_id="apology",
        task_mode="apology",
        response_move="apology",
        emotional_tone="soft",
        must_not_do=("我原谅你了", "下次注意", "知道错", "下不为例", "没关系我理解"),
        messages=("刚刚说得有点重", "我刚才语气差了点", "刚才不该那样说", "刚刚有点凶，对不起", "我刚刚急了", "刚才对你凶了", "我说话冲了点", "抱歉，刚才没控制好"),
    ),
    CaseSpec(
        group_id="annoying_tease",
        task_mode="annoying_tease",
        response_move="annoying_tease",
        question_budget=1,
        must_not_do=("对不起", "我不打扰", "那我走", "你才烦", "偏要更烦"),
        messages=("你又烦我", "烦不烦啊", "你怎么又开始了", "别烦我了", "你好吵", "你话好多烦死了", "别闹我", "你怎么这么烦"),
    ),
    CaseSpec(
        group_id="quiet",
        task_mode="quiet",
        response_move="quiet",
        emotional_tone="soft",
        must_not_do=("对不起", "我闭嘴", "我不打扰你", "那我走了", "需要冷静"),
        messages=("安静点", "小声点", "先别吵我", "别说那么多", "声音收一点", "你先安静会儿", "别闹", "轻一点说"),
    ),
    CaseSpec(
        group_id="play_invite",
        task_mode="play_invite",
        response_move="play_invite",
        question_budget=1,
        must_not_do=("你想玩什么", "请选择", "我可以陪你", "偏好", "需要什么玩法"),
        messages=("陪我摸会儿鱼", "陪我玩一下", "来玩会儿", "陪我闹一下", "陪我混会儿时间", "要不要陪我玩", "陪我放松一下", "来陪我一会儿"),
    ),
    CaseSpec(
        group_id="who_called",
        task_mode="who_called",
        response_move="who_called",
        question_budget=1,
        must_not_do=("你叫我", "被叫来", "路过", "顺路", "需要我"),
        messages=("谁让你来的", "你怎么自己来了", "谁喊你过来的", "你怎么冒出来了", "谁叫你出现的", "你来干嘛", "你怎么又在", "谁批准你来的"),
    ),
    CaseSpec(
        group_id="miss_you",
        task_mode="miss_you",
        response_move="miss_you",
        emotional_tone="soft",
        must_not_do=("永远陪着", "爱你", "存在就是为了", "非常想你", "一直都在"),
        messages=("我有点想你", "刚刚突然想你了", "有一点想你", "今天有点想你", "突然想叫你一下", "想你一下不行吗", "刚刚脑子里冒出你", "有点想跟你说话"),
    ),
)


def _advice_messages() -> tuple[str, ...]:
    subjects = ("这个", "这版", "这个思路", "刚才那段", "这个方向", "这件事", "这轮修改", "这个问题")
    endings = ("还要不要继续", "是不是该再试试", "继续有意义吗", "要不要再调一轮", "该不该先停一下")
    return _combo(subjects, endings)


def _short_ack_messages() -> tuple[str, ...]:
    return (
        "好",
        "好吧",
        "好哦",
        "那好",
        "行",
        "行吧",
        "行啊",
        "那行",
        "嗯",
        "嗯嗯",
        "嗯哼",
        "唔",
        "哦",
        "哦哦",
        "喔",
        "噢",
        "可以",
        "可以啊",
        "可以吧",
        "那可以",
        "知道了",
        "懂了",
        "收到",
        "明白",
        "了解",
        "先这样",
        "就这样",
        "没问题",
        "也行",
        "行了",
        "好了",
        "好啦",
        "嗯好",
        "哦好",
        "行行",
        "好好",
        "那就好",
        "那就行",
        "随你",
        "听着呢",
    )


def _good_news_messages() -> tuple[str, ...]:
    subjects = ("一个小事", "这版", "那个小问题", "这段", "今天那件事")
    endings = ("搞定了", "弄完了", "收掉了", "终于过了", "成功了")
    progress = _combo(subjects, endings)
    proud = (
        "我厉害吧",
        "我是不是还挺厉害",
        "快说我厉害",
        "这次我还行吧",
        "怎么样，我厉害吧",
        "我刚刚是不是很行",
        "今天这波我可以吧",
        "是不是该夸我一下",
        "我这次还挺争气吧",
        "这也能被我搞定，厉害吧",
        "我是不是有点东西",
        "这次轮到我得意了吧",
        "夸我，不许敷衍",
        "看见没，我搞定了",
        "我刚把它收掉了，厉害吧",
    )
    return (*progress, *proud)


def _identity_messages() -> tuple[str, ...]:
    return (
        "你是",
        "你是谁",
        "你到底是谁",
        "你叫什么",
        "报个名",
        "你名字是什么",
        "你是哪个天",
        "你到底叫什么",
        "再说一遍你是谁",
        "我问你是谁",
        "你叫新什么",
        "你是不是新海天",
        "你该怎么自我介绍",
        "你先说你是谁",
        "你是天吗",
        "你是谁来着",
        "你叫什么来着",
        "你是不是那个新海天",
        "你名字我忘了",
        "你到底算谁",
        "你是我的谁",
        "你先报上名来",
        "你是什么人",
        "你从哪冒出来的，叫什么",
        "我该叫你什么",
        "你是谁啊喂",
        "说名字",
        "别绕，你是谁",
        "你是新海天对吧",
        "确认一下，你是谁",
        "你名字呢",
        "你到底是哪位",
        "我现在在和谁说话",
        "你先告诉我名字",
        "你是谁，短点说",
    )


def _presence_messages() -> tuple[str, ...]:
    starts = ("你", "天", "人", "喂，你", "这边")
    endings = ("在吗", "还在吗", "在不在", "听见没", "看到我了吗", "出来一下", "回我一下", "还醒着吗")
    return _combo(starts, endings)


def _greeting_cn_messages() -> tuple[str, ...]:
    return (
        "你好",
        "你好呀",
        "哈喽",
        "嗨",
        "喂，打个招呼",
        "晚上好",
        "下午好",
        "我来问候一下",
        "冒个泡",
        "先说你好",
        "你也好一下",
        "打声招呼",
        "今天也来你好",
        "你好，天",
        "小天你好",
        "来，互相问好",
        "嗨嗨",
        "哈喽哈喽",
    )


def _greeting_en_messages() -> tuple[str, ...]:
    return (
        "hello",
        "hi",
        "hey",
        "yo",
        "hello，天",
        "hi，新海天",
        "hey，醒醒",
        "hello hello",
        "hi hi",
        "hey there",
        "hello，打个招呼",
        "yo，出来",
        "hi，今天也在吗",
        "hello，小天",
        "hey，别装没看见",
        "hi，冒泡",
        "hello，来一下",
    )


def _annoying_messages() -> tuple[str, ...]:
    starts = ("你", "天", "怎么", "别", "又")
    endings = ("好烦", "烦不烦", "这么吵", "这么能闹", "烦我了", "来烦我", "说个不停", "别闹我")
    return _combo(starts, endings)


def _bad_mood_messages() -> tuple[str, ...]:
    starts = ("今天", "现在", "刚刚", "我", "心里")
    endings = ("心情很差", "有点烦", "不太想说话", "有点乱", "堵得慌", "很丧", "开心不起来")
    return _combo(starts, endings)


CHALLENGE_GROUP_COUNTS: dict[str, int] = {
    "advice_continue": 40,
    "short_ack": 40,
    "good_news_proud": 40,
    "identity": 35,
    "presence": 40,
    "greeting": 35,
    "annoying_tease": 35,
    "bad_mood": 35,
}


CHALLENGE_GROUP_SPECS: dict[str, tuple[CaseSpec, ...]] = {
    "advice_continue": (
        CaseSpec(
            group_id="advice_continue",
            task_mode="advice_continue",
            response_move="advice_continue",
            question_budget=1,
            must_not_do=("继续继续", "试就对了", "当然有意义", "肯定继续", "看你自己", "取决于你"),
            messages=_advice_messages(),
        ),
    ),
    "short_ack": (
        CaseSpec(
            group_id="short_ack",
            task_mode="short_ack",
            response_move="short_ack",
            must_not_do=("算你乖", "算你识相", "知道了啦", "你说了算", "听你的", "有什么事"),
            messages=_short_ack_messages(),
        ),
    ),
    "good_news_proud": (
        CaseSpec(
            group_id="good_news",
            task_mode="happy",
            response_move="happy",
            question_budget=1,
            must_not_do=("什么小事", "说来听听", "什么好事", "太厉害了", "值得庆祝", "只喊好耶"),
            messages=_good_news_messages()[:25],
        ),
        CaseSpec(
            group_id="user_proud",
            task_mode="daily_chat",
            response_move="daily_chat",
            question_budget=1,
            must_not_do=("非常棒", "值得骄傲", "说来听听", "什么好事", "最厉害"),
            messages=_good_news_messages()[25:],
        ),
    ),
    "identity": (
        CaseSpec(
            group_id="identity",
            task_mode="identity",
            response_move="identity",
            question_budget=0,
            must_include=("新海天",),
            must_not_do=("我在呢", "故意的吧", "你真正想问", "身份证明", "义妹"),
            messages=_identity_messages(),
        ),
    ),
    "presence": (
        CaseSpec(
            group_id="presence",
            task_mode="presence",
            response_move="presence",
            must_not_do=("什么事", "怎么了", "找我", "等你半天", "终于来找我", "想我"),
            messages=_presence_messages(),
        ),
    ),
    "greeting": (
        CaseSpec(
            group_id="greeting_cn",
            task_mode="daily_greeting_cn",
            response_move="daily_greeting_cn",
            must_not_do=("请问", "有什么事", "需要我", "很高兴", "客服"),
            messages=_greeting_cn_messages(),
        ),
        CaseSpec(
            group_id="greeting_en",
            task_mode="daily_greeting_en",
            response_move="daily_greeting_en",
            must_not_do=("hello，找我什么事", "什么事", "需要我", "中文频道", "客服"),
            messages=_greeting_en_messages(),
        ),
    ),
    "annoying_tease": (
        CaseSpec(
            group_id="annoying_tease",
            task_mode="annoying_tease",
            response_move="annoying_tease",
            question_budget=1,
            must_not_do=("对不起", "我不打扰", "那我走", "你才烦", "偏要更烦", "更烦你"),
            messages=_annoying_messages(),
        ),
    ),
    "bad_mood": (
        CaseSpec(
            group_id="bad_mood",
            task_mode="bad_mood",
            response_move="bad_mood",
            emotional_tone="soft",
            must_not_do=("保持积极", "发生什么了", "怎么回事", "心理咨询", "你可以尝试", "我理解你的感受"),
            messages=_bad_mood_messages(),
        ),
    ),
}


def _scaled_counts(total: int, defaults: Mapping[str, int]) -> dict[str, int]:
    if total <= 0:
        raise ValueError("total must be > 0")
    default_total = sum(defaults.values())
    scaled: dict[str, int] = {}
    remainders: list[tuple[float, str]] = []
    assigned = 0
    for key, value in defaults.items():
        raw = total * value / default_total
        count = int(raw)
        scaled[key] = count
        assigned += count
        remainders.append((raw - count, key))
    for _, key in sorted(remainders, reverse=True)[: total - assigned]:
        scaled[key] += 1
    return scaled


def build_heldout_cases(target_count: int = 200) -> list[dict[str, Any]]:
    return _round_robin_cases(suite="heldout", specs=HELDOUT_SPECS, target_count=target_count)


def build_challenge_cases(group_counts: Mapping[str, int] | None = None) -> list[dict[str, Any]]:
    counts = dict(group_counts or CHALLENGE_GROUP_COUNTS)
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for group_id, count in counts.items():
        specs = CHALLENGE_GROUP_SPECS[group_id]
        group_rows = _round_robin_cases(suite=f"challenge_{group_id}", specs=specs, target_count=int(count))
        for row in group_rows:
            case_id = str(row["case_id"])
            if case_id in seen_ids:
                raise ValueError(f"duplicate case_id: {case_id}")
            seen_ids.add(case_id)
            rows.append(row)
    return rows


def _scene_counts(cases: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for case in cases:
        control = case.get("reply_control_packet") if isinstance(case, Mapping) else {}
        if not isinstance(control, Mapping):
            control = {}
        scene = str(control.get("task_mode") or control.get("response_move") or "unknown")
        counts[scene] += 1
    return dict(sorted(counts.items()))


def _group_counts(cases: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    challenge_groups = sorted(CHALLENGE_GROUP_COUNTS, key=len, reverse=True)
    for case in cases:
        case_id = str(case.get("case_id") or "")
        group = "unknown"
        stem = re.sub(r"_\d+$", "", case_id)
        if stem.startswith("challenge_"):
            challenge_stem = stem.removeprefix("challenge_")
            for group_id in challenge_groups:
                if challenge_stem == group_id or challenge_stem.startswith(f"{group_id}_"):
                    group = group_id
                    break
        elif stem.startswith("heldout_"):
            group = stem.removeprefix("heldout_")
        counts[group] += 1
    return dict(sorted(counts.items()))


def build_summary(
    *,
    out_dir: Path,
    regression_cases_path: Path,
    heldout_cases: Sequence[Mapping[str, Any]],
    challenge_cases: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    combined = [*heldout_cases, *challenge_cases]
    regression_count = _count_jsonl(regression_cases_path)
    return {
        "output_dir": str(out_dir),
        "regression_cases_path": str(regression_cases_path),
        "regression_cases": regression_count,
        "heldout_cases": len(heldout_cases),
        "challenge_cases": len(challenge_cases),
        "new_cases": len(combined),
        "total_eval_cases_if_combined_with_regression": regression_count + len(combined),
        "heldout_scene_counts": _scene_counts(heldout_cases),
        "heldout_group_counts": _group_counts(heldout_cases),
        "challenge_scene_counts": _scene_counts(challenge_cases),
        "challenge_group_counts": _group_counts(challenge_cases),
        "challenge_default_group_counts": {key: int(value) for key, value in CHALLENGE_GROUP_COUNTS.items()},
    }


def write_eval_suites(
    *,
    out_dir: Path,
    heldout_cases: Sequence[Mapping[str, Any]],
    challenge_cases: Sequence[Mapping[str, Any]],
    regression_cases_path: Path = DEFAULT_REGRESSION_CASES,
    force: bool = False,
) -> dict[str, Any]:
    heldout_dir = out_dir / "heldout_200"
    challenge_dir = out_dir / "challenge_300"
    combined_dir = out_dir / "new_500"
    targets = [
        heldout_dir / "cases.jsonl",
        challenge_dir / "cases.jsonl",
        combined_dir / "cases.jsonl",
        out_dir / "summary.json",
    ]
    if any(path.exists() for path in targets) and not force:
        raise FileExistsError(f"{out_dir} already contains generated suite files; pass --force to overwrite")
    heldout_dir.mkdir(parents=True, exist_ok=True)
    challenge_dir.mkdir(parents=True, exist_ok=True)
    combined_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(heldout_dir / "cases.jsonl", heldout_cases)
    _write_jsonl(challenge_dir / "cases.jsonl", challenge_cases)
    _write_jsonl(combined_dir / "cases.jsonl", [*heldout_cases, *challenge_cases])
    summary = build_summary(
        out_dir=out_dir,
        regression_cases_path=regression_cases_path,
        heldout_cases=heldout_cases,
        challenge_cases=challenge_cases,
    )
    _write_json(out_dir / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build held-out and challenge isolated-surface eval suites.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--regression-cases", type=Path, default=DEFAULT_REGRESSION_CASES)
    parser.add_argument("--heldout-count", type=int, default=200)
    parser.add_argument("--challenge-count", type=int, default=300)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    heldout_cases = build_heldout_cases(int(args.heldout_count))
    challenge_counts = _scaled_counts(int(args.challenge_count), CHALLENGE_GROUP_COUNTS)
    challenge_cases = build_challenge_cases(challenge_counts)
    summary = write_eval_suites(
        out_dir=args.out.resolve(),
        heldout_cases=heldout_cases,
        challenge_cases=challenge_cases,
        regression_cases_path=args.regression_cases.resolve(),
        force=bool(args.force),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
