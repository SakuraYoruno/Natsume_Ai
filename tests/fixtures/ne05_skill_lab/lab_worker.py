"""Deterministic no-network worker for the trusted NE-05 Docker Lab."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--runtime-manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--variant", required=True)
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    runtime_path = Path(args.runtime_manifest)
    output_path = Path(args.output)
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    version = str(os.environ.get("NE05_LAB_VERSION") or "unversioned")
    records = dataset.get("records") if isinstance(dataset, dict) else None
    if not isinstance(records, list) or not records:
        raise ValueError("dataset records are required")
    if not isinstance(runtime, dict) or runtime.get("protocol") != "ne05.v1":
        raise ValueError("runtime manifest is invalid")

    result = {
        "schema_version": "natsume.ne05.skill-lab.result.v1",
        "status": "PASS",
        "variant": args.variant,
        "candidate_version": version,
        "record_count": len(records),
        "dataset_sha256": sha256(dataset_path.read_bytes()).hexdigest(),
        "runtime_sha256": sha256(runtime_path.read_bytes()).hexdigest(),
        "score": 0.80 if args.variant == "baseline" else 0.92,
        "external_effects": 0,
    }
    result["result_sha256"] = sha256(_canonical_bytes(result)).hexdigest()
    output_path.write_bytes(_canonical_bytes(result))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
