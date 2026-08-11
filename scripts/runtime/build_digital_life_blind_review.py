"""Build and score anonymous A/B review kits for Digital Life cohorts."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from pathlib import Path
from typing import Any


SCORE_FIELDS = ("persona", "emotion_fit", "naturalness", "continuity")
REVIEW_COLUMNS = (
    "reviewer_id",
    "item_id",
    "item_order",
    "conversation_A",
    "conversation_B",
    "winner_A_B_TIE",
    "persona_A_1_5",
    "persona_B_1_5",
    "emotion_fit_A_1_5",
    "emotion_fit_B_1_5",
    "naturalness_A_1_5",
    "naturalness_B_1_5",
    "continuity_A_1_5",
    "continuity_B_1_5",
    "mixed_emotion_A_Y_N_NA",
    "mixed_emotion_B_Y_N_NA",
    "repair_correct_A_Y_N_NA",
    "repair_correct_B_Y_N_NA",
    "notes",
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _case_id(outcome: dict[str, Any]) -> str:
    return str(outcome.get("case", {}).get("case_id") or "").strip()


def _turn_prompt(turn: dict[str, Any]) -> str:
    return str(turn.get("prompt") or "").strip()


def _turn_reply(turn: dict[str, Any]) -> str:
    graph = turn.get("graph") or {}
    projection = turn.get("projection") or {}
    return str(
        graph.get("accepted_narrative")
        or projection.get("narrative")
        or ""
    ).strip()


def _conversation(outcome: dict[str, Any]) -> str:
    lines: list[str] = []
    for turn in outcome.get("turns") or []:
        lines.extend((f"用户：{_turn_prompt(turn)}", f"回复：{_turn_reply(turn)}"))
    return "\n".join(lines)


def _aligned_pairs(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    baseline_outcomes = list(baseline.get("outcomes") or [])
    candidate_outcomes = list(candidate.get("outcomes") or [])
    if len(baseline_outcomes) != len(candidate_outcomes) or not baseline_outcomes:
        raise ValueError("baseline and candidate outcome counts must match and be nonzero")
    pairs = list(zip(baseline_outcomes, candidate_outcomes, strict=True))
    for baseline_item, candidate_item in pairs:
        if _case_id(baseline_item) != _case_id(candidate_item):
            raise ValueError("case order mismatch")
        baseline_prompts = [_turn_prompt(turn) for turn in baseline_item.get("turns") or []]
        candidate_prompts = [_turn_prompt(turn) for turn in candidate_item.get("turns") or []]
        if baseline_prompts != candidate_prompts:
            raise ValueError(f"prompt mismatch for {_case_id(baseline_item)}")
        if not all(_turn_reply(turn) for turn in baseline_item.get("turns") or []):
            raise ValueError(f"baseline has empty reply for {_case_id(baseline_item)}")
        if not all(_turn_reply(turn) for turn in candidate_item.get("turns") or []):
            raise ValueError(f"candidate has empty reply for {_case_id(candidate_item)}")
    return pairs


def build_kit(args: argparse.Namespace) -> dict[str, Any]:
    baseline = _read_json(args.baseline)
    candidate = _read_json(args.candidate)
    pairs = _aligned_pairs(baseline, candidate)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, Any] = {
        "schema_version": "natsume.a22.digital-life-blind-review-map.v1",
        "status": "READY_FOR_3_HUMAN_REVIEW",
        "seed": args.seed,
        "reviewer_count": args.reviewers,
        "item_count_per_reviewer": len(pairs),
        "baseline": {
            "commit_sha": baseline.get("commit_sha"),
            "report_sha256": _sha256(args.baseline),
        },
        "candidate": {
            "commit_sha": candidate.get("commit_sha"),
            "report_sha256": _sha256(args.candidate),
        },
        "reviewers": {},
    }
    for reviewer_index in range(1, args.reviewers + 1):
        reviewer_id = f"reviewer-{reviewer_index:02d}"
        rng = random.Random(f"{args.seed}:{reviewer_id}")
        order = list(range(len(pairs)))
        rng.shuffle(order)
        rows: list[dict[str, str]] = []
        reviewer_map: list[dict[str, Any]] = []
        for item_order, pair_index in enumerate(order, 1):
            baseline_item, candidate_item = pairs[pair_index]
            case_id = _case_id(candidate_item)
            item_id = hashlib.sha256(
                f"{args.seed}:{reviewer_id}:{case_id}".encode("utf-8")
            ).hexdigest()[:16]
            candidate_side = rng.choice(("A", "B"))
            baseline_side = "B" if candidate_side == "A" else "A"
            conversations = {
                candidate_side: _conversation(candidate_item),
                baseline_side: _conversation(baseline_item),
            }
            row = {column: "" for column in REVIEW_COLUMNS}
            row.update(
                reviewer_id=reviewer_id,
                item_id=item_id,
                item_order=str(item_order),
                conversation_A=conversations["A"],
                conversation_B=conversations["B"],
            )
            rows.append(row)
            reviewer_map.append(
                {
                    "item_id": item_id,
                    "case_id": case_id,
                    "family_id": candidate_item.get("case", {}).get("family_id"),
                    "candidate_side": candidate_side,
                    "baseline_side": baseline_side,
                }
            )
        review_path = output_dir / f"{reviewer_id}.csv"
        with review_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        mapping["reviewers"][reviewer_id] = {
            "review_file": review_path.name,
            "review_file_sha256": _sha256(review_path),
            "items": reviewer_map,
        }
    map_path = output_dir / "sealed-version-map.json"
    map_path.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "natsume.a22.digital-life-blind-review-kit.v1",
        "status": mapping["status"],
        "instructions": (
            "每位真实评审独立填写自己的 CSV；不得查看 sealed-version-map.json。"
            " winner 仅填 A/B/TIE，四项分数填 1-5；混合情绪与修复正确性按适用项填 Y/N，"
            "其他场景填 NA。完成后保留原文件名交回汇总。"
        ),
        "review_files": [f"reviewer-{index:02d}.csv" for index in range(1, args.reviewers + 1)],
        "sealed_map": map_path.name,
        "sealed_map_sha256": _sha256(map_path),
    }
    manifest_path = output_dir / "README.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", default="a22-human-review-v1")
    parser.add_argument("--reviewers", type=int, default=3)
    args = parser.parse_args()
    if args.reviewers < 3:
        parser.error("at least three human reviewers are required")
    manifest = build_kit(args)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
