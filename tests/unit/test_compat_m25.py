"""T-COMPAT-01：asyncio DeprecationWarning 兼容性测试（TASK-0220 M25-D）。"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# T-COMPAT-01：vts_driver.py 无 asyncio.get_event_loop() 调用
# ---------------------------------------------------------------------------

class TestAsyncioCompatibility:
    """T-COMPAT-01：edge_client/actuators/vts_driver.py 无 asyncio.get_event_loop() 调用。

    Python 3.10+ 中 asyncio.get_event_loop() 在没有运行中的事件循环时会触发
    DeprecationWarning，应改用 asyncio.get_running_loop() 或 asyncio.new_event_loop()。
    """

    def test_no_get_event_loop_in_vts_driver(self) -> None:
        vts_driver_path = (
            Path(__file__).parent.parent.parent
            / "edge_client" / "actuators" / "vts_driver.py"
        )
        assert vts_driver_path.exists(), f"vts_driver.py 不存在: {vts_driver_path}"

        source = vts_driver_path.read_text(encoding="utf-8")
        # 检查是否有 asyncio.get_event_loop() 调用（排除注释行）
        lines_with_issue = []
        for i, line in enumerate(source.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "asyncio.get_event_loop()" in line:
                lines_with_issue.append((i, line.rstrip()))

        assert not lines_with_issue, (
            f"vts_driver.py 中存在 asyncio.get_event_loop() 调用（Python 3.10+ DeprecationWarning）：\n"
            + "\n".join(f"  行 {ln}: {code}" for ln, code in lines_with_issue)
        )

    def test_uses_new_event_loop_or_get_running_loop(self) -> None:
        """验证 vts_driver.py 使用了推荐的 asyncio API。"""
        vts_driver_path = (
            Path(__file__).parent.parent.parent
            / "edge_client" / "actuators" / "vts_driver.py"
        )
        source = vts_driver_path.read_text(encoding="utf-8")

        has_new_event_loop = "asyncio.new_event_loop()" in source
        has_get_running_loop = "asyncio.get_running_loop()" in source
        has_run_coroutine_threadsafe = "asyncio.run_coroutine_threadsafe" in source

        assert has_new_event_loop or has_get_running_loop or has_run_coroutine_threadsafe, (
            "vts_driver.py 应使用 asyncio.new_event_loop() 或 asyncio.get_running_loop() "
            "或 asyncio.run_coroutine_threadsafe 替代 asyncio.get_event_loop()"
        )

    def test_no_get_event_loop_in_edge_client(self) -> None:
        """验证整个 edge_client 目录无 asyncio.get_event_loop() 调用。"""
        edge_client_path = (
            Path(__file__).parent.parent.parent / "edge_client"
        )
        if not edge_client_path.exists():
            pytest.skip("edge_client 目录不存在")

        issues: list[tuple[str, int, str]] = []
        for py_file in edge_client_path.rglob("*.py"):
            source = py_file.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(source.splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "asyncio.get_event_loop()" in line:
                    issues.append((str(py_file.relative_to(edge_client_path)), i, line.rstrip()))

        assert not issues, (
            f"edge_client 中存在 asyncio.get_event_loop() 调用：\n"
            + "\n".join(f"  {f}:{ln}: {code}" for f, ln, code in issues)
        )
