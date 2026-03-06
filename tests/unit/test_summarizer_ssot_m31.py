"""M31-E：summarizer 不得写回 Neo4j 静态扫描断言测试（T-SSOT-SUMM-01~02）。

DevSpec 8.2：summarizer.py 只能派生 summary，不得写回 SSOT（Neo4j Bond）。
DevSpec 8.8：T-SSOT-01 静态扫描断言必须在 CI 中自动拦截违规写回。
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from host_brain.bond.summarizer import derive_bond_summary


# ── T-SSOT-SUMM-01：静态扫描 summarizer.py，断言不含写入关键词 ──────────────

# 禁止出现的写入关键词（正则模式）
_WRITE_KEYWORDS = [
    r"\bbond_dynamics\b",
    r"\bneo4j\b",
    r"\.write\b",
    r"\.upsert\b",
    r"\.create\b",
    r"\.merge\b",
    r"\.execute\b",
    r"\.run\b",
    r"INSERT\b",
    r"UPDATE\b",
    r"DELETE\b",
    r"MERGE\b",
]


def test_ssot_summ_01_no_write_keywords_in_summarizer():
    """T-SSOT-SUMM-01：静态扫描 summarizer.py 源码，断言不含写入关键词（排除注释和文档字符串）。"""
    source_path = Path(__file__).parent.parent.parent / "host_brain" / "bond" / "summarizer.py"
    assert source_path.exists(), f"T-SSOT-SUMM-01 FAILED: summarizer.py 不存在: {source_path}"

    source = source_path.read_text(encoding="utf-8")

    # 过滤掉注释行和文档字符串，只扫描实际代码行
    code_lines: list[str] = []
    in_docstring = False
    docstring_char = None
    for line in source.split("\n"):
        stripped = line.strip()
        # 处理三引号文档字符串
        if not in_docstring:
            if stripped.startswith('"""') or stripped.startswith("'''"):
                docstring_char = stripped[:3]
                # 单行文档字符串（开头和结尾在同一行）
                rest = stripped[3:]
                if docstring_char in rest:
                    continue  # 单行文档字符串，跳过
                in_docstring = True
                continue
            # 跳过注释行
            if stripped.startswith("#"):
                continue
            # 去除行内注释
            code_part = line.split("#")[0]
            code_lines.append(code_part)
        else:
            if docstring_char and docstring_char in stripped:
                in_docstring = False
            continue

    code_text = "\n".join(code_lines)

    violations: list[str] = []
    for pattern in _WRITE_KEYWORDS:
        matches = re.findall(pattern, code_text, re.IGNORECASE)
        if matches:
            violations.append(f"关键词 '{pattern}' 命中 {len(matches)} 次")

    assert not violations, (
        "T-SSOT-SUMM-01 FAILED: summarizer.py 代码中发现写入关键词，违反 DevSpec 8.2：\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


# ── T-SSOT-SUMM-02：derive_bond_summary() 不触发任何数据库写入副作用 ─────────

def test_ssot_summ_02_no_db_write_side_effects():
    """T-SSOT-SUMM-02：mock bond_dynamics 写入方法后，调用 derive_bond_summary() 不触发任何 mock 调用。"""
    # 创建 mock bond_dynamics 模块，监控所有写入方法
    mock_bond_dynamics = MagicMock()
    mock_write_methods = [
        "write", "upsert", "create", "merge", "execute", "run",
        "update_bond", "save_bond", "persist_bond",
    ]

    # 在 summarizer 模块命名空间中注入 mock（如果 summarizer 导入了 bond_dynamics）
    import host_brain.bond.summarizer as summarizer_module
    original_attrs = {}
    for attr in mock_write_methods:
        if hasattr(summarizer_module, attr):
            original_attrs[attr] = getattr(summarizer_module, attr)
            setattr(summarizer_module, attr, mock_bond_dynamics)

    try:
        # 调用 derive_bond_summary()
        event = {
            "event_id": "evt_test_001",
            "stage": "S_BOND_UPDATE",
            "trend": "RISING",
            "capability_hint": "CREATIVE",
            "evidence_refs": ["evd://test/001"],
        }
        result = derive_bond_summary(event)

        # 验证返回值为纯数据结构
        assert isinstance(result, dict), (
            f"T-SSOT-SUMM-02 FAILED: derive_bond_summary() 返回非 dict 类型: {type(result)}"
        )
        assert "stage" in result, "T-SSOT-SUMM-02 FAILED: 返回值缺少 stage 字段"
        assert "evidence_refs" in result, "T-SSOT-SUMM-02 FAILED: 返回值缺少 evidence_refs 字段"

        # 验证 mock 写入方法未被调用
        for attr in mock_write_methods:
            if attr in original_attrs:
                mock_bond_dynamics.assert_not_called()

    finally:
        # 恢复原始属性
        for attr, original in original_attrs.items():
            setattr(summarizer_module, attr, original)
