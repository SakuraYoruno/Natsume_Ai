"""T-RT-01: 启动结果 JSON 字段完整性 + OBS/MCP 状态可观测。

验收标准（DevSpec §18.1.1）：
- 正常启动：obs_status=ok、mcp_registry_status=ok、startup_warnings=[]
- OBS 不可用：obs_status=degraded、startup_warnings 含 STARTUP_OBS_DEGRADED，进程仍启动
- MCP SQLite 不可写：mcp_registry_status=degraded、startup_warnings 含 STARTUP_MCP_DEGRADED，进程仍启动
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "runtime"

# ── 必须存在的顶层字段 ──────────────────────────────────────────────────────
REQUIRED_FIELDS = {
    "side",
    "status",
    "pid",
    "obs_status",
    "mcp_registry_status",
    "startup_warnings",
}

VALID_OBS_STATUSES = {"ok", "degraded"}
VALID_MCP_STATUSES = {"ok", "degraded"}
VALID_SIDES = {"host"}
VALID_STATUSES = {"started", "already_running"}


def _load_fixture(name: str) -> dict:
    path = FIXTURES_DIR / name
    return json.loads(path.read_text(encoding="utf-8-sig"))


# ── T-RT-01-A: 正常启动 fixture 字段完整性 ──────────────────────────────────

class TestStartupOkFixture:
    """T-RT-01-A: 正常启动结果 JSON 字段完整性。"""

    def setup_method(self):
        self.data = _load_fixture("startup_ok.json")

    def test_required_fields_present(self):
        """所有必须字段均存在。"""
        for field in REQUIRED_FIELDS:
            assert field in self.data, f"缺少必须字段: {field}"

    def test_side_is_host(self):
        assert self.data["side"] in VALID_SIDES

    def test_status_is_valid(self):
        assert self.data["status"] in VALID_STATUSES

    def test_obs_status_ok(self):
        assert self.data["obs_status"] == "ok"

    def test_mcp_registry_status_ok(self):
        assert self.data["mcp_registry_status"] == "ok"

    def test_startup_warnings_empty(self):
        assert self.data["startup_warnings"] == []

    def test_pid_is_positive_int(self):
        assert isinstance(self.data["pid"], int)
        assert self.data["pid"] > 0


# ── T-RT-01-B: OBS 降级 fixture ─────────────────────────────────────────────

class TestStartupObsDegradedFixture:
    """T-RT-01-B: OBS 子系统不可用时，obs_status=degraded，进程仍启动。"""

    def setup_method(self):
        self.data = _load_fixture("startup_obs_degraded.json")

    def test_required_fields_present(self):
        for field in REQUIRED_FIELDS:
            assert field in self.data, f"缺少必须字段: {field}"

    def test_status_is_started(self):
        """进程仍启动（不阻断）。"""
        assert self.data["status"] in VALID_STATUSES

    def test_obs_status_degraded(self):
        assert self.data["obs_status"] == "degraded"

    def test_mcp_registry_status_ok(self):
        assert self.data["mcp_registry_status"] == "ok"

    def test_startup_warnings_contains_obs_degraded(self):
        assert "STARTUP_OBS_DEGRADED" in self.data["startup_warnings"]

    def test_startup_warnings_is_list(self):
        assert isinstance(self.data["startup_warnings"], list)


# ── T-RT-01-C: MCP 降级 fixture ─────────────────────────────────────────────

class TestStartupMcpDegradedFixture:
    """T-RT-01-C: MCP 注册表不可用时，mcp_registry_status=degraded，进程仍启动。"""

    def setup_method(self):
        self.data = _load_fixture("startup_mcp_degraded.json")

    def test_required_fields_present(self):
        for field in REQUIRED_FIELDS:
            assert field in self.data, f"缺少必须字段: {field}"

    def test_status_is_started(self):
        assert self.data["status"] in VALID_STATUSES

    def test_obs_status_ok(self):
        assert self.data["obs_status"] == "ok"

    def test_mcp_registry_status_degraded(self):
        assert self.data["mcp_registry_status"] == "degraded"

    def test_startup_warnings_contains_mcp_degraded(self):
        assert "STARTUP_MCP_DEGRADED" in self.data["startup_warnings"]

    def test_startup_warnings_is_list(self):
        assert isinstance(self.data["startup_warnings"], list)


# ── T-RT-01-D: 错误码常量可导入 ─────────────────────────────────────────────

class TestStartupErrorCodes:
    """T-RT-01-D: M8 启动错误码常量可从 errors.py 导入。"""

    def test_startup_obs_degraded_importable(self):
        from host_brain.core.errors import STARTUP_OBS_DEGRADED
        assert STARTUP_OBS_DEGRADED == "STARTUP_OBS_DEGRADED"

    def test_startup_mcp_degraded_importable(self):
        from host_brain.core.errors import STARTUP_MCP_DEGRADED
        assert STARTUP_MCP_DEGRADED == "STARTUP_MCP_DEGRADED"

    def test_e2e_smoke_failed_importable(self):
        from host_brain.core.errors import E2E_SMOKE_FAILED
        assert E2E_SMOKE_FAILED == "E2E_SMOKE_FAILED"

    def test_perf_baseline_stale_importable(self):
        from host_brain.core.errors import PERF_BASELINE_STALE
        assert PERF_BASELINE_STALE == "PERF_BASELINE_STALE"


# ── T-RT-01-E: obs_status / mcp_registry_status 枚举稳定性 ──────────────────

@pytest.mark.parametrize("fixture_name,expected_obs,expected_mcp", [
    ("startup_ok.json", "ok", "ok"),
    ("startup_obs_degraded.json", "degraded", "ok"),
    ("startup_mcp_degraded.json", "ok", "degraded"),
])
def test_startup_status_enum_stability(fixture_name, expected_obs, expected_mcp):
    """T-RT-01-E: 三种 fixture 的 obs_status/mcp_registry_status 枚举稳定。"""
    data = _load_fixture(fixture_name)
    assert data["obs_status"] == expected_obs, (
        f"{fixture_name}: obs_status 期望 {expected_obs!r}，实际 {data['obs_status']!r}"
    )
    assert data["mcp_registry_status"] == expected_mcp, (
        f"{fixture_name}: mcp_registry_status 期望 {expected_mcp!r}，实际 {data['mcp_registry_status']!r}"
    )
