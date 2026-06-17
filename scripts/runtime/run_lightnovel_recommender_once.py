"""Run the lightnovel recommender once and optionally push through Gateway."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from host_brain.agent_runtime.production_executor import (
    execute_agent_production_payload,
    _maybe_push_lightnovel_recommendations_to_gateway,
)
from host_brain.agent_runtime.production_studio import build_production_work_order
from host_brain.agent_runtime.work_order import AgentWorkOrder


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ[key] = value


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts-root", default="artifacts/codex_runs/lightnovel_recommender")
    parser.add_argument("--timeout-s", type=int, default=720)
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument(
        "--replay-run-dir",
        default="",
        help=(
            "Replay an existing lightnovel recommender run through the normal Gateway delivery path. "
            "Use this for failed deliveries instead of ad-hoc OneBot sends."
        ),
    )
    args = parser.parse_args()

    _load_env_file(Path(".env"))
    _load_env_file(Path("runtime/host/host.runtime.env"))
    if args.no_push:
        os.environ["HOST_LIGHTNOVEL_RECOMMENDER_GATEWAY_ENABLED"] = "false"

    artifacts_root = str(Path(args.artifacts_root).resolve())
    request_id = f"manual_ln_push_{int(time.time())}"
    job_id = f"manual_ln_job_{int(time.time())}"
    if args.replay_run_dir:
        run_dir = Path(args.replay_run_dir).resolve()
        result = _read_json(run_dir / "result.json")
        if not result:
            result = {"status": "EXECUTED"}
        else:
            result = dict(result)
            result.setdefault("status", "EXECUTED")
        work_order_payload = build_production_work_order(
            {
                "task": "重放已有轻小说推荐结果，并通过正式 Gateway 投递到 QQ。",
                "request_id": request_id,
                "artifacts_root": str(run_dir.parent),
                "workflow_owner": "natsume",
                "timeout_seconds": 180,
            },
            skill_id="agent_lightnovel_recommender",
        )
        work_order = AgentWorkOrder.from_mapping(work_order_payload)
        delivery = _maybe_push_lightnovel_recommendations_to_gateway(
            job_id=job_id,
            order=work_order,
            result=result,
            run_dir=run_dir,
        )
        print(
            "REPLAY="
            + json.dumps(
                {
                    "job_id": job_id,
                    "request_id": request_id,
                    "run_dir": str(run_dir),
                    "delivery": delivery,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 0 if isinstance(delivery, dict) and delivery.get("success") else 2

    work_order = build_production_work_order(
        {
            "task": "调用今日推荐：从轻之国度/轻小说公开榜单或最新资源抓取封面、简介和来源，推荐轻小说，并推送到 QQ。",
            "request_id": request_id,
            "artifacts_root": artifacts_root,
            "urls": [
                "https://www.lightnovel.fun/cn/category/3/106?type=1",
                "https://www.lightnovel.fun/",
                "https://book.sfacg.com/rank/",
            ],
            "site_adapter": "lightnovel.fun",
            "workflow_owner": "natsume",
            "timeout_seconds": 900,
        },
        skill_id="agent_lightnovel_recommender",
    )
    queued = execute_agent_production_payload(
        {
            "work_order": work_order,
            "artifacts_root": artifacts_root,
            "start_background": True,
            "job_id": job_id,
        }
    )
    run_dir = Path(str(queued.get("artifacts_dir") or ""))
    print(
        "QUEUED="
        + json.dumps(
            {
                "job_id": job_id,
                "request_id": request_id,
                "run_dir": str(run_dir),
                "status": queued.get("status"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    deadline = time.time() + max(5, int(args.timeout_s))
    last_status = ""
    while time.time() < deadline:
        job = _read_json(run_dir / "production_job.json")
        result = _read_json(run_dir / "result.json")
        status = str(job.get("status") or "").upper()
        delivery = result.get("gateway_delivery") if isinstance(result, dict) else None
        if status and status != last_status:
            print(
                "STATUS="
                + json.dumps(
                    {
                        "status": status,
                        "detail": job.get("detail"),
                        "has_gateway_delivery": delivery is not None,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            last_status = status
        if status in {"COMPLETED", "FAILED"} and (delivery is not None or status == "FAILED"):
            print("FINAL=" + json.dumps({"job": job, "result": result}, ensure_ascii=False), flush=True)
            return 0 if status == "COMPLETED" else 2
        time.sleep(5)
    print("FINAL=" + json.dumps({"timeout": True, "run_dir": str(run_dir)}, ensure_ascii=False), flush=True)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
