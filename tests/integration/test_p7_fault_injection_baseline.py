"""P7-4：压测与故障注入基线 — 断言基线用例存在且可执行。

不重复实现故障逻辑，仅验证 P7-4 基线文档所引用的测试存在并可被收集。
"""

from __future__ import annotations

import pytest
from pathlib import Path


def test_p7_4_baseline_intent_fault_tests_exist() -> None:
    """P7-4 基线：Intent 图故障注入用例存在。"""
    root = Path(__file__).resolve().parents[1]
    p6 = root / "integration" / "test_p6_integration_acceptance.py"
    p4 = root / "integration" / "test_p4_intent_subgraph_failure.py"
    assert p6.is_file(), "test_p6_integration_acceptance.py 为 P7-4 Intent 故障基线"
    assert p4.is_file(), "test_p4_intent_subgraph_failure.py 为 P7-4 Intent 子图失败基线"


def test_p7_4_baseline_supervised_fault_tests_exist() -> None:
    """P7-4 基线：Supervised 图故障/回退用例存在。"""
    root = Path(__file__).resolve().parents[1]
    p5 = root / "integration" / "test_p5_supervisor_llm_mode.py"
    assert p5.is_file(), "test_p5_supervisor_llm_mode.py 为 P7-4 Supervised 回退基线"


def test_p7_4_baseline_doc_exists() -> None:
    """P7-4 基线文档存在。"""
    root = Path(__file__).resolve().parents[2]
    doc = root / "docs" / "50_project" / "MULTI_AGENT_P7_4_FAULT_INJECTION_BASELINE_2026-03.md"
    assert doc.is_file(), "MULTI_AGENT_P7_4_FAULT_INJECTION_BASELINE_2026-03.md 须存在"


def test_p7_4_intent_timeout_fault_runnable(monkeypatch) -> None:
    """P7-4：Intent 局部循环超时故障注入可重复运行（与 P6-3 超时用例一致）。"""
    from tests.integration.test_p6_integration_acceptance import (
        _intent_config_with_local_loop_overrides,
        test_p6_3_timeout_integration_local_loop_timeout,
    )
    intent_config = _intent_config_with_local_loop_overrides(local_loop_timeout_ms=15)
    test_p6_3_timeout_integration_local_loop_timeout(monkeypatch, intent_config)
