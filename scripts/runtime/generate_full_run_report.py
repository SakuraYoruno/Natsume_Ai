"""
生成 Host Brain 完整运行报告。
收集：
  - 外部服务状态（Docker 容器）
  - 环境就绪检查（check_external_env.py 输出）
  - Host Brain 服务器状态（/health、/ready、/metrics）
  - 现有测试套件结果（test_host_optimizations.py）
  - 真实环境集成测试结果（最新 run_report_*.json）
"""
from __future__ import annotations

import glob
import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
REPORT_DIR = ROOT
HOST_URL = f"http://127.0.0.1:{os.environ.get('HOST_APP_PORT', '18080')}"


def _http_get(url: str, timeout: int = 10):
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}
    except Exception as e:
        return -1, {"error": str(e)}


def collect_docker_status():
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{json .}}"],
            capture_output=True, text=True, timeout=15
        )
        containers = []
        for line in result.stdout.strip().splitlines():
            if line:
                try:
                    containers.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        natsume = [c for c in containers if "natsume" in c.get("Names", "").lower()]
        return {"ok": True, "containers": natsume}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def collect_env_check():
    try:
        env_script = ROOT / "scripts" / "runtime" / "check_external_env.py"
        result = subprocess.run(
            [sys.executable, str(env_script)],
            capture_output=True, text=True, timeout=60, cwd=str(ROOT)
        )
        return json.loads(result.stdout) if result.stdout.strip() else {"error": result.stderr[:500]}
    except Exception as e:
        return {"error": str(e)}


def collect_host_status():
    health_code, health = _http_get(f"{HOST_URL}/health")
    ready_code, ready = _http_get(f"{HOST_URL}/ready")
    metrics_code, metrics = _http_get(f"{HOST_URL}/metrics")
    return {
        "url": HOST_URL,
        "health": {"status_code": health_code, "data": health},
        "ready": {"status_code": ready_code, "data": ready},
        "metrics": {"status_code": metrics_code, "data": metrics},
    }


def collect_codex_immune_scheduler_probe():
    try:
        script_path = ROOT / "scripts" / "runtime" / "test_codex_immune_scheduler.py"
        spec = importlib.util.spec_from_file_location("test_codex_immune_scheduler", script_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load runtime probe module: {script_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        report = module.build_probe_report(base_url=HOST_URL)
        return {"ok": bool(report.get("overall", {}).get("passed")), "data": report}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def collect_unit_test_results():
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / "test_host_optimizations.py")],
            capture_output=True, text=True, timeout=60, cwd=str(ROOT),
            env={**os.environ,
                 "HOST_LLM_PROVIDER": "mock",
                 "HOST_COLD_START_ALLOWED": "true",
                 "HOST_ENABLE_RETRIEVAL": "false",
                 "GEMINI_API_KEY": "test-key-mock",
                 "OPENAI_API_KEY": "test-key-mock"}
        )
        lines = (result.stdout + result.stderr).splitlines()
        summary_line = next((l for l in reversed(lines) if "测试结果" in l), "")
        passed = failed = total = 0
        import re
        m = re.search(r"(\d+)/(\d+)\s+通过.*?(\d+)\s+失败", summary_line)
        if m:
            passed, total, failed = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return {
            "ok": result.returncode == 0,
            "exit_code": result.returncode,
            "summary": summary_line.strip(),
            "passed": passed,
            "total": total,
            "failed": failed,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def collect_integration_test_results():
    reports = sorted(glob.glob(str(ROOT / "run_report_run_*.json")), reverse=True)
    if not reports:
        return {"ok": False, "error": "no integration test reports found"}
    latest = Path(reports[0])
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
        return {"ok": data["summary"]["failed"] == 0, "report_file": latest.name, "data": data}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def main():
    print("正在收集所有运行数据...", flush=True)

    ts = int(time.time())
    report = {
        "report_id": f"full_run_{ts}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sections": {}
    }

    print("  [1/5] Docker 容器状态...", flush=True)
    report["sections"]["docker_services"] = collect_docker_status()

    print("  [2/5] 环境就绪检查...", flush=True)
    report["sections"]["env_readiness"] = collect_env_check()

    print("  [3/5] Host Brain 服务状态...", flush=True)
    report["sections"]["host_brain_status"] = collect_host_status()
    report["sections"]["codex_immune_scheduler"] = collect_codex_immune_scheduler_probe()

    print("  [4/5] 单元测试套件...", flush=True)
    report["sections"]["unit_tests"] = collect_unit_test_results()

    print("  [5/5] 真实环境集成测试...", flush=True)
    report["sections"]["integration_tests"] = collect_integration_test_results()

    # 综合评估
    s = report["sections"]
    docker_ok = s["docker_services"].get("ok", False)
    env_ok = s["env_readiness"].get("ready", False)
    host_ok = s["host_brain_status"]["health"]["status_code"] == 200
    codex_scheduler_ok = s["codex_immune_scheduler"].get("ok", False)
    unit_ok = s["unit_tests"].get("ok", False)
    integ_ok = s["integration_tests"].get("ok", False)

    report["overall"] = {
        "docker_services": "PASS" if docker_ok else "FAIL",
        "env_readiness": "PASS" if env_ok else "FAIL",
        "host_brain_up": "PASS" if host_ok else "FAIL",
        "codex_immune_scheduler": "PASS" if codex_scheduler_ok else "FAIL",
        "unit_tests": "PASS" if unit_ok else "FAIL",
        "integration_tests": "PASS" if integ_ok else "FAIL",
        "all_pass": all([docker_ok, env_ok, host_ok, codex_scheduler_ok, unit_ok, integ_ok]),
    }

    report_path = ROOT / f"FULL_RUN_REPORT_{ts}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'='*60}")
    print(f"完整运行报告：{report_path.name}")
    print(f"{'='*60}")
    for k, v in report["overall"].items():
        if k != "all_pass":
            icon = "✓" if v == "PASS" else "✗"
            print(f"  {icon}  {k:<25} {v}")
    print(f"{'='*60}")
    print(f"  总体结果: {'ALL PASS' if report['overall']['all_pass'] else 'SOME FAILURES'}")
    print(f"  报告路径: {report_path}")
    print(f"{'='*60}\n")

    return report_path, report


if __name__ == "__main__":
    path, _ = main()
    sys.exit(0)
