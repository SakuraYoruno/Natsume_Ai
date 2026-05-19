"""Read-only observability for real wall-clock digital-life soak runs."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from host_brain.app.obs_routes_core import _now_ms
from tools.review_digital_life_wall_clock_soak import (
    DEFAULT_EXPECTED_HOURS,
    DEFAULT_EXPECTED_ORGAN_COUNT,
    review_wall_clock_soak,
)


ROOT = Path(__file__).resolve().parents[2]
REAL_WALL_CLOCK_MODE = "real_wall_clock_digital_life_soak"

router = APIRouter(prefix="/obs", tags=["observability"])


class DigitalLifeWallClockSoakResponse(BaseModel):
    timestamp: int
    available: bool = False
    summary: dict[str, Any] = Field(default_factory=dict)
    latestCheckpoints: list[dict[str, Any]] = Field(default_factory=list)
    paths: dict[str, str] = Field(default_factory=dict)
    stderrTail: str = ""
    stdoutTail: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)


def _dump_model(value: BaseModel) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value.dict()


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _get_artifacts_root() -> Path:
    raw = os.getenv("HOST_OBS_WALL_CLOCK_SOAK_ARTIFACTS_ROOT", "").strip()
    return Path(raw).resolve() if raw else (ROOT / "artifacts" / "codex_runs").resolve()


def _summary_sort_key(summary_path: Path) -> tuple[int, float]:
    summary = _read_json(summary_path)
    stamp = int(summary.get("updated_at_ms") or summary.get("started_at_ms") or 0)
    if stamp > 0:
        return stamp, summary_path.stat().st_mtime if summary_path.exists() else 0.0
    try:
        return 0, summary_path.stat().st_mtime
    except OSError:
        return 0, 0.0


def _resolve_run_dir(run_id: str = "") -> tuple[Path | None, str]:
    artifacts_root = _get_artifacts_root()
    requested = (run_id or os.getenv("HOST_OBS_WALL_CLOCK_SOAK_RUN_ID", "")).strip()
    if requested:
        return (artifacts_root / requested / "run").resolve(), "requested-run-id"

    candidates: list[Path] = []
    if artifacts_root.exists():
        for summary_path in artifacts_root.glob("*/run/summary.json"):
            summary = _read_json(summary_path)
            if str(summary.get("evaluation_mode") or "") == REAL_WALL_CLOCK_MODE:
                candidates.append(summary_path)
    if not candidates:
        return None, "latest-wall-clock-run-not-found"
    selected = max(candidates, key=_summary_sort_key)
    return selected.parent.resolve(), "latest-wall-clock-summary"


def _read_pid_hint(run_dir: Path) -> int:
    for path in (run_dir / "pid.txt", run_dir.parent / "pid.txt"):
        try:
            text = path.read_text(encoding="utf-8").strip()
            value = int(text)
        except Exception:
            continue
        if value > 0:
            return value
    return 0


def _build_wall_clock_soak_payload(
    *,
    run_id: str = "",
    pid: int = 0,
    require_complete: bool = False,
) -> dict[str, Any]:
    artifacts_root = _get_artifacts_root()
    run_dir, selection_mode = _resolve_run_dir(run_id)
    timestamp = _now_ms()
    if run_dir is None:
        return _dump_model(DigitalLifeWallClockSoakResponse(
            timestamp=timestamp,
            available=False,
            summary={
                "verdict": "MISSING",
                "run_status": "",
                "failed_checks": ["wall_clock_soak_run_found"],
                "checks": {"wall_clock_soak_run_found": False},
            },
            meta={
                "artifactsRoot": str(artifacts_root),
                "selectionMode": selection_mode,
                "readOnly": True,
            },
        ))

    effective_pid = int(pid or 0) or _env_int("HOST_OBS_WALL_CLOCK_SOAK_PID", 0) or _read_pid_hint(run_dir)
    stale_seconds_env = os.getenv("HOST_OBS_WALL_CLOCK_SOAK_STALE_SECONDS", "").strip()
    try:
        stale_seconds = max(1.0, float(stale_seconds_env)) if stale_seconds_env else None
    except ValueError:
        stale_seconds = None
    expected_hours = _env_float(
        "HOST_OBS_WALL_CLOCK_SOAK_EXPECTED_HOURS",
        DEFAULT_EXPECTED_HOURS,
        minimum=0.0,
    )
    expected_organ_count = _env_int(
        "HOST_OBS_WALL_CLOCK_SOAK_EXPECTED_ORGAN_COUNT",
        DEFAULT_EXPECTED_ORGAN_COUNT,
        minimum=1,
    )
    args = argparse.Namespace(
        run_dir=str(run_dir),
        out_dir="",
        pid=effective_pid,
        stdout="",
        stderr="",
        expected_hours=expected_hours,
        expected_seconds=None,
        expected_organ_count=expected_organ_count,
        stale_seconds=stale_seconds,
        log_tail_chars=4_000,
        require_complete=bool(require_complete),
        write_outputs=False,
    )
    result = review_wall_clock_soak(args)
    summary = _as_dict(result.get("summary"))
    details = _as_dict(result.get("details"))
    checks = _as_dict(summary.get("checks"))
    paths = _as_dict(details.get("paths"))
    latest_checkpoints = [_as_dict(item) for item in _as_list(details.get("latest_checkpoints"))]
    available = bool(checks.get("summary_file_present")) and bool(summary.get("run_id"))

    return _dump_model(DigitalLifeWallClockSoakResponse(
        timestamp=timestamp,
        available=available,
        summary=summary,
        latestCheckpoints=latest_checkpoints,
        paths={key: str(value) for key, value in paths.items()},
        stderrTail=str(details.get("stderr_tail") or ""),
        stdoutTail=str(details.get("stdout_tail") or ""),
        meta={
            "artifactsRoot": str(artifacts_root),
            "selectionMode": selection_mode,
            "requestedRunId": run_id or None,
            "readOnly": True,
            "processTracked": effective_pid > 0,
            "expectedOrganCount": expected_organ_count,
        },
    ))


@router.get("/digital-life/wall-clock-soak", response_model=DigitalLifeWallClockSoakResponse)
def get_digital_life_wall_clock_soak(
    run_id: str = Query("", description="Optional artifacts/codex_runs/<run_id>/run selector."),
    pid: int = Query(0, ge=0, description="Optional process id hint for liveness checks."),
    require_complete: bool = Query(False, description="Require final PASS instead of accepting an active RUNNING soak."),
) -> DigitalLifeWallClockSoakResponse:
    return DigitalLifeWallClockSoakResponse(
        **_build_wall_clock_soak_payload(
            run_id=run_id,
            pid=pid,
            require_complete=require_complete,
        )
    )
