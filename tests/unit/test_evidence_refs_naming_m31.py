"""M31-F：evidence_refs 命名口径统一测试（T-EVREF-01）。

DevSpec 8.3.1：Evidence Pack 字段命名规范要求使用 evidence_refs（复数）。
recall_fusion.py 中不得出现孤立的 evidence_ref（单数）键名。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


# ── T-EVREF-01：静态扫描 recall_fusion.py，断言不含孤立的 evidence_ref 单数 ──

def test_evref_01_no_singular_evidence_ref():
    """T-EVREF-01：静态扫描 recall_fusion.py，断言不含孤立的 evidence_ref（单数，排除 evidence_refs 子串）。"""
    source_path = Path(__file__).parent.parent.parent / "host_brain" / "retrieval" / "recall_fusion.py"
    assert source_path.exists(), f"T-EVREF-01 FAILED: recall_fusion.py 不存在: {source_path}"

    source = source_path.read_text(encoding="utf-8")

    # 查找孤立的 evidence_ref（单数）：
    # 匹配 "evidence_ref" 但不是 "evidence_refs" 的子串
    # 使用负向前瞻：evidence_ref 后面不跟 s
    pattern = r'"evidence_ref"(?!s)'
    matches = re.findall(pattern, source)

    # 允许的例外：向后兼容的 fallback 读取（metadata.get("evidence_ref")）
    # 这些是读取旧数据的兼容代码，不是写入新数据的键名
    # 过滤掉 .get("evidence_ref") 形式的读取
    source_lines = source.split("\n")
    violations: list[str] = []
    for i, line in enumerate(source_lines, 1):
        # 查找 "evidence_ref" 单数（不是 evidence_refs）
        if re.search(r'"evidence_ref"(?!s)', line):
            # 排除向后兼容的 .get() 读取
            if '.get("evidence_ref")' in line:
                continue
            violations.append(f"  第 {i} 行: {line.strip()}")

    assert not violations, (
        "T-EVREF-01 FAILED: recall_fusion.py 中发现孤立的 evidence_ref（单数）键名，"
        "违反 DevSpec 8.3.1 命名规范：\n" + "\n".join(violations)
    )
