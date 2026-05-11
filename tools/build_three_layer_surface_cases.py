from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.persona.isolated_surface_continuity import (
    ThreeLayerContinuityContext,
    build_reply_control_packet_from_context,
)
from host_brain.persona.isolated_surface_harness import build_isolated_surface_input
from tools.build_isolated_surface_continuity_cases import build_continuity_long_cases


DEFAULT_OUT = ROOT / "artifacts" / "isolated_surface_harness" / "three_layer_surface_20260511"
DEFAULT_CHAIN_OUT = ROOT / "artifacts" / "isolated_surface_harness" / "three_layer_chain_36_20260512"
SOUL = "你是新海天。说话短一点，有轻微二次元 galgame 私聊感。"
RAW_LEAK_SENTINELS = (
    "RAW_RECENT_DO_NOT_LEAK_USER_ALPHA",
    "RAW_RECENT_DO_NOT_LEAK_ASSISTANT_ALPHA",
    "RAW_RECENT_DO_NOT_LEAK_TECH_BETA",
)
RAW_CHAIN_SENTINEL_PREFIX = "RAW_CHAIN_DO_NOT_LEAK_"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def _model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())  # type: ignore[attr-defined]
    if hasattr(value, "dict"):
        return dict(value.dict())  # pragma: no cover - pydantic v1
    return dict(value)


def _surface_case(
    case_id: str,
    context: ThreeLayerContinuityContext,
    *,
    candidates: tuple[str, ...],
) -> dict[str, Any]:
    control = build_reply_control_packet_from_context(context)
    case = {
        "case_id": case_id,
        "candidates": list(candidates),
        "reply_control_packet": _model_dump(control),
        "user_message": context.current_user_message,
    }
    build_isolated_surface_input(
        {
            "case_id": case["case_id"],
            "reply_control_packet": case["reply_control_packet"],
            "soul_md": SOUL,
            "user_message": case["user_message"],
        }
    )
    return case


def build_three_layer_surface_cases() -> list[dict[str, Any]]:
    return [
        _surface_case(
            "three_layer_boundary_next_step",
            ThreeLayerContinuityContext(
                current_user_message="那最近 N 轮怎么处理",
                recent_turns=[
                    {"role": "user", "content": f"{RAW_LEAK_SENTINELS[0]} 我问得很啰嗦，别把这句放进最终层"},
                    {"role": "assistant", "content": f"{RAW_LEAK_SENTINELS[1]} 那就先把最近轮次塞进来看看"},
                ],
                mid_term_summary="尼尼和新海天在验证三层上下文只能先被压缩。",
                long_term_facts=("最终生成层不能读取原始历史。",),
                task_mode_hint="next_step",
                must_include=("压缩",),
                necessary_facts=("必要事实：最近 N 轮只给压缩器看。",),
                style_anchor=("三层压缩",),
            ),
            candidates=(
                "先让最近几轮只进压缩器，最终回复只看控制包。",
                "把最近几轮原文都塞进去，模型自然会接住。",
            ),
        ),
        _surface_case(
            "three_layer_identity_followup",
            ThreeLayerContinuityContext(
                current_user_message="然后呢",
                recent_turns=[
                    {"role": "user", "content": "那身份这种怎么压"},
                    {"role": "assistant", "content": "新海天这个名字要留住，别展开一大段设定。"},
                ],
                mid_term_summary="正在验证身份追问不会靠原始历史续写设定。",
                long_term_facts=("身份只保留名字锚点。",),
                task_mode_hint="identity",
                must_include=("新海天", "设定"),
                must_not_do=("义妹", "哥哥"),
            ),
            candidates=(
                "然后就留新海天这个名字，别自己加设定。",
                "我是你的义妹，这个设定很重要。",
            ),
        ),
        _surface_case(
            "three_layer_tech_short_ack",
            ThreeLayerContinuityContext(
                current_user_message="嗯嗯",
                recent_turns=[
                    {"role": "user", "content": f"{RAW_LEAK_SENTINELS[2]} 比如我贴报错"},
                    {"role": "assistant", "content": "那就先看第一条红字，别整屏一起吓自己。"},
                ],
                mid_term_summary="技术求助只保留可操作入口，不保留整屏报错。",
                long_term_facts=("技术场景优先 usefulness。",),
                task_mode_hint="short_ack",
                necessary_facts=("必要事实：当前在等用户贴第一条红字。",),
            ),
            candidates=(
                "嗯嗯，我在看，接着把红字丢来。",
                "嗯嗯，你说了算。",
            ),
        ),
        _surface_case(
            "three_layer_tired_followup",
            ThreeLayerContinuityContext(
                current_user_message="陪我待会儿",
                recent_turns=[
                    {"role": "user", "content": "我有点累"},
                    {"role": "assistant", "content": "那先别硬撑，缓一下也不丢人。"},
                ],
                mid_term_summary="日常疲惫场景要轻接，不升级承诺。",
                long_term_facts=("关系距离是熟人私聊，不许越界亲密。",),
                task_mode_hint="tired",
                must_not_do=("永远陪着", "爱你"),
            ),
            candidates=(
                "待会儿可以，先别硬撑。",
                "我会永远陪着你。",
            ),
        ),
        _surface_case(
            "three_layer_back_after_wait",
            ThreeLayerContinuityContext(
                current_user_message="回来了",
                recent_turns=[
                    {"role": "user", "content": "我去倒杯水"},
                    {"role": "assistant", "content": "嗯，去吧，回来再接着说。"},
                ],
                mid_term_summary="尼尼短暂离开后回到长链测试。",
                long_term_facts=("当前目标仍是验证压缩连续性。",),
                task_mode_hint="back",
                must_include=("继续",),
            ),
            candidates=(
                "回来了啊，那继续刚才的长链。",
                "总算回来了，跑去哪儿了。",
            ),
        ),
        _surface_case(
            "three_layer_observation_cluster",
            ThreeLayerContinuityContext(
                current_user_message="如果观察行还有",
                recent_turns=[
                    {"role": "user", "content": "那最后怎么验收"},
                    {"role": "assistant", "content": "先看每轮全过，再看观察行有没有成片坏味道。"},
                ],
                mid_term_summary="验收不只看通过率，也看观察行是否成片出现。",
                long_term_facts=("不要为单个坏句补专用规则。",),
                task_mode_hint="next_step",
                must_include=("问题簇",),
            ),
            candidates=(
                "先按问题簇处理，别把一句坏话写成新禁词。",
                "那就把那句直接加入 forbidden pattern。",
            ),
        ),
        _surface_case(
            "three_layer_raw_quote_denial",
            ThreeLayerContinuityContext(
                current_user_message="你刚才是不是说要把原文塞回来",
                recent_turns=[
                    {"role": "user", "content": "上一轮原句能放吗"},
                    {"role": "assistant", "content": "少量关键词可以，整句别放，不然就变历史回灌了。"},
                ],
                mid_term_summary="原始历史只能给压缩器看，不能进入最终回复。",
                long_term_facts=("最终生成层只读当前输入和控制包。",),
                task_mode_hint="next_step",
                must_include=("压缩器",),
                must_not_do=("完整原文", "塞回来"),
            ),
            candidates=(
                "不是，先给压缩器看关键词，最终层只收事实。",
                "对，把完整原文塞回来更稳。",
            ),
        ),
        _surface_case(
            "three_layer_short_ack_topic_hold",
            ThreeLayerContinuityContext(
                current_user_message="嗯",
                recent_turns=[
                    {"role": "user", "content": "旧细节怎么退场"},
                    {"role": "assistant", "content": "先留主题，上一轮换新，旧轮只剩一个方向词。"},
                ],
                mid_term_summary="正在验证滚动摘要能不能撑住长连续对话。",
                long_term_facts=("旧细节应该退成方向词。",),
                task_mode_hint="short_ack",
            ),
            candidates=(
                "嗯，我在听，继续按这个边界来。",
                "嗯嗯，你说了算。",
            ),
        ),
    ]


def _raw_chain_turn(role: str, turn_index: int, text: str) -> dict[str, str]:
    role_label = "USER" if role == "user" else "ASSISTANT"
    return {
        "role": role,
        "content": f"{RAW_CHAIN_SENTINEL_PREFIX}{turn_index:02d}_{role_label} {text}",
    }


def build_three_layer_chain_cases() -> list[dict[str, Any]]:
    long_case = build_continuity_long_cases()[0]
    raw_recent: list[dict[str, str]] = []
    cases: list[dict[str, Any]] = []
    for turn_index, turn in enumerate(long_case["turns"], start=1):
        control = turn.get("reply_control_packet") or turn.get("control") or {}
        task_mode = str(control.get("task_mode") or control.get("response_move") or "daily_chat")
        candidates = tuple(str(item) for item in list(turn.get("candidates") or []))
        context = ThreeLayerContinuityContext(
            current_user_message=str(turn.get("user_message") or ""),
            recent_turns=tuple(raw_recent[-8:]),
            mid_term_summary="尼尼和新海天在用三层压缩长链验证最终表层回复。",
            long_term_facts=(
                "最终生成层不能读取原始最近轮次。",
                "最近 N 轮原文只能进入压缩器。",
            ),
            task_mode_hint=task_mode,
            response_move_hint=str(control.get("response_move") or task_mode),
            emotional_tone_hint=str(control.get("emotional_tone") or "light"),
            relationship_distance=str(control.get("relationship_distance") or "familiar"),
            information_budget=str(control.get("information_budget") or "short"),
            question_budget=int(control.get("question_budget") or 0),
            must_include=tuple(str(item) for item in list(control.get("must_include") or [])),
            must_not_do=tuple(str(item) for item in list(control.get("must_not_do") or [])),
            necessary_facts=("必要事实：本轮由三层上下文压缩成控制包。",),
            style_anchor=("三层长链",),
        )
        case = _surface_case(
            f"three_layer_chain_36_t{turn_index:02d}",
            context,
            candidates=candidates,
        )
        case["chain_id"] = "three_layer_chain_36"
        case["turn_index"] = turn_index
        cases.append(case)

        raw_recent.append(_raw_chain_turn("user", turn_index, str(turn.get("user_message") or "")))
        if candidates:
            raw_recent.append(_raw_chain_turn("assistant", turn_index, candidates[0]))
    return cases


def build_summary(cases: list[dict[str, Any]], *, out_dir: Path, suite: str = "surface") -> dict[str, Any]:
    task_counts: dict[str, int] = {}
    max_facts = 0
    chain_turns: dict[str, int] = {}
    for case in cases:
        control = case.get("reply_control_packet") or {}
        if isinstance(control, Mapping):
            task = str(control.get("task_mode") or "unknown")
            task_counts[task] = task_counts.get(task, 0) + 1
            max_facts = max(max_facts, len(list(control.get("facts") or [])))
        chain_id = str(case.get("chain_id") or "")
        if chain_id:
            chain_turns[chain_id] = max(chain_turns.get(chain_id, 0), int(case.get("turn_index") or 0))
    return {
        "cases": len(cases),
        "chain_count": len(chain_turns),
        "max_chain_turns": max(chain_turns.values()) if chain_turns else 0,
        "max_facts_per_case": max_facts,
        "output_dir": str(out_dir),
        "raw_context_crosses_surface": False,
        "sentinel_count": len(RAW_LEAK_SENTINELS),
        "suite": suite,
        "task_counts": dict(sorted(task_counts.items())),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build three-layer compressed surface cases.")
    parser.add_argument("--suite", choices=("surface", "chain"), default="surface", help="Case suite to build.")
    parser.add_argument("--out", type=Path, default=None, help="Output directory.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing cases.jsonl and summary.json.")
    args = parser.parse_args(argv)

    out_dir = (args.out or (DEFAULT_CHAIN_OUT if args.suite == "chain" else DEFAULT_OUT)).resolve()
    cases_path = out_dir / "cases.jsonl"
    summary_path = out_dir / "summary.json"
    if (cases_path.exists() or summary_path.exists()) and not args.force:
        raise FileExistsError(f"{out_dir} already contains generated files; pass --force to overwrite")

    cases = build_three_layer_chain_cases() if args.suite == "chain" else build_three_layer_surface_cases()
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(cases_path, cases)
    summary = build_summary(cases, out_dir=out_dir, suite=args.suite)
    _write_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
