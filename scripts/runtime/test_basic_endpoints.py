"""基础端点快速测试（不含 LLM 调用）。"""
from __future__ import annotations
import io, json, sys, urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:18080"

def get(path):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=8) as r:
        return json.loads(r.read())

errors = 0

# /health
try:
    r = get("/health")
    s = r.get("status")
    svc = r.get("details", {}).get("summary", {})
    print(f"[health] status={s}  required_total={svc.get('required_total')}  unavailable={svc.get('required_unavailable')}")
    if s != "OK":
        errors += 1
except Exception as e:
    print(f"[health] FAIL: {e}")
    errors += 1

# /ready
try:
    r = get("/ready")
    print(f"[ready]  ready={r.get('ready')}  status={r.get('status')}")
except Exception as e:
    print(f"[ready]  FAIL: {e}")
    errors += 1

# /metrics
try:
    r = get("/metrics")
    print(f"[metrics] uptime={r.get('uptime_seconds')}s  total_requests={r.get('total_requests')}")
except Exception as e:
    print(f"[metrics] FAIL: {e}")
    errors += 1

# /health/canon_sync
try:
    r = get("/health/canon_sync")
    print(f"[canon_sync] {json.dumps(r, ensure_ascii=False)[:200]}")
except Exception as e:
    print(f"[canon_sync] FAIL: {e}")
    errors += 1

print(f"\n{'PASS' if errors == 0 else 'FAIL'} — {errors} errors")
sys.exit(errors)
