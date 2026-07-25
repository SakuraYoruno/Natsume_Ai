"""
test_bond_sqlite_persistence.py —— 审计修复 #10 单元测试

验证 SqliteBondClient 持久化功能：
1. 写入事件后重新连接仍可读取（进程重启模拟）
2. build_host_bond_client() 根据环境变量正确选择客户端类型
3. Host Bond writer 使用同一持久化客户端
"""
from __future__ import annotations

import os
import shutil
import pytest
from pathlib import Path
from unittest.mock import patch

from host_brain.bond.neo4j_client import (
    BOND_WRITER_ID,
    InMemoryNeo4jBondClient,
    SqliteBondClient,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner


@pytest.fixture(scope="module")
def bond_schema_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    db_path = tmp_path_factory.mktemp("bond-schema") / "template.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="bond-sqlite-unit-template",
        ts_ms=1_800_000_000_000,
    )
    assert result.success is True, result.as_dict()
    return db_path


@pytest.fixture
def migrated_bond_db(bond_schema_template: Path, tmp_path: Path) -> Path:
    db_path = tmp_path / "bond.sqlite3"
    shutil.copyfile(bond_schema_template, db_path)
    return db_path


# ---------------------------------------------------------------------------
# 测试：SqliteBondClient 持久化
# ---------------------------------------------------------------------------

class TestSqliteBondClientPersistence:
    """SqliteBondClient 持久化测试。"""

    def test_write_and_read_survives_reconnect(self, migrated_bond_db: Path):
        """验证写入事件后重新连接（模拟进程重启）仍可读取。"""
        db_path = migrated_bond_db

        # 第一次连接：写入事件
        client1 = SqliteBondClient(db_path=db_path)
        result = client1.write_bond_event(
            writer_id=BOND_WRITER_ID,
            event_id="evt-001",
            user_id="user-alice",
            session_id="session-001",
            topic_id="topic-chat",
            capability_hint="chat",
            evidence_refs=["ref-001"],
            payload={"emotion_tag": "JOY", "emotion_intensity": 0.8},
            ts_ms=1000000,
        )
        assert result["event_id"] == "evt-001"

        # 第二次连接（模拟进程重启）：读取事件
        client2 = SqliteBondClient(db_path=db_path)
        event = client2.get_event("evt-001")
        assert event is not None
        assert event["event_id"] == "evt-001"
        assert event["user_id"] == "user-alice"
        assert event["payload"]["emotion_tag"] == "JOY"

    def test_trace_user_events_returns_all_events(self, migrated_bond_db: Path):
        """验证 trace_user_events 返回用户所有事件（按时间排序）。"""
        db_path = migrated_bond_db
        client = SqliteBondClient(db_path=db_path)

        for i in range(3):
            client.write_bond_event(
                writer_id=BOND_WRITER_ID,
                event_id=f"evt-{i:03d}",
                user_id="user-bob",
                session_id="session-001",
                topic_id="topic-chat",
                capability_hint="chat",
                evidence_refs=[f"ref-{i}"],
                payload={"seq": i},
                ts_ms=1000000 + i * 1000,
            )

        events = client.trace_user_events("user-bob")
        assert len(events) == 3
        # 验证按时间升序排列
        ts_list = [e["ts_ms"] for e in events]
        assert ts_list == sorted(ts_list)

    def test_upsert_replaces_existing_event(self, migrated_bond_db: Path):
        """验证 INSERT OR REPLACE 语义：相同 event_id 会覆盖旧记录。"""
        db_path = migrated_bond_db
        client = SqliteBondClient(db_path=db_path)

        client.write_bond_event(
            writer_id=BOND_WRITER_ID,
            event_id="evt-dup",
            user_id="user-carol",
            session_id="session-001",
            topic_id="topic-chat",
            capability_hint="chat",
            evidence_refs=["ref-001"],
            payload={"version": 1},
            ts_ms=1000000,
        )
        client.write_bond_event(
            writer_id=BOND_WRITER_ID,
            event_id="evt-dup",
            user_id="user-carol",
            session_id="session-001",
            topic_id="topic-chat",
            capability_hint="chat",
            evidence_refs=["ref-001"],
            payload={"version": 2},
            ts_ms=1000001,
        )

        event = client.get_event("evt-dup")
        assert event is not None
        assert event["payload"]["version"] == 2

    def test_single_writer_policy_enforced(self, migrated_bond_db: Path):
        """验证单写者策略：非法 writer_id 抛出 PermissionError。"""
        db_path = migrated_bond_db
        client = SqliteBondClient(db_path=db_path)

        with pytest.raises(PermissionError):
            client.write_bond_event(
                writer_id="illegal_writer",
                event_id="evt-illegal",
                user_id="user-dave",
                session_id="session-001",
                topic_id="topic-chat",
                capability_hint="chat",
                evidence_refs=["ref-001"],
                payload={},
                ts_ms=1000000,
            )

    def test_empty_evidence_refs_raises(self, migrated_bond_db: Path):
        """验证空 evidence_refs 抛出 ValueError。"""
        db_path = migrated_bond_db
        client = SqliteBondClient(db_path=db_path)

        with pytest.raises(ValueError):
            client.write_bond_event(
                writer_id=BOND_WRITER_ID,
                event_id="evt-no-evidence",
                user_id="user-eve",
                session_id="session-001",
                topic_id="topic-chat",
                capability_hint="chat",
                evidence_refs=[],
                payload={},
                ts_ms=1000000,
            )

    def test_get_event_returns_none_for_missing(self, migrated_bond_db: Path):
        """验证 get_event 对不存在的 event_id 返回 None。"""
        db_path = migrated_bond_db
        client = SqliteBondClient(db_path=db_path)
        assert client.get_event("nonexistent") is None


# ---------------------------------------------------------------------------
# 测试：build_host_bond_client 环境变量切换
# ---------------------------------------------------------------------------

class TestBuildHostBondClient:
    """build_host_bond_client 环境变量切换测试。"""

    def test_default_returns_in_memory_client(self):
        """验证默认（无环境变量）返回 InMemoryNeo4jBondClient。"""
        from host_brain.composition.host_bond_runtime import build_host_bond_client

        with patch.dict(os.environ, {}, clear=False):
            # 清除相关环境变量
            env_backup = {}
            for key in ("HOST_BOND_BACKEND", "HOST_BOND_SQLITE_PATH"):
                env_backup[key] = os.environ.pop(key, None)
            try:
                client = build_host_bond_client()
                assert isinstance(client, InMemoryNeo4jBondClient)
            finally:
                for key, val in env_backup.items():
                    if val is not None:
                        os.environ[key] = val

    def test_sqlite_backend_returns_sqlite_client(self, tmp_path):
        """验证 HOST_BOND_BACKEND=sqlite 返回 SqliteBondClient。"""
        from host_brain.composition.host_bond_runtime import build_host_bond_client

        db_path = str(tmp_path / "bond_env.db")
        with patch.dict(os.environ, {
            "HOST_BOND_BACKEND": "sqlite",
            "HOST_BOND_SQLITE_PATH": db_path,
        }):
            client = build_host_bond_client()
            assert isinstance(client, SqliteBondClient)

    def test_sqlite_path_env_auto_upgrades_to_sqlite(self, tmp_path):
        """验证仅设置 HOST_BOND_SQLITE_PATH 也会自动升级为 SqliteBondClient。"""
        from host_brain.composition.host_bond_runtime import build_host_bond_client

        db_path = str(tmp_path / "bond_auto.db")
        env_backup = os.environ.pop("HOST_BOND_BACKEND", None)
        try:
            with patch.dict(os.environ, {"HOST_BOND_SQLITE_PATH": db_path}):
                client = build_host_bond_client()
                assert isinstance(client, SqliteBondClient)
        finally:
            if env_backup is not None:
                os.environ["HOST_BOND_BACKEND"] = env_backup


# ---------------------------------------------------------------------------
# 测试：Host Bond writer 使用持久化客户端
# ---------------------------------------------------------------------------

class TestBondWriterClientBinding:
    """验证 Host Bond writer 使用 app-local client builder。"""

    def test_writer_uses_host_bond_client(self, tmp_path):
        """验证 writer_factory 创建的 BondDynamicsWriter 使用持久化客户端。"""
        from host_brain.composition.host_bond_runtime import build_host_bond_client
        from host_brain.bond.bond_dynamics import BondDynamicsWriter

        db_path = str(tmp_path / "bond_queue.db")
        with patch.dict(os.environ, {
            "HOST_BOND_BACKEND": "sqlite",
            "HOST_BOND_SQLITE_PATH": db_path,
        }):
            client = build_host_bond_client()
            writer = BondDynamicsWriter(neo4j_client=client)
            assert isinstance(writer.neo4j_client, SqliteBondClient)
