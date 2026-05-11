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


def build_summary(cases: list[dict[str, Any]], *, out_dir: Path) -> dict[str, Any]:
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
        "turns": turn_count,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build deterministic compressed-continuity cases for isolated surface testing.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output directory.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing cases.jsonl and summary.json.")
    args = parser.parse_args(argv)

    out_dir = args.out.resolve()
    cases_path = out_dir / "cases.jsonl"
    summary_path = out_dir / "summary.json"
    if (cases_path.exists() or summary_path.exists()) and not args.force:
        raise FileExistsError(f"{out_dir} already contains generated files; pass --force to overwrite")

    cases = build_continuity_cases()
    for case in cases:
        build_continuity_surface_inputs(case, soul_md="你是新海天。说话短一点，有轻微二次元 galgame 私聊感。")
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(cases_path, cases)
    summary = build_summary(cases, out_dir=out_dir)
    _write_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
