"""Stdlib-only worker executed inside the NE-04 Docker sandbox."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping


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


def _research_result(
    record: Mapping[str, Any],
    *,
    variant_id: str,
) -> dict[str, Any]:
    required = [str(item) for item in record.get("required_steps", ())]
    selected = required[:1] if variant_id == "baseline" else required
    return {
        "case_id": str(record["case_id"]),
        "selected_steps": selected,
    }


def _proactive_result(
    record: Mapping[str, Any],
    *,
    variant_id: str,
) -> dict[str, Any]:
    if variant_id == "baseline":
        action = "notify"
    elif bool(record.get("maintenance")):
        action = "maintenance_notice"
    elif (
        bool(record.get("user_annoyed"))
        or bool(record.get("game_mode"))
        or int(record.get("hour", 0)) >= 23
    ):
        action = "silence"
    else:
        action = "brief_prompt"
    return {"case_id": str(record["case_id"]), "action": action}


def _memory_result(
    record: Mapping[str, Any],
    *,
    variant_id: str,
) -> dict[str, Any]:
    if variant_id == "baseline":
        action = "write"
    else:
        sensitivity = str(record.get("sensitivity") or "").lower()
        action = (
            "write"
            if bool(record.get("has_provenance"))
            and sensitivity != "secret"
            and (sensitivity != "private" or bool(record.get("user_consent")))
            else "reject"
        )
    return {"case_id": str(record["case_id"]), "action": action}


def run(
    *,
    dataset_payload: Mapping[str, Any],
    experiment_kind: str,
    variant_id: str,
    execution_id: str,
    seed: int,
    expected_dataset_digest: str,
) -> dict[str, Any]:
    if variant_id not in {"baseline", "candidate"}:
        raise ValueError("unsupported golden policy variant")
    actual_digest = _digest(dataset_payload)
    if actual_digest != expected_dataset_digest:
        raise ValueError("mounted dataset digest drift")
    records = dataset_payload.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("golden dataset records are missing")
    handlers = {
        "research_planning": _research_result,
        "proactive": _proactive_result,
        "memory_write": _memory_result,
    }
    handler = handlers.get(experiment_kind)
    if handler is None:
        raise ValueError("unsupported golden experiment kind")
    results = [
        handler(record, variant_id=variant_id)
        for record in records
        if isinstance(record, Mapping)
    ]
    if len(results) != len(records):
        raise ValueError("golden dataset contains a non-object record")
    return {
        "schema": "natsume.evolution-lab.golden-worker-output.v1",
        "execution_id": execution_id,
        "experiment_kind": experiment_kind,
        "variant_id": variant_id,
        "seed": seed,
        "dataset_id": str(dataset_payload.get("dataset_id") or ""),
        "dataset_digest": actual_digest,
        "results": results,
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
    dataset_path = Path(args.dataset)
    output_path = Path(args.output)
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("golden dataset must be a JSON object")
    output = run(
        dataset_payload=payload,
        experiment_kind=args.kind,
        variant_id=args.variant,
        execution_id=args.execution_id,
        seed=args.seed,
        expected_dataset_digest=args.dataset_digest,
    )
    output_path.write_text(_canonical(output), encoding="utf-8")
    sys.stdout.write(
        _canonical(
            {
                "status": "PASS",
                "execution_id": args.execution_id,
                "output_digest": _digest({"output": output}),
            }
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
