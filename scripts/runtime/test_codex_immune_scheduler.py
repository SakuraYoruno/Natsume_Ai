"""Runtime probe for Codex immune scheduler v2."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


def _http_json(method: str, url: str, *, payload: dict[str, Any] | None = None, timeout: float = 30.0) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            return {"ok": 200 <= response.status < 300, "status_code": response.status, "data": data}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {"raw": raw}
        return {"ok": False, "status_code": exc.code, "data": data}
    except Exception as exc:
        return {"ok": False, "status_code": -1, "error": str(exc)}


def build_probe_report(
    *,
    base_url: str,
    task_id: str = "codex_immune_auto_dispatch_tick",
    source: str = "health",
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    base = str(base_url or "").rstrip("/")
    source_query = urllib.parse.urlencode({"source": source})
    before = _http_json("GET", f"{base}/internal/codex/immune/auto-state", timeout=timeout_seconds)
    proposal = _http_json("POST", f"{base}/internal/codex/immune/proposal?{source_query}", payload={}, timeout=timeout_seconds)
    scheduler_trigger = _http_json("POST", f"{base}/host-scheduler/tasks/{urllib.parse.quote(task_id)}/trigger", payload={}, timeout=timeout_seconds)
    after = _http_json("GET", f"{base}/internal/codex/immune/auto-state", timeout=timeout_seconds)
    dispatch = None
    if not scheduler_trigger.get("ok"):
        dispatch = _http_json("POST", f"{base}/internal/codex/immune/dispatch?{source_query}", payload={}, timeout=timeout_seconds)
        after = _http_json("GET", f"{base}/internal/codex/immune/auto-state", timeout=timeout_seconds)
    passed = bool(before.get("ok") and proposal.get("ok") and after.get("ok") and (scheduler_trigger.get("ok") or (dispatch or {}).get("ok")))
    return {
        "probe": "codex_immune_scheduler",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base,
        "task_id": task_id,
        "source": source,
        "before": before,
        "proposal": proposal,
        "scheduler_trigger": scheduler_trigger,
        "dispatch_fallback": dispatch,
        "after": after,
        "overall": {"passed": passed},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Codex immune scheduler v2.")
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--task-id", default="codex_immune_auto_dispatch_tick")
    parser.add_argument("--source", default="health")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--report-dir", default=str(ROOT / "artifacts" / "release" / "reports"))
    args = parser.parse_args()

    report = build_probe_report(
        base_url=args.base_url,
        task_id=args.task_id,
        source=args.source,
        timeout_seconds=args.timeout_seconds,
    )
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"codex_immune_scheduler_probe_{int(time.time())}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": report["overall"]["passed"], "report_path": str(report_path)}, ensure_ascii=False))
    return 0 if report["overall"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
