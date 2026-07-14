"""Fail-closed marker for the retired private Digital Life scheduler validator.

The former validator constructed its own registry, scheduler, workers and
process globals.  That was a second production authority and could not prove
the ASGI-composed Secure Ingress -> Scheduler -> Task Runtime chain.  AW-04
will replace this command with a real service-level Digital Life acceptance;
until then it deliberately reports that the governed service is required.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "artifacts" / "digital_life_background_runtime"
CORE_TASK_IDS = (
    "inner_world_numeric_tick",
    "inner_world_monologue_tick",
    "silence_tick",
    "body_sense_tick",
    "emotion_decay_tick",
)


def run_background_runtime_validation(args: argparse.Namespace) -> dict[str, Any]:
    """Refuse to recreate the deleted private scheduler authority."""

    mode = _resolve_mode(args)
    out_dir = _resolve_out_dir(args)
    out_dir.mkdir(parents=True, exist_ok=True)
    persona_self_id = _resolve_persona_self_id_arg(args)
    run_id = str(getattr(args, "run_id", "") or f"retired-{int(time.time())}")
    checks = {
        "legacy_scheduler_retired": True,
        "governed_scheduler_service_required": True,
    }
    summary = {
        "passed": False,
        "evaluation_mode": "retired_standalone_scheduler_validation",
        "reason_code": "GOVERNED_SCHEDULER_SERVICE_REQUIRED",
        "mode": mode,
        "run_id": run_id,
        "user_id": persona_self_id,
        "persona_self_id": persona_self_id,
        "core_task_count": len(CORE_TASK_IDS),
        "registered_task_count": 0,
        "is_advancing": False,
        "private_thought_count": 0,
        "self_update_count": 0,
        "checks": checks,
        "output_dir": str(out_dir),
    }
    details = {
        "reason_code": summary["reason_code"],
        "trigger_results": {},
        "checks": checks,
    }
    _write_json(out_dir / "summary.json", summary)
    _write_json(out_dir / "details.json", details)
    (out_dir / "report.md").write_text(
        "# Digital Life Background Runtime\n\n"
        "The private scheduler validator is retired. Run the AW-04 governed "
        "service acceptance after it is installed.\n",
        encoding="utf-8",
    )
    return {"summary": summary, "details": details, "output_dir": str(out_dir)}


def _resolve_persona_self_id_arg(args: argparse.Namespace) -> str:
    try:
        from host_brain.persona.identity import (  # noqa: PLC0415
            get_canonical_persona_self_id,
            resolve_persona_self_id,
        )

        return resolve_persona_self_id(
            getattr(args, "user_id", "") or get_canonical_persona_self_id()
        )
    except Exception:
        return "natsume_ai"


def _resolve_mode(args: argparse.Namespace) -> str:
    return "live" if str(getattr(args, "mode", "fixture")).lower() == "live" else "fixture"


def _resolve_out_dir(args: argparse.Namespace) -> Path:
    raw = str(getattr(args, "out_dir", "") or "").strip()
    return Path(raw) if raw else DEFAULT_OUT / time.strftime("%Y%m%d-%H%M%S")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Report that the retired private background scheduler cannot run.",
    )
    parser.add_argument("--mode", choices=["fixture", "live"], default="fixture")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--user-id", default="")
    parser.add_argument("--rewind-minutes", type=int, default=30)
    return parser


def main(argv: list[str] | None = None) -> int:
    result = run_background_runtime_validation(build_arg_parser().parse_args(argv))
    summary = dict(result.get("summary") or {})
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if bool(summary.get("passed")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
