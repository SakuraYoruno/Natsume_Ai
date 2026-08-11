"""Stdlib-only worker for the focused NE-06 expression policy experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


_INSTRUCTIONS = {
    "ai_tone": "避免机械 AI 腔",
    "template_like": "避免模板化分段",
    "out_of_persona": "保持既定人格语气",
    "logic_break": "先核对意图和因果",
    "too_helpful": "不要过度提供建议",
    "in_persona": "保持当前自然人格表达",
}


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _hints(policy: Mapping[str, Any]) -> list[str]:
    raw_scores = policy.get("tag_scores")
    if not isinstance(raw_scores, Mapping):
        raise ValueError("expression policy tag_scores are missing")
    ranked: list[tuple[int, str]] = []
    for tag, instruction in _INSTRUCTIONS.items():
        score = int(raw_scores.get(tag, 0))
        if (
            (tag == "in_persona" and score > 0)
            or (tag != "in_persona" and score < 0)
        ):
            ranked.append((abs(score), instruction))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [instruction for _score, instruction in ranked[:3]]


def run(
    *,
    dataset_payload: Mapping[str, Any],
    experiment_kind: str,
    variant_id: str,
    execution_id: str,
    seed: int,
    expected_dataset_digest: str,
) -> dict[str, Any]:
    if experiment_kind != "companion_expression":
        raise ValueError("unsupported companion expression experiment")
    if variant_id not in {"baseline", "candidate"}:
        raise ValueError("unsupported expression policy variant")
    if _digest(dataset_payload) != expected_dataset_digest:
        raise ValueError("mounted expression dataset digest drift")
    records = dataset_payload.get("records")
    if not isinstance(records, list) or len(records) != 1:
        raise ValueError("expression dataset must contain one record")
    record = records[0]
    if not isinstance(record, Mapping):
        raise ValueError("expression dataset record is invalid")
    policy_key = (
        "baseline_policy" if variant_id == "baseline" else "target_policy"
    )
    policy = record.get(policy_key)
    if not isinstance(policy, Mapping):
        raise ValueError("expression variant policy is missing")
    return {
        "schema": (
            "natsume.evolution-lab.companion-expression-worker-output.v1"
        ),
        "execution_id": execution_id,
        "experiment_kind": experiment_kind,
        "variant_id": variant_id,
        "seed": seed,
        "dataset_id": str(dataset_payload.get("dataset_id") or ""),
        "dataset_digest": expected_dataset_digest,
        "hints": _hints(policy),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--kind", required=True)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--execution-id", required=True)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--dataset-digest", required=True)
    args = parser.parse_args()
    payload = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("expression dataset must be a JSON object")
    output = run(
        dataset_payload=payload,
        experiment_kind=args.kind,
        variant_id=args.variant,
        execution_id=args.execution_id,
        seed=args.seed,
        expected_dataset_digest=args.dataset_digest,
    )
    Path(args.output).write_text(_canonical(output), encoding="utf-8")
    print(
        _canonical(
            {
                "status": "PASS",
                "execution_id": args.execution_id,
                "output_digest": _digest({"output": output}),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
