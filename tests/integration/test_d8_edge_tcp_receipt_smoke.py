"""Process-level proof for the loopback-only Host-to-Edge receipt smoke."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SMOKE_PATH = ROOT / "scripts/runtime/test_edge_tcp_receipt_smoke.py"
SPEC = importlib.util.spec_from_file_location("d8_edge_tcp_receipt_smoke", SMOKE_PATH)
assert SPEC is not None and SPEC.loader is not None
SMOKE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SMOKE
SPEC.loader.exec_module(SMOKE)


def test_real_tcp_hmac_edge_sqlite_receipt_smoke(tmp_path: Path) -> None:
    result = SMOKE.run_smoke(root=ROOT, work_dir=tmp_path, timeout_s=60)

    assert result["status"] == "PASS"
    assert result["host_transport"] == "HttpEdgeTransportPort + EdgeRpcEmbodimentCommandPort"
    assert result["minimal_profile"]["bind_host"] == "127.0.0.1"
    assert result["minimal_profile"]["asr_tts_gateway_qq_vts_disabled"] is True
    assert result["receipt"]["status"] == "DEGRADED"
    assert result["receipt"]["reason_code"] == "MATEENGINE_UNAVAILABLE"
    assert result["receipt"]["duplicate_exact"] is True
    assert result["receipt"]["read_query_exact"] is True
    assert result["receipt"]["restart_replay_exact"] is True
    assert result["receipt"]["physical_effect_status"] == "NOT_TESTED"
    assert result["durability"]["command_row_count"] == 1
    assert result["durability"]["receipt_sha256_verified"] is True
