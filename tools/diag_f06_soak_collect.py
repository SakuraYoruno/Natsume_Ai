"""
F-06 长稳 soak 持续采集：按间隔采样 Host 进程 RSS、/health，追加到报告目录。

用法:
  python tools/diag_f06_soak_collect.py --interval 30 --report-dir artifacts/release/reports/.../10_health_runtime
  (默认 interval=30 分钟，report-dir 默认 artifacts/release/reports/.../10_health_runtime)

后台运行示例:
  nohup python tools/diag_f06_soak_collect.py --interval 30 &
  # Windows: Start-Process python -ArgumentList "tools/diag_f06_soak_collect.py","--interval","30" -WindowStyle Hidden
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_REPORT_DIR = (
    REPO_ROOT / "artifacts" / "release" / "reports" / "release_seal_personal_full_20260314" / "10_health_runtime"
)
DEFAULT_PID_FILE = REPO_ROOT / "runtime" / "host" / "host.pid"
DEFAULT_HEALTH_URL = "http://127.0.0.1:18080/health"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_pid(pid_file: Path) -> int | None:
    if not pid_file.exists():
        return None
    try:
        raw = pid_file.read_text(encoding="utf-8-sig").strip()
        return int(raw) if raw else None
    except Exception:
        return None


def _rss_mb(pid: int) -> float | None:
    try:
        import psutil
        p = psutil.Process(pid)
        return round(p.memory_info().rss / (1024 * 1024), 2)
    except Exception:
        return None


def _health_status(url: str, timeout_s: float = 5) -> dict | None:
    try:
        import urllib.request
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            if resp.status == 200:
                body = json.loads(resp.read().decode("utf-8"))
                return {"status": resp.status, "ok": True, "detail": body.get("status") or "ok"}
            return {"status": resp.status, "ok": False}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def main() -> int:
    parser = argparse.ArgumentParser(description="F-06 soak: periodic Host RSS + health sampling")
    parser.add_argument("--interval", type=float, default=30.0, help="Sampling interval in minutes")
    parser.add_argument("--report-dir", type=str, default=str(DEFAULT_REPORT_DIR), help="Output directory")
    parser.add_argument("--pid-file", type=str, default=str(DEFAULT_PID_FILE), help="Host PID file")
    parser.add_argument("--health-url", type=str, default=DEFAULT_HEALTH_URL, help="Host /health URL")
    parser.add_argument("--once", action="store_true", help="Run one sample and exit (no loop)")
    args = parser.parse_args()

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    date_tag = datetime.now(timezone.utc).strftime("%Y%m%d")
    out_json = report_dir / f"F-06_soak_samples_{date_tag}.json"
    out_md = report_dir / f"F-06_soak_samples_{date_tag}.md"

    pid_file = Path(args.pid_file)
    interval_sec = max(1.0, args.interval * 60.0)
    samples: list[dict] = []

    def load_existing() -> list[dict]:
        if out_json.exists():
            try:
                with open(out_json, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def save(samples_list: list[dict]) -> None:
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(samples_list, f, ensure_ascii=False, indent=2)
        with open(out_md, "w", encoding="utf-8") as f:
            f.write("# F-06 长稳 Soak 采样\n\n")
            f.write(f"- 采样间隔: {args.interval} 分钟\n")
            f.write(f"- 样本数: {len(samples_list)}\n\n")
            f.write("| ts_utc | rss_mb | health_ok |\n")
            f.write("|--------|--------|------------|\n")
            for s in samples_list[-100:]:
                rss = s.get("rss_mb")
                health = s.get("health")
                ok = (health or {}).get("ok") if isinstance(health, dict) else None
                f.write(f"| {s.get('ts_utc', '')} | {rss if rss is not None else '-'} | {ok} |\n")

    samples = load_existing()
    sample_count = 0

    while True:
        ts = _now_iso()
        pid = _read_pid(pid_file)
        rss_mb = _rss_mb(pid) if pid else None
        health = _health_status(args.health_url)

        row = {
            "ts_utc": ts,
            "pid": pid,
            "rss_mb": rss_mb,
            "health": health,
        }
        samples.append(row)
        save(samples)
        sample_count += 1
        health_ok = (health.get("ok") if isinstance(health, dict) else None)
        print(f"[F-06 soak] {ts} pid={pid} rss_mb={rss_mb} health_ok={health_ok} (sample #{sample_count})")

        if args.once:
            break
        time.sleep(interval_sec)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
