"""独立 Edge 存储的版本化迁移入口。

启动时按文件名顺序执行受版本控制的 SQL，使本机传输库的结构可重复建立；
该函数只负责迁移编排，不承担业务数据恢复或跨节点同步。
"""

from __future__ import annotations

from pathlib import Path
import sqlite3


MIGRATIONS_ROOT = Path(__file__).with_name("migrations")


def apply_edge_gateway_migrations(db_path: str | Path) -> tuple[str, ...]:
    # 连接采用 autocommit，迁移脚本自身定义原子边界；逐份记录已执行文件名，
    # 方便调用方将实际 schema 进展与启动收据关联。
    path = Path(str(db_path)).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    applied: list[str] = []
    connection = sqlite3.connect(str(path), isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        for migration in sorted(MIGRATIONS_ROOT.glob("[0-9][0-9][0-9][0-9]_*.sql")):
            connection.executescript(migration.read_text(encoding="utf-8-sig"))
            applied.append(migration.name)
    finally:
        connection.close()
    return tuple(applied)


__all__ = ["MIGRATIONS_ROOT", "apply_edge_gateway_migrations"]
