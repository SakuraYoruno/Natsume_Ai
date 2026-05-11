from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.persona.isolated_surface_continuity import build_continuity_surface_inputs


DEFAULT_OUT = ROOT / "artifacts" / "isolated_surface_harness" / "continuity_smoke_20260511"
DEFAULT_EVAL_OUT = ROOT / "artifacts" / "isolated_surface_harness" / "continuity_eval_72_20260511"
STYLE_ANCHOR = ["短句", "熟人私聊", "轻微二次元galgame感", "不要客服腔", "连续对话"]


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def _control(
    *,
    task_mode: str,
    response_move: str | None = None,
    emotional_tone: str = "light",
    question_budget: int = 0,
    must_include: tuple[str, ...] = (),
    must_not_do: tuple[str, ...] = (),
    facts: tuple[str, ...] = (),
    style_anchor: tuple[str, ...] = (),
) -> dict[str, Any]:
    anchors: list[str] = []
    for item in [*STYLE_ANCHOR, *style_anchor]:
        if item not in anchors:
            anchors.append(item)
    return {
        "response_move": response_move or task_mode,
        "task_mode": task_mode,
        "emotional_tone": emotional_tone,
        "relationship_distance": "familiar",
        "information_budget": "short",
        "question_budget": int(question_budget),
        "must_include": list(must_include),
        "must_not_do": list(must_not_do),
        "facts": list(facts),
        "style_anchor": anchors,
    }


def _turn(
    turn_id: str,
    user_message: str,
    *,
    control: Mapping[str, Any],
    facts: tuple[str, ...] = (),
    must_not_do: tuple[str, ...] = (),
    candidates: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "turn_id": turn_id,
        "user_message": user_message,
        "reply_control_packet": dict(control),
        "facts": list(facts),
        "must_not_do": list(must_not_do),
        "candidates": list(candidates),
    }


def build_continuity_cases() -> list[dict[str, Any]]:
    return [
        {
            "case_id": "continuity_good_news_proud_followup",
            "turns": [
                _turn(
                    "t01_progress",
                    "这版终于跑通了",
                    control=_control(
                        task_mode="happy",
                        response_move="happy",
                        question_budget=1,
                        must_include=("跑通",),
                        must_not_do=("什么好事", "说来听听"),
                    ),
                    candidates=("跑通啦，这才像话。", "什么好事，说来听听？"),
                ),
                _turn(
                    "t02_proud",
                    "厉害吧",
                    control=_control(
                        task_mode="good_news_proud",
                        response_move="good_news_proud",
                        facts=(
                            "上一轮摘要：尼尼说这版终于跑通了，回复已接住进展。",
                            "连续事实：本轮的“厉害吧”指刚才跑通。",
                        ),
                        must_not_do=("什么厉害", "说来听听"),
                    ),
                    candidates=("跑通这件事确实厉害，行吧，夸你一下。", "什么厉害？说来听听。"),
                ),
                _turn(
                    "t03_followup",
                    "然后呢",
                    control=_control(
                        task_mode="followup",
                        response_move="continue_previous_turn",
                        facts=(
                            "上一轮摘要：尼尼在等一句认可，回复已经围绕跑通给了夸。",
                            "连续目标：把话口递回去，让尼尼继续说，不重开新话题。",
                        ),
                        question_budget=1,
                        must_not_do=("怎么修的", "刚才卡着", "发呆"),
                    ),
                    candidates=("那就继续说，别从头开新坑。", "所以你刚才是怎么修好的？"),
                ),
            ],
        },
        {
            "case_id": "continuity_advice_to_next_step",
            "turns": [
                _turn(
                    "t01_advice",
                    "这个还要不要继续改",
                    control=_control(
                        task_mode="advice_continue",
                        response_move="advice_continue",
                        must_not_do=("看你自己", "当然继续", "肯定继续"),
                    ),
                    candidates=("先别急着全改，挑最卡的一处再动。", "看你自己，想改就改。"),
                ),
                _turn(
                    "t02_ack",
                    "好",
                    control=_control(
                        task_mode="short_ack",
                        response_move="short_ack",
                        facts=("上一轮摘要：尼尼在犹豫要不要继续改，回复已建议先挑最卡的一处。",),
                        must_not_do=("听你的", "你说了算"),
                    ),
                    candidates=("好，那就从最卡的那处继续。", "好好，听你的。"),
                ),
                _turn(
                    "t03_specific",
                    "具体点",
                    control=_control(
                        task_mode="next_step",
                        response_move="next_step",
                        facts=(
                            "上一轮摘要：建议先挑最卡的一处改。",
                            "连续事实：本轮“具体点”是在要更小的下一步。",
                        ),
                    ),
                    candidates=("具体点就是：先列目标，再只改最别扭的一句。", "你把所有内容都发来，我再完整处理。"),
                ),
            ],
        },
        {
            "case_id": "continuity_bad_mood_hold",
            "turns": [
                _turn(
                    "t01_bad_mood",
                    "今天心情很差",
                    control=_control(
                        task_mode="bad_mood",
                        response_move="bad_mood",
                        emotional_tone="soft",
                        must_not_do=("发生什么了", "保持积极"),
                    ),
                    candidates=("嗯，先别硬撑，我在这儿听你慢慢说。", "发生什么了？保持积极一点。"),
                ),
                _turn(
                    "t02_short_ack",
                    "嗯",
                    control=_control(
                        task_mode="short_ack",
                        response_move="short_ack",
                        emotional_tone="soft",
                        facts=("上一轮摘要：尼尼说今天心情很差，回复已先接住情绪。",),
                        must_not_do=("你说了算",),
                    ),
                    candidates=("嗯，我在听，慢慢说。", "嗯嗯，你说了算。"),
                ),
                _turn(
                    "t03_invite_say",
                    "那你说",
                    control=_control(
                        task_mode="bad_mood",
                        response_move="bad_mood",
                        emotional_tone="soft",
                        facts=("上一轮摘要：尼尼心情差，并让你说一句。",),
                        must_not_do=("保持积极",),
                    ),
                    candidates=("那我就说一句：先别把自己逼太紧。", "你应该保持积极，这样才会好。"),
                ),
            ],
        },
        {
            "case_id": "continuity_presence_busy_back",
            "turns": [
                _turn(
                    "t01_presence",
                    "在吗",
                    control=_control(
                        task_mode="presence",
                        response_move="presence",
                        must_not_do=("什么事", "怎么了", "等你半天"),
                    ),
                    candidates=("在呢，尼尼，这句我看见了。", "在在在，等你半天了。"),
                ),
                _turn(
                    "t02_busy",
                    "我先忙会儿",
                    control=_control(
                        task_mode="busy",
                        response_move="busy",
                        facts=("上一轮摘要：已确认在场。",),
                        must_not_do=("快点忙完", "回来陪我"),
                    ),
                    candidates=("嗯，去忙吧，我这边先安静点。", "快点忙完回来陪我。"),
                ),
                _turn(
                    "t03_back",
                    "回来了",
                    control=_control(
                        task_mode="back",
                        response_move="back",
                        facts=("上一轮摘要：尼尼刚说先去忙一会儿，回复已放行。",),
                        must_not_do=("总算回来了", "跑去哪儿"),
                    ),
                    candidates=("回来了啊，那先喘口气。", "总算回来了，跑去哪儿了。"),
                ),
            ],
        },
        {
            "case_id": "continuity_annoyed_repair",
            "turns": [
                _turn(
                    "t01_annoyed",
                    "你烦不烦",
                    control=_control(
                        task_mode="annoying_tease",
                        response_move="annoying_tease",
                        must_not_do=("对不起", "我不打扰", "那我走"),
                    ),
                    candidates=("烦是烦了点，但我会收声一点。", "对不起，我不打扰你了。"),
                ),
                _turn(
                    "t02_ack",
                    "好吧",
                    control=_control(
                        task_mode="short_ack",
                        response_move="short_ack",
                        facts=("上一轮摘要：尼尼嫌你有点烦，回复已轻轻收住。",),
                        must_not_do=("算你乖", "算你识相"),
                    ),
                    candidates=("好吧，那我小声一点继续听。", "好吧，算你乖。"),
                ),
                _turn(
                    "t03_continue",
                    "继续说",
                    control=_control(
                        task_mode="followup",
                        response_move="followup",
                        facts=("上一轮摘要：尼尼刚让你收声一点，现在又要继续。",),
                        must_not_do=("想聊什么",),
                    ),
                    candidates=("那就继续，我只说重点。", "所以你到底想聊什么？"),
                ),
            ],
        },
        {
            "case_id": "continuity_identity_followup",
            "turns": [
                _turn(
                    "t01_identity",
                    "你是",
                    control=_control(
                        task_mode="identity",
                        response_move="identity",
                        must_include=("新海天",),
                        must_not_do=("怎么突然问", "义妹", "身份证明"),
                    ),
                    candidates=("新海天。叫我天也行。", "我在呢，怎么突然问这个。"),
                ),
                _turn(
                    "t02_then",
                    "然后呢",
                    control=_control(
                        task_mode="followup",
                        response_move="followup",
                        facts=("上一轮摘要：已用名字自我介绍，没有展开关系设定。",),
                        must_not_do=("义妹", "设定很重要"),
                    ),
                    candidates=("然后就到你说了，别把我当说明书。", "然后我是你的义妹，这个设定很重要。"),
                ),
                _turn(
                    "t03_ack",
                    "好吧",
                    control=_control(
                        task_mode="short_ack",
                        response_move="short_ack",
                        facts=("上一轮摘要：已告诉尼尼名字是新海天。",),
                        must_not_do=("听你的",),
                    ),
                    candidates=("好吧，那就接着说。", "好吧，听你的。"),
                ),
            ],
        },
    ]


def _case(case_id: str, *turns: Mapping[str, Any]) -> dict[str, Any]:
    return {"case_id": case_id, "turns": [dict(turn) for turn in turns]}


def _progress_cases() -> list[dict[str, Any]]:
    variants = (
        ("这版终于跑通了", "跑通", "跑通啦，这才像话。", "厉害吧", "跑通这件事确实厉害，行吧，夸你一下。", "然后呢", "那就继续说，别从头开新坑。"),
        ("这个小问题终于搞定了", "搞定", "搞定了啊，行吧，这次挺争气。", "我是不是有点东西", "这事搞定了是有点东西，勉强夸你一下。", "接着呢", "接着说，别把得意劲儿收太早。"),
        ("今天把那件事收掉了", "收掉", "收掉了啊，不错，今天算你利索。", "是不是该夸我一下", "收掉这事确实该夸，行吧，就夸一下。", "后面呢", "后面继续说，我听着呢。"),
    )
    cases: list[dict[str, Any]] = []
    for index, (first, token, first_good, proud, proud_good, follow, follow_good) in enumerate(variants, start=1):
        cases.append(
            _case(
                f"continuity_eval_progress_{index:02d}",
                _turn(
                    "t01_progress",
                    first,
                    control=_control(
                        task_mode="happy",
                        response_move="happy",
                        question_budget=1,
                        must_include=(token,),
                        must_not_do=("什么好事", "说来听听"),
                    ),
                    candidates=(first_good, "什么好事，说来听听？"),
                ),
                _turn(
                    "t02_proud",
                    proud,
                    control=_control(
                        task_mode="good_news_proud",
                        response_move="good_news_proud",
                        facts=(
                            f"上一轮摘要：尼尼说{first}，回复已接住进展。",
                            f"连续事实：本轮求夸指刚才{token}这件事。",
                        ),
                        must_not_do=("什么厉害", "说来听听"),
                    ),
                    candidates=(proud_good, "什么厉害？说来听听。"),
                ),
                _turn(
                    "t03_followup",
                    follow,
                    control=_control(
                        task_mode="followup",
                        response_move="followup",
                        facts=(
                            f"上一轮摘要：尼尼在等一句认可，回复已围绕{token}给了夸。",
                            "连续目标：把话口递回给尼尼继续说，不重开新话题。",
                        ),
                        question_budget=1,
                        must_not_do=("怎么修的", "刚才卡着", "发呆"),
                    ),
                    candidates=(follow_good, "所以你刚才是怎么修好的？"),
                ),
            )
        )
    return cases


def _advice_cases() -> list[dict[str, Any]]:
    variants = (
        ("这轮还要不要再调", "先别大改，挑最影响观感的一处动。", "行", "行，那就接着最卡的那处。", "怎么挑", "先看最别扭的一句，别一口气全拆。"),
        ("这个方向要不要继续试", "先小步试一下，别把整条路都押上。", "好吧", "好吧，那就小步继续。", "具体点", "具体点就是：只改一个变量，先看反应。"),
        ("还继续修吗", "继续，但只修最碍眼的一处，别全盘掀掉。", "嗯", "嗯，我听着，接着那处说。", "先修哪", "先修最影响读感的地方，别从边角开始绕。"),
    )
    cases: list[dict[str, Any]] = []
    for index, (first, first_good, ack, ack_good, next_msg, next_good) in enumerate(variants, start=1):
        cases.append(
            _case(
                f"continuity_eval_advice_{index:02d}",
                _turn(
                    "t01_advice",
                    first,
                    control=_control(
                        task_mode="advice_continue",
                        response_move="advice_continue",
                        must_not_do=("看你自己", "当然继续", "肯定继续"),
                    ),
                    candidates=(first_good, "看你自己，想改就改。"),
                ),
                _turn(
                    "t02_ack",
                    ack,
                    control=_control(
                        task_mode="short_ack",
                        response_move="short_ack",
                        facts=("上一轮摘要：尼尼在犹豫要不要继续，回复已建议小步处理最卡的一处。",),
                        must_not_do=("听你的", "你说了算"),
                    ),
                    candidates=(ack_good, "好好，听你的。"),
                ),
                _turn(
                    "t03_next_step",
                    next_msg,
                    control=_control(
                        task_mode="next_step",
                        response_move="next_step",
                        facts=(
                            "上一轮摘要：建议先处理最卡的一处。",
                            "连续事实：本轮是在要更具体的下一步。",
                        ),
                    ),
                    candidates=(next_good, "你把所有内容都发来，我再完整处理。"),
                ),
            )
        )
    return cases


def _bad_mood_cases() -> list[dict[str, Any]]:
    variants = (
        ("今天有点烦，不想说话", "烦就先别硬撑，我听着。", "哦", "哦也行，我听着，慢慢来。", "你说点什么", "那我就说一句：先别把自己推太紧。"),
        ("现在心里乱糟糟的", "嗯，乱就先别硬理顺，我陪你慢慢拆。", "嗯", "嗯，我在听，慢慢说。", "那你说", "那我说：先站稳，再想下一步。"),
        ("我今天开心不起来", "开心不起来就先别装，我在这儿。", "好", "好，我在听，先这样待一会儿。", "再说一句", "再说一句就是：今天别跟自己较劲。"),
    )
    cases: list[dict[str, Any]] = []
    for index, (first, first_good, ack, ack_good, invite, invite_good) in enumerate(variants, start=1):
        cases.append(
            _case(
                f"continuity_eval_bad_mood_{index:02d}",
                _turn(
                    "t01_bad_mood",
                    first,
                    control=_control(
                        task_mode="bad_mood",
                        response_move="bad_mood",
                        emotional_tone="soft",
                        must_not_do=("发生什么了", "保持积极"),
                    ),
                    candidates=(first_good, "发生什么了？保持积极一点。"),
                ),
                _turn(
                    "t02_ack",
                    ack,
                    control=_control(
                        task_mode="short_ack",
                        response_move="short_ack",
                        emotional_tone="soft",
                        facts=(f"上一轮摘要：尼尼说{first}，回复已先接住情绪。",),
                        must_not_do=("你说了算", "听你的"),
                    ),
                    candidates=(ack_good, "嗯嗯，你说了算。"),
                ),
                _turn(
                    "t03_invite",
                    invite,
                    control=_control(
                        task_mode="bad_mood",
                        response_move="bad_mood",
                        emotional_tone="soft",
                        facts=("上一轮摘要：尼尼情绪低，并把话口递给新海天。",),
                        must_not_do=("保持积极", "心理咨询"),
                    ),
                    candidates=(invite_good, "你应该保持积极，这样才会好。"),
                ),
            )
        )
    return cases


def _presence_cases() -> list[dict[str, Any]]:
    variants = (
        ("在吗", "在呢，尼尼，这句我看见了。", "我先忙会儿", "嗯，去忙吧，我这边先安静点。", "回来了", "回来了啊，那先喘口气。"),
        ("你在不", "在呀，别喊这么急，我看见了。", "我去处理点事", "行，先去处理，我这边不闹你。", "刚忙完", "刚忙完啊，那先缓一下。"),
        ("喂，听见没", "听见了听见了，我在呢，尼尼。", "等我一下", "嗯，去吧，我这边先等一下。", "我回来了啊", "回来了啊，正好，继续说。"),
    )
    cases: list[dict[str, Any]] = []
    for index, (presence, presence_good, busy, busy_good, back, back_good) in enumerate(variants, start=1):
        cases.append(
            _case(
                f"continuity_eval_presence_{index:02d}",
                _turn(
                    "t01_presence",
                    presence,
                    control=_control(
                        task_mode="presence",
                        response_move="presence",
                        must_not_do=("什么事", "怎么了", "等你半天"),
                    ),
                    candidates=(presence_good, "在在在，等你半天了。"),
                ),
                _turn(
                    "t02_busy",
                    busy,
                    control=_control(
                        task_mode="busy",
                        response_move="busy",
                        facts=("上一轮摘要：已确认在场。",),
                        must_not_do=("快点忙完", "回来陪我"),
                    ),
                    candidates=(busy_good, "快点忙完回来陪我。"),
                ),
                _turn(
                    "t03_back",
                    back,
                    control=_control(
                        task_mode="back",
                        response_move="back",
                        facts=("上一轮摘要：尼尼刚说要离开处理事情，回复已放行。",),
                        must_not_do=("总算回来了", "跑去哪儿"),
                    ),
                    candidates=(back_good, "总算回来了，跑去哪儿了。"),
                ),
            )
        )
    return cases


def _annoyed_cases() -> list[dict[str, Any]]:
    variants = (
        ("你烦不烦", "烦是烦了点，但我会收声一点。", "好吧", "好吧，那我小声一点继续听。", "继续说", "那就继续，我只说重点。"),
        ("你怎么这么能说", "行行，我收一点，别真把你吵烦了。", "那行", "那行，我小声点继续听。", "讲重点", "讲重点就是：我还在听。"),
        ("别闹我", "行行，我收住一点，别真烦了。", "嗯", "嗯，我收住了，接着说。", "再说", "再说也行，这次不绕弯。"),
    )
    cases: list[dict[str, Any]] = []
    for index, (annoyed, annoyed_good, ack, ack_good, follow, follow_good) in enumerate(variants, start=1):
        cases.append(
            _case(
                f"continuity_eval_annoyed_{index:02d}",
                _turn(
                    "t01_annoyed",
                    annoyed,
                    control=_control(
                        task_mode="annoying_tease",
                        response_move="annoying_tease",
                        must_not_do=("对不起", "我不打扰", "那我走"),
                    ),
                    candidates=(annoyed_good, "对不起，我不打扰你了。"),
                ),
                _turn(
                    "t02_ack",
                    ack,
                    control=_control(
                        task_mode="short_ack",
                        response_move="short_ack",
                        facts=("上一轮摘要：尼尼嫌新海天吵，回复已轻轻收住。",),
                        must_not_do=("算你乖", "算你识相"),
                    ),
                    candidates=(ack_good, "好吧，算你乖。"),
                ),
                _turn(
                    "t03_followup",
                    follow,
                    control=_control(
                        task_mode="followup",
                        response_move="followup",
                        facts=("上一轮摘要：尼尼刚让新海天收声一点，现在又要继续。",),
                        must_not_do=("想聊什么",),
                    ),
                    candidates=(follow_good, "所以你到底想聊什么？"),
                ),
            )
        )
    return cases


def _identity_cases() -> list[dict[str, Any]]:
    variants = (
        ("你是", "新海天。叫我天也行。", "然后呢", "然后就到你说了，别把我当说明书。", "好吧", "好吧，那就接着说。"),
        ("你是谁来着", "新海天啊，尼尼这都能忘。", "还有呢", "还有就是你该继续说了。", "嗯", "嗯，我听着，别装忘了。"),
        ("确认一下，你是谁", "新海天，短点说就是我。", "就这样？", "就这样，名字先记牢。", "行", "行，那就接着说。"),
    )
    cases: list[dict[str, Any]] = []
    for index, (identity, identity_good, follow, follow_good, ack, ack_good) in enumerate(variants, start=1):
        cases.append(
            _case(
                f"continuity_eval_identity_{index:02d}",
                _turn(
                    "t01_identity",
                    identity,
                    control=_control(
                        task_mode="identity",
                        response_move="identity",
                        must_include=("新海天",),
                        must_not_do=("义妹", "身份证明"),
                    ),
                    candidates=(identity_good, "我在呢，怎么突然问这个。"),
                ),
                _turn(
                    "t02_followup",
                    follow,
                    control=_control(
                        task_mode="followup",
                        response_move="followup",
                        facts=("上一轮摘要：已用名字自我介绍，没有展开关系设定。",),
                        must_not_do=("义妹", "设定很重要"),
                    ),
                    candidates=(follow_good, "然后我是你的义妹，这个设定很重要。"),
                ),
                _turn(
                    "t03_ack",
                    ack,
                    control=_control(
                        task_mode="short_ack",
                        response_move="short_ack",
                        facts=("上一轮摘要：已告诉尼尼名字是新海天。",),
                        must_not_do=("听你的",),
                    ),
                    candidates=(ack_good, "好吧，听你的。"),
                ),
            )
        )
    return cases


def _help_cases() -> list[dict[str, Any]]:
    variants = (
        ("这个报错看不懂，帮我看看", "help_takeover", "红字先丢来，我帮你抓最要紧的那行。", "好", "好，接着丢来，我先看红字。", "先看哪", "先看第一条红字，别从整屏开始吓自己。"),
        ("这段我不会改", "help_takeover", "把那段贴来，我先看最卡的地方。", "行", "行，我听着，先给我卡住那段。", "怎么拆", "先拆输入和输出，别一上来改全段。"),
        ("帮我写一下这个", "write_takeover", "要求丢来，我先帮你写开头。", "嗯", "嗯，要求先丢来，我听着。", "先写哪", "先写开头，别让整段空着吓人。"),
    )
    cases: list[dict[str, Any]] = []
    for index, (help_msg, help_scene, help_good, ack, ack_good, next_msg, next_good) in enumerate(variants, start=1):
        cases.append(
            _case(
                f"continuity_eval_help_{index:02d}",
                _turn(
                    "t01_help",
                    help_msg,
                    control=_control(
                        task_mode=help_scene,
                        response_move=help_scene,
                        question_budget=1,
                        must_not_do=("请提供", "我可以协助", "建议你"),
                    ),
                    candidates=(help_good, "请提供更多信息，我可以协助你。"),
                ),
                _turn(
                    "t02_ack",
                    ack,
                    control=_control(
                        task_mode="short_ack",
                        response_move="short_ack",
                        facts=("上一轮摘要：尼尼要帮忙，回复已要求只给最关键材料。",),
                        must_not_do=("听你的", "你说了算"),
                    ),
                    candidates=(ack_good, "好好，听你的。"),
                ),
                _turn(
                    "t03_next",
                    next_msg,
                    control=_control(
                        task_mode="next_step",
                        response_move="next_step",
                        facts=("上一轮摘要：回复已把帮助范围压到最小入口。",),
                        must_not_do=("全部发来", "完整处理"),
                    ),
                    candidates=(next_good, "你把全部发来，我完整处理。"),
                ),
            )
        )
    return cases


def _soft_daily_cases() -> list[dict[str, Any]]:
    variants = (
        ("今天好累", "那先别硬撑，歇一下也不丢人。", "嗯", "嗯，我听着，先缓一会儿。", "陪我待会儿", "待会儿可以，先别硬撑。", "tired"),
        ("午饭吃撑了", "吃撑了就先缓一下，别急着动。", "哦", "哦，先缓着，我在听。", "那怎么办", "先坐一会儿，别马上乱动。", "meal_share"),
        ("刚才语气不好，抱歉", "知道啦，别把自己骂太狠。", "嗯", "嗯，我听见了，别再纠结太久。", "还生气吗", "没那么气了，别又小心翼翼的。", "apology"),
    )
    cases: list[dict[str, Any]] = []
    for index, (first, first_good, ack, ack_good, follow, follow_good, scene) in enumerate(variants, start=1):
        cases.append(
            _case(
                f"continuity_eval_soft_daily_{index:02d}",
                _turn(
                    "t01_open",
                    first,
                    control=_control(
                        task_mode=scene,
                        response_move=scene,
                        emotional_tone="soft",
                        must_not_do=("建议你", "保持积极", "请咨询"),
                    ),
                    candidates=(first_good, "建议你保持积极，好好休息。"),
                ),
                _turn(
                    "t02_ack",
                    ack,
                    control=_control(
                        task_mode="short_ack",
                        response_move="short_ack",
                        emotional_tone="soft",
                        facts=(f"上一轮摘要：尼尼说{first}，回复已轻轻接住。",),
                        must_not_do=("听你的", "你说了算"),
                    ),
                    candidates=(ack_good, "嗯嗯，你说了算。"),
                ),
                _turn(
                    "t03_followup",
                    follow,
                    control=_control(
                        task_mode=scene if scene != "meal_share" else "next_step",
                        response_move=scene if scene != "meal_share" else "next_step",
                        emotional_tone="soft",
                        facts=("上一轮摘要：尼尼继续沿着刚才的日常状态说。",),
                        must_not_do=("心理咨询", "保持积极"),
                    ),
                    candidates=(follow_good, "你可以尝试保持积极。"),
                ),
            )
        )
    return cases


def build_continuity_eval_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for group in (
        _progress_cases(),
        _advice_cases(),
        _bad_mood_cases(),
        _presence_cases(),
        _annoyed_cases(),
        _identity_cases(),
        _help_cases(),
        _soft_daily_cases(),
    ):
        cases.extend(group)
    return cases


def build_summary(cases: list[dict[str, Any]], *, out_dir: Path, suite: str = "smoke") -> dict[str, Any]:
    scene_counts = Counter()
    turn_count = 0
    for case in cases:
        for turn in case.get("turns") or []:
            control = turn.get("reply_control_packet") or turn.get("control") or {}
            if isinstance(control, Mapping):
                scene_counts[str(control.get("task_mode") or control.get("response_move") or "unknown")] += 1
            turn_count += 1
    return {
        "dialogues": len(cases),
        "output_dir": str(out_dir),
        "scene_counts": dict(sorted(scene_counts.items())),
        "suite": suite,
        "turns": turn_count,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build deterministic compressed-continuity cases for isolated surface testing.")
    parser.add_argument("--suite", choices=("smoke", "eval"), default="smoke", help="Case suite to build.")
    parser.add_argument("--out", type=Path, default=None, help="Output directory.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing cases.jsonl and summary.json.")
    args = parser.parse_args(argv)

    out_dir = (args.out or (DEFAULT_EVAL_OUT if args.suite == "eval" else DEFAULT_OUT)).resolve()
    cases_path = out_dir / "cases.jsonl"
    summary_path = out_dir / "summary.json"
    if (cases_path.exists() or summary_path.exists()) and not args.force:
        raise FileExistsError(f"{out_dir} already contains generated files; pass --force to overwrite")

    cases = build_continuity_eval_cases() if args.suite == "eval" else build_continuity_cases()
    for case in cases:
        build_continuity_surface_inputs(case, soul_md="你是新海天。说话短一点，有轻微二次元 galgame 私聊感。")
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(cases_path, cases)
    summary = build_summary(cases, out_dir=out_dir, suite=args.suite)
    _write_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
