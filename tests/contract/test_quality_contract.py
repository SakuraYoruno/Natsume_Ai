"""T-Quality-01: 代码质量加固契约测试。

验收断言：
- 零裸 print() 残留（F-13）
- 公开函数/方法有返回类型标注（F-14/F-15）
- _fetch_all 传入非法表名时抛出 ValueError（F-16）
- _table_has_column 首次调用后缓存命中（F-06）
- requirements.txt 标注 redis 为可选依赖（F-12）
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Any

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger, _ALLOWED_TABLES


# ---------------------------------------------------------------------------
# 辅助：项目根目录
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
HOST_BRAIN_DIR = PROJECT_ROOT / "host_brain"


# ---------------------------------------------------------------------------
# F-13: 零裸 print() 残留
# ---------------------------------------------------------------------------

class _PrintCallVisitor(ast.NodeVisitor):
    """AST visitor that detects bare print() calls (not logger.xxx or print as attribute)."""

    def __init__(self) -> None:
        self.findings: list[tuple[str, int]] = []
        self._file: str = ""

    def check_file(self, filepath: str) -> None:
        self._file = filepath
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        try:
            tree = ast.parse(source, filename=filepath)
        except SyntaxError:
            return
        self.visit(tree)

    def visit_Call(self, node: ast.Call) -> None:
        # 检查裸 print(...)：func 是 Name 且 id == "print"
        if isinstance(node.func, ast.Name) and node.func.id == "print":
            self.findings.append((self._file, node.lineno))
        self.generic_visit(node)


def test_no_bare_print_in_host_brain() -> None:
    """F-13: host_brain/ 下不应存在裸 print() 调用。"""
    visitor = _PrintCallVisitor()
    for root, _dirs, files in os.walk(str(HOST_BRAIN_DIR)):
        for fname in files:
            if not fname.endswith(".py"):
                continue
            filepath = os.path.join(root, fname)
            visitor.check_file(filepath)

    if visitor.findings:
        detail = "\n".join(f"  {f}:{line}" for f, line in visitor.findings)
        pytest.fail(f"裸 print() 残留:\n{detail}")


# ---------------------------------------------------------------------------
# F-14/F-15: 关键公开函数/方法有返回类型标注
# ---------------------------------------------------------------------------

def test_graph_executor_compile_has_return_annotation() -> None:
    """F-14: GraphExecutor.compile_runtime_graph 必须有返回类型标注。"""
    from host_brain.integrations.graph_executor import GraphExecutor
    method = GraphExecutor.compile_runtime_graph
    annotations = getattr(method, "__annotations__", {})
    assert "return" in annotations, (
        "compile_runtime_graph 缺少返回类型标注 (-> Any 或具体类型)"
    )


def test_fastapi_panel_page_has_return_annotation() -> None:
    """F-15: fastapi_app.panel_page 必须有返回类型标注。"""
    from host_brain.app.fastapi_app import panel_page
    annotations = getattr(panel_page, "__annotations__", {})
    assert "return" in annotations, "panel_page 缺少返回类型标注"


def test_fastapi_root_has_return_annotation() -> None:
    """F-15: fastapi_app.root 必须有返回类型标注。"""
    from host_brain.app.fastapi_app import root
    annotations = getattr(root, "__annotations__", {})
    assert "return" in annotations, "root 缺少返回类型标注"


# ---------------------------------------------------------------------------
# F-16: _fetch_all 传入非法表名时抛出 ValueError
# ---------------------------------------------------------------------------

def test_fetch_all_rejects_invalid_table_name() -> None:
    """F-16: _fetch_all 传入非法表名时必须抛出 ValueError。"""
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        with pytest.raises(ValueError, match="AUDIT_TABLE_NAME_INVALID"):
            ledger._fetch_all("robert_drop_tables")
    finally:
        ledger.close()


def test_fetch_all_accepts_valid_table_name() -> None:
    """F-16: _fetch_all 传入合法表名时正常返回。"""
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        result = ledger._fetch_all("intent_log")
        assert isinstance(result, list)
    finally:
        ledger.close()


def test_allowed_tables_covers_all_properties() -> None:
    """F-16: _ALLOWED_TABLES 必须覆盖所有 SQLiteLedger 的 property 使用的表名。"""
    # 收集所有 property 访问 _fetch_all 的表名
    property_tables: set[str] = set()
    for attr_name in dir(SQLiteLedger):
        attr = getattr(SQLiteLedger, attr_name, None)
        if isinstance(attr, property) and attr.fget is not None:
            import inspect
            source = inspect.getsource(attr.fget)
            matches = re.findall(r'_fetch_all\(["\'](\w+)["\']\)', source)
            property_tables.update(matches)

    missing = property_tables - _ALLOWED_TABLES
    assert not missing, f"以下表名在 property 中使用但未加入白名单: {missing}"


# ---------------------------------------------------------------------------
# F-06: _table_has_column 缓存命中
# ---------------------------------------------------------------------------

def test_table_has_column_uses_cache() -> None:
    """F-06: _table_has_column 首次调用后缓存命中，不再触发 PRAGMA。"""
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        # 清空缓存
        ledger._column_cache.clear()

        # 首次调用：应触发 PRAGMA 并填充缓存
        result1 = ledger._table_has_column("intent_log", "request_id")
        assert result1 is True
        assert "intent_log" in ledger._column_cache

        # 记录当前缓存内容
        cached_columns = set(ledger._column_cache["intent_log"])

        # 第二次调用：应命中缓存
        result2 = ledger._table_has_column("intent_log", "request_id")
        assert result2 is True

        # 验证缓存未变化（说明没有重新查询 PRAGMA）
        assert ledger._column_cache["intent_log"] == cached_columns

        # 验证不存在的列
        result3 = ledger._table_has_column("intent_log", "nonexistent_column")
        assert result3 is False
    finally:
        ledger.close()


# ---------------------------------------------------------------------------
# F-12: requirements.txt 标注 redis 为可选依赖
# ---------------------------------------------------------------------------

def test_requirements_txt_marks_redis_optional() -> None:
    """F-12: requirements.txt 中 redis 必须标注为可选依赖。"""
    req_path = PROJECT_ROOT / "requirements.txt"
    assert req_path.exists(), "requirements.txt 不存在"
    content = req_path.read_text(encoding="utf-8")
    # 必须有"可选"相关注释 + redis 字样
    assert "redis" in content.lower(), "requirements.txt 中未提及 redis"
    assert "可选" in content or "optional" in content.lower(), (
        "requirements.txt 中 redis 未标注为可选依赖"
    )
