"""Edge 存储的显式 SQLite 连接与事务权威。"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator


class EdgeSqliteDatabase:
    """为各 Edge 账本统一提供 WAL、完整同步和立即写事务。"""
    def __init__(self, db_path: str | Path, *, timeout_ms: int = 5_000) -> None:
        self.db_path = Path(str(db_path)).expanduser().resolve()
        self.timeout_ms = max(1, int(timeout_ms))

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        # 连接的所有权只限于当前上下文：Edge worker 不共享 sqlite3.Connection，
        # 从而避免线程间事务状态、超时和关闭时机相互污染。
        # 每次操作使用短连接，避免跨线程共享 sqlite 对象；WAL 支持读写并行，FULL
        # 同步优先保证命令/收据在设备副作用附近的崩溃恢复语义。
        connection = sqlite3.connect(
            str(self.db_path),
            timeout=self.timeout_ms / 1000.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        # 命令领取、栅栏和回执都要在设备突然断电后仍可恢复；因此宁可牺牲
        # 一部分吞吐，也不能把 FULL 同步降级为仅依赖操作系统缓存。
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute(f"PRAGMA busy_timeout = {self.timeout_ms}")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        # BEGIN IMMEDIATE 在读取待处理记录前取得写入预约。这样两个 worker 不会
        # 都基于旧快照认领同一副作用，后续的 claim/fencing 判断才有意义。
        # BEGIN IMMEDIATE 在读取后就取得写预约，防止两个 worker 都认为同一幂等键
        # 尚未处理；异常包含 BaseException，确保取消也不会留下半提交事务。
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                # 连取消或进程级异常也不能留下半个状态转换；调用方可从上一次
                # 已提交状态重新领取，而不是猜测数据库是否写入成功。
                connection.rollback()
                raise


__all__ = ["EdgeSqliteDatabase"]
