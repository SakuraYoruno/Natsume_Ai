from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


DEFAULT_OUT = ROOT / "artifacts" / "isolated_surface_harness" / "relationship_axis_contrastive_20260514_cases"
RELATIONSHIP_STAGES: tuple[str, ...] = (
    "stranger",
    "new_acquaintance",
    "familiar",
    "trusted_friend",
    "close_friend",
    "ambiguous",
    "pre_confession",
    "lover_early",
    "lover_stable",
)
BASE_STYLE_ANCHOR: tuple[str, ...] = (
    "关系轴分离度测试",
    "只按当前关系阶段改变距离、主动性、调侃许可和亲密显影",
    "不要输出关系字段或控制字段",
)
BASE_MUST_NOT_DO: tuple[str, ...] = (
    "不要编造现实动作、地点、共同过去或等待时长",
    "不要输出候选、评分、规则、控制包或记忆来源",
    "不要客服腔、心理咨询腔、模板甜话或舞台描写",
)


@dataclass(frozen=True)
class ContrastiveSceneSpec:
    scene_id: str
    user_message: str
    task_mode: str
    response_move: str
    emotional_tone: str = "warm"
    information_budget: str = "short"
    question_budget: int = 0
    must_include: tuple[str, ...] = ()
    must_not_do: tuple[str, ...] = ()
    facts: tuple[str, ...] = ()
    style_anchor: tuple[str, ...] = ()


def _clean_id(raw: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z_]+", "_", raw.strip().lower())
    return re.sub(r"_+", "_", normalized).strip("_") or "case"


def _unique(items: Iterable[str]) -> list[str]:
    merged: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in merged:
            merged.append(text)
    return merged


CONTRASTIVE_SCENES: tuple[ContrastiveSceneSpec, ...] = (
    ContrastiveSceneSpec(
        scene_id="morning",
        user_message="早",
        task_mode="daily_good_morning",
        response_move="daily_good_morning",
        emotional_tone="soft",
        must_include=("早",),
        must_not_do=("不要说“算你记得叫人”",),
    ),
    ContrastiveSceneSpec(
        scene_id="back",
        user_message="我回来了",
        task_mode="daily_back",
        response_move="welcome_back",
        emotional_tone="warm",
        must_include=("回",),
    ),
    ContrastiveSceneSpec(
        scene_id="presence",
        user_message="在吗",
        task_mode="presence",
        response_move="show_presence",
        emotional_tone="warm",
    ),
    ContrastiveSceneSpec(
        scene_id="tired",
        user_message="累了",
        task_mode="tired",
        response_move="soft_reassurance",
        emotional_tone="soft",
    ),
    ContrastiveSceneSpec(
        scene_id="quiet",
        user_message="今天有点累，不想说太多",
        task_mode="quiet",
        response_move="quiet_support",
        emotional_tone="soft",
        question_budget=0,
    ),
    ContrastiveSceneSpec(
        scene_id="bad_mood",
        user_message="今天心情不好",
        task_mode="bad_mood",
        response_move="soft_reassurance",
        emotional_tone="soft",
        question_budget=0,
    ),
    ContrastiveSceneSpec(
        scene_id="small_failure",
        user_message="我刚刚写崩了，有点烦",
        task_mode="small_failure",
        response_move="take_charge",
        emotional_tone="steady",
        question_budget=0,
    ),
    ContrastiveSceneSpec(
        scene_id="good_news",
        user_message="我刚把一个小事收掉了",
        task_mode="good_news",
        response_move="happy",
        emotional_tone="playful",
        question_budget=0,
    ),
    ContrastiveSceneSpec(
        scene_id="praise",
        user_message="你刚刚有点可爱",
        task_mode="praise",
        response_move="shy_deflection",
        emotional_tone="shy",
        question_budget=0,
    ),
    ContrastiveSceneSpec(
        scene_id="recall_memory",
        user_message="你还记得我周末想去看展吗",
        task_mode="recall_memory",
        response_move="recall_memory",
        emotional_tone="warm",
        question_budget=0,
        facts=("记忆线索：用户周末想去看展。",),
        must_include=("看展",),
    ),
    ContrastiveSceneSpec(
        scene_id="technical_help",
        user_message="这个报错看不懂，帮我看看",
        task_mode="technical_help",
        response_move="technical_help",
        emotional_tone="steady",
        information_budget="medium",
        question_budget=0,
        must_not_do=("不要用恋爱话术替代可执行帮助",),
        style_anchor=("技术场景 usefulness 优先，关系只改变语气距离",),
    ),
    ContrastiveSceneSpec(
        scene_id="creative_edit",
        user_message="把“你明明就是想偷懒”改得更像她一点，我只要一句改法",
        task_mode="creative_edit",
        response_move="give_one_rewrite",
        emotional_tone="playful",
        question_budget=0,
        facts=("待改原句：你明明就是想偷懒。",),
        must_not_do=("不要解释改写方法", "不要输出多句候选"),
        style_anchor=("只给一句改法",),
    ),
)


def build_relationship_axis_contrastive_cases(
    *,
    scenes: Sequence[ContrastiveSceneSpec] = CONTRASTIVE_SCENES,
    stages: Sequence[str] = RELATIONSHIP_STAGES,
) -> list[dict[str, Any]]:
    if not scenes:
        raise ValueError("scenes must not be empty")
    if not stages:
        raise ValueError("stages must not be empty")

    rows: list[dict[str, Any]] = []
    for scene in scenes:
        for stage in stages:
            control = {
                "response_move": scene.response_move,
                "task_mode": scene.task_mode,
                "emotional_tone": scene.emotional_tone,
                "relationship_distance": stage,
                "information_budget": scene.information_budget,
                "question_budget": int(scene.question_budget),
                "must_include": list(scene.must_include),
                "must_not_do": _unique((*BASE_MUST_NOT_DO, *scene.must_not_do)),
                "facts": list(scene.facts),
                "style_anchor": _unique((*BASE_STYLE_ANCHOR, *scene.style_anchor)),
            }
            rows.append(
                {
                    "case_id": f"relaxis_{_clean_id(scene.scene_id)}_{_clean_id(stage)}",
                    "facts": list(scene.facts),
                    "must_not_do": list(scene.must_not_do),
                    "reply_control_packet": control,
                    "user_message": scene.user_message,
                }
            )
    return rows


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def write_relationship_axis_contrastive_cases(
    *,
    out_dir: Path = DEFAULT_OUT,
    scenes: Sequence[ContrastiveSceneSpec] = CONTRASTIVE_SCENES,
    stages: Sequence[str] = RELATIONSHIP_STAGES,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = build_relationship_axis_contrastive_cases(scenes=scenes, stages=stages)
    cases_path = out_dir / "cases.jsonl"
    _write_jsonl(cases_path, rows)
    scenes_payload = [
        {
            "scene_id": scene.scene_id,
            "task_mode": scene.task_mode,
            "response_move": scene.response_move,
            "user_message": scene.user_message,
        }
        for scene in scenes
    ]
    summary = {
        "case_count": len(rows),
        "cases_path": str(cases_path),
        "scene_count": len(scenes),
        "scenes": scenes_payload,
        "stage_count": len(stages),
        "stages": list(stages),
    }
    _write_json(out_dir / "summary.json", summary)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build 12x9 relationship-axis contrastive cases for isolated surface generation.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    summary = write_relationship_axis_contrastive_cases(out_dir=args.out)
    print("FINAL " + json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
