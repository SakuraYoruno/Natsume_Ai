from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.persona.surface_scene_quality_pack import (  # noqa: E402
    surface_scene_quality_pack_payload,
)


DEFAULT_OUT = ROOT / "artifacts" / "surface_scene_quality" / "harvested_samples.json"
SOURCE_REF_MARKERS = ("memory://", "evd://", "chat://", "source_refs=", "evidence_refs=")
BAD_REASON_PREFIXES = (
    "SHORT_CONTINUE_MEMORY_OVERDISPLAY",
    "SHORT_CONTINUE_UNPROVIDED_TECH_DETAIL",
    "BACK_MEMORY_OVERDISPLAY",
    "SURFACE_PROCESS_META_LANGUAGE",
    "UNPROVIDED_",
    "RAW_CONTEXT_BACKFILL_REQUEST",
    "UNNEEDED_CONTINUITY_MATERIAL_REQUEST",
    "CURRENT_USER_TOPIC_MISMATCH",
    "MISPLACED_SUPPORT_TEMPLATE",
    "MISPLACED_EMOTIONAL_CONTAINMENT",
)


@dataclass
class HarvestedExample:
    text: str
    score: float
    passed: bool
    variant: str
    case_id: str
    source_report: str
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    observation_codes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "score": round(float(self.score or 0.0), 3),
            "passed": bool(self.passed),
            "variant": self.variant,
            "case_id": self.case_id,
            "source_report": self.source_report,
            "reason_codes": list(self.reason_codes),
            "observation_codes": list(self.observation_codes),
        }


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _clean_list(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for item in (_clean_text(value) for value in values) if item))


def _source_leak(text: str) -> bool:
    return any(marker in text for marker in SOURCE_REF_MARKERS)


def _bad_reason_hit(reason_codes: Sequence[str], observation_codes: Sequence[str]) -> bool:
    codes = [*_clean_list(reason_codes), *_clean_list(observation_codes)]
    return any(any(code.startswith(prefix) for prefix in BAD_REASON_PREFIXES) for code in codes)


def _valid_surface_text(text: str) -> bool:
    text = _clean_text(text)
    return bool(text and 1 <= len(text) <= 80 and not _source_leak(text))


def _load_details(path: Path) -> list[Mapping[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        rows: list[Mapping[str, Any]] = []
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, Mapping):
                rows.append(payload)
        return rows
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, Mapping):
        results = payload.get("results")
        if isinstance(results, list):
            return [item for item in results if isinstance(item, Mapping)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    return []


def _load_json_mapping(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return payload if isinstance(payload, Mapping) else {}


def _candidate_examples(
    *,
    row: Mapping[str, Any],
    variant_name: str,
    variant_payload: Mapping[str, Any],
    source_report: str,
) -> list[HarvestedExample]:
    case = dict(row.get("case") or {})
    case_id = _clean_text(case.get("case_id"))
    examples: list[HarvestedExample] = []
    candidates = variant_payload.get("candidates")
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            text = _clean_text(candidate.get("text"))
            if not _valid_surface_text(text):
                continue
            examples.append(
                HarvestedExample(
                    text=text,
                    score=float(candidate.get("score") or 0.0),
                    passed=bool(candidate.get("passed")),
                    variant=variant_name,
                    case_id=case_id,
                    source_report=source_report,
                    reason_codes=_clean_list(candidate.get("reason_codes") or ()),
                    observation_codes=_clean_list(variant_payload.get("observation_codes") or ()),
                )
            )
    selected_text = _clean_text(variant_payload.get("selected_text"))
    if _valid_surface_text(selected_text) and selected_text not in {item.text for item in examples}:
        examples.append(
            HarvestedExample(
                text=selected_text,
                score=float(variant_payload.get("score") or 0.0),
                passed=bool(variant_payload.get("passed")),
                variant=variant_name,
                case_id=case_id,
                source_report=source_report,
                reason_codes=_clean_list(variant_payload.get("reason_codes") or ()),
                observation_codes=_clean_list(variant_payload.get("observation_codes") or ()),
            )
        )
    return examples


def harvest_surface_scene_quality_samples(
    rows: Sequence[Mapping[str, Any]],
    *,
    source_report: str = "",
    min_good_score: float = 92.0,
    max_bad_score: float = 70.0,
    max_per_scene: int = 8,
) -> list[dict[str, Any]]:
    by_scene: dict[str, dict[str, Any]] = {}

    for row in rows:
        case = dict(row.get("case") or {})
        scene_id = _clean_text(case.get("scene_id")) or "unknown_scene"
        bucket = by_scene.setdefault(
            scene_id,
            {
                "scene_id": scene_id,
                "case_ids": [],
                "good_examples": [],
                "bad_examples": [],
            },
        )
        case_id = _clean_text(case.get("case_id"))
        if case_id and case_id not in bucket["case_ids"]:
            bucket["case_ids"].append(case_id)
        variants = row.get("variants")
        if not isinstance(variants, Mapping):
            continue
        for variant_name, raw_variant in variants.items():
            if not isinstance(raw_variant, Mapping):
                continue
            variant = dict(raw_variant)
            for example in _candidate_examples(
                row=row,
                variant_name=_clean_text(variant_name),
                variant_payload=variant,
                source_report=source_report,
            ):
                if example.passed and example.score >= min_good_score and not _bad_reason_hit(
                    example.reason_codes,
                    example.observation_codes,
                ):
                    bucket["good_examples"].append(example)
                elif (
                    (not example.passed)
                    or example.score <= max_bad_score
                    or _bad_reason_hit(example.reason_codes, example.observation_codes)
                ):
                    bucket["bad_examples"].append(example)

    harvested: list[dict[str, Any]] = []
    for scene_id, bucket in sorted(by_scene.items()):
        good = _dedupe_rank_examples(bucket["good_examples"], reverse=True)[:max(1, int(max_per_scene or 8))]
        bad = _dedupe_rank_examples(bucket["bad_examples"], reverse=False)[:max(1, int(max_per_scene or 8))]
        harvested.append(
            {
                "scene_id": scene_id,
                "case_ids": list(bucket["case_ids"]),
                "good_examples": [item.as_dict() for item in good],
                "bad_examples": [item.as_dict() for item in bad],
                "summary": {
                    "good_count": len(good),
                    "bad_count": len(bad),
                },
            }
        )
    return harvested


def _dedupe_rank_examples(examples: Sequence[HarvestedExample], *, reverse: bool) -> list[HarvestedExample]:
    best_by_text: dict[str, HarvestedExample] = {}
    for example in examples:
        existing = best_by_text.get(example.text)
        if existing is None:
            best_by_text[example.text] = example
            continue
        if reverse and example.score > existing.score:
            best_by_text[example.text] = example
        elif not reverse and example.score < existing.score:
            best_by_text[example.text] = example
    return sorted(
        best_by_text.values(),
        key=lambda item: (float(item.score or 0.0), item.text),
        reverse=reverse,
    )


def build_surface_scene_quality_sample_report(
    *,
    details_paths: Sequence[Path],
    min_good_score: float = 92.0,
    max_bad_score: float = 70.0,
    max_per_scene: int = 8,
    include_pack: bool = True,
) -> dict[str, Any]:
    harvested_by_scene: dict[str, dict[str, Any]] = {}
    source_reports: list[str] = []
    for path in details_paths:
        rows = _load_details(path)
        source_report = str(path)
        source_reports.append(source_report)
        for scene in harvest_surface_scene_quality_samples(
            rows,
            source_report=source_report,
            min_good_score=min_good_score,
            max_bad_score=max_bad_score,
            max_per_scene=max_per_scene,
        ):
            scene_id = _clean_text(scene.get("scene_id"))
            target = harvested_by_scene.setdefault(
                scene_id,
                {
                    "scene_id": scene_id,
                    "case_ids": [],
                    "good_examples": [],
                    "bad_examples": [],
                    "summary": {"good_count": 0, "bad_count": 0},
                },
            )
            for case_id in scene.get("case_ids") or ():
                if case_id not in target["case_ids"]:
                    target["case_ids"].append(case_id)
            target["good_examples"].extend(scene.get("good_examples") or ())
            target["bad_examples"].extend(scene.get("bad_examples") or ())

    harvested = []
    for scene_id, scene in sorted(harvested_by_scene.items()):
        good = _dedupe_dict_examples(scene.get("good_examples") or (), reverse=True)[:max_per_scene]
        bad = _dedupe_dict_examples(scene.get("bad_examples") or (), reverse=False)[:max_per_scene]
        harvested.append(
            {
                "scene_id": scene_id,
                "case_ids": list(scene.get("case_ids") or ()),
                "good_examples": good,
                "bad_examples": bad,
                "summary": {"good_count": len(good), "bad_count": len(bad)},
            }
        )

    payload: dict[str, Any] = {
        "schema_version": 1,
        "sample_kind": "surface_scene_quality_harvest",
        "source_reports": source_reports,
        "harvest_rules": {
            "min_good_score": float(min_good_score),
            "max_bad_score": float(max_bad_score),
            "max_per_scene": int(max_per_scene),
            "bad_reason_prefixes": list(BAD_REASON_PREFIXES),
        },
        "harvested": harvested,
    }
    if include_pack:
        payload["current_pack"] = surface_scene_quality_pack_payload()
    return payload


def _dedupe_dict_examples(examples: Sequence[Mapping[str, Any]], *, reverse: bool) -> list[dict[str, Any]]:
    best_by_text: dict[str, dict[str, Any]] = {}
    for raw in examples:
        item = dict(raw)
        text = _clean_text(item.get("text"))
        if not text:
            continue
        existing = best_by_text.get(text)
        if existing is None:
            best_by_text[text] = item
            continue
        score = float(item.get("score") or 0.0)
        existing_score = float(existing.get("score") or 0.0)
        if reverse and score > existing_score:
            best_by_text[text] = item
        elif not reverse and score < existing_score:
            best_by_text[text] = item
    return sorted(
        (dict(item) for item in best_by_text.values()),
        key=lambda item: (float(item.get("score") or 0.0), _clean_text(item.get("text"))),
        reverse=reverse,
    )


def _reviewed_example_texts(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    texts: list[str] = []
    for item in value:
        if isinstance(item, Mapping):
            texts.append(_clean_text(item.get("text")))
        else:
            texts.append(_clean_text(item))
    return _clean_list(texts)


def _append_unique(base: Sequence[Any], additions: Sequence[Any]) -> list[str]:
    return list(_clean_list([*list(base or ()), *list(additions or ())]))


def merge_reviewed_surface_scene_samples(
    pack_payload: Mapping[str, Any],
    reviewed_payload: Mapping[str, Any],
) -> dict[str, Any]:
    pack = dict(pack_payload or {})
    scenes = [dict(scene) for scene in list(pack.get("scenes") or ()) if isinstance(scene, Mapping)]
    by_scene = {
        _clean_text(scene.get("scene_id")): scene
        for scene in scenes
        if _clean_text(scene.get("scene_id"))
    }
    reviewed_scenes = reviewed_payload.get("scenes") or reviewed_payload.get("reviewed") or ()
    if not isinstance(reviewed_scenes, list):
        reviewed_scenes = ()
    for raw_review in reviewed_scenes:
        if not isinstance(raw_review, Mapping):
            continue
        scene_id = _clean_text(raw_review.get("scene_id"))
        if not scene_id:
            continue
        scene = by_scene.get(scene_id)
        if scene is None:
            scene = {
                "scene_id": scene_id,
                "title": _clean_text(raw_review.get("title")) or scene_id,
                "user_markers": list(_clean_list(raw_review.get("user_markers") or ())),
                "required_move": _clean_text(raw_review.get("required_move")),
                "should_not_surface": [],
                "scoring_focus": list(_clean_list(raw_review.get("scoring_focus") or ())),
                "good_examples": [],
                "bad_examples": [],
                "source": "reviewed",
                "source_note": "人工 reviewed 样本晋升。",
            }
            scenes.append(scene)
            by_scene[scene_id] = scene
        good = _reviewed_example_texts(raw_review.get("good_examples"))
        bad = _reviewed_example_texts(raw_review.get("bad_examples"))
        should_not = _reviewed_example_texts(raw_review.get("should_not_surface"))
        if good:
            scene["good_examples"] = _append_unique(scene.get("good_examples") or (), good)
        if bad:
            scene["bad_examples"] = _append_unique(scene.get("bad_examples") or (), bad)
        if should_not:
            scene["should_not_surface"] = _append_unique(scene.get("should_not_surface") or (), should_not)
        if good or bad or should_not:
            source = _clean_text(scene.get("source")) or "manual_gold"
            if "reviewed" not in source:
                scene["source"] = f"{source}+reviewed"
            note = _clean_text(scene.get("source_note"))
            review_note = _clean_text(raw_review.get("source_note")) or "人工 reviewed 样本晋升。"
            if review_note and review_note not in note:
                scene["source_note"] = f"{note} {review_note}".strip()
    pack["schema_version"] = int(pack.get("schema_version") or 1)
    pack["sample_kind"] = "surface_scene_quality_pack"
    pack["scenes"] = scenes
    return pack


def merge_reviewed_surface_scene_sample_files(
    *,
    reviewed_paths: Sequence[Path],
    base_pack: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    merged = dict(base_pack or surface_scene_quality_pack_payload())
    for path in reviewed_paths:
        merged = merge_reviewed_surface_scene_samples(merged, _load_json_mapping(path))
    return merged


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Harvest surface scene quality samples from AB details.")
    parser.add_argument("--details", action="append", default=[])
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--min-good-score", type=float, default=92.0)
    parser.add_argument("--max-bad-score", type=float, default=70.0)
    parser.add_argument("--max-per-scene", type=int, default=8)
    parser.add_argument("--no-current-pack", action="store_true")
    parser.add_argument("--reviewed", action="append", default=[])
    parser.add_argument("--merge-out", default="")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    details_paths = [Path(item) for item in args.details]
    if not details_paths:
        details_paths = [ROOT / "artifacts" / "surface_memory_ab" / "live_quality_pack_latest" / "details.json"]
    payload = build_surface_scene_quality_sample_report(
        details_paths=details_paths,
        min_good_score=float(args.min_good_score),
        max_bad_score=float(args.max_bad_score),
        max_per_scene=int(args.max_per_scene),
        include_pack=not bool(args.no_current_pack),
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = {"output": str(out_path), "scene_count": len(payload["harvested"])}
    reviewed_paths = [Path(item) for item in args.reviewed]
    if args.merge_out:
        merged_pack = merge_reviewed_surface_scene_sample_files(reviewed_paths=reviewed_paths)
        merge_out = Path(args.merge_out)
        merge_out.parent.mkdir(parents=True, exist_ok=True)
        merge_out.write_text(json.dumps(merged_pack, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        result["merged_pack_output"] = str(merge_out)
        result["merged_scene_count"] = len(merged_pack.get("scenes") or [])
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
