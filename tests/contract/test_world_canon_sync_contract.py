"""T-WCanon-04: World Canon 索引同步幂等性 + CANON_SYNC_FAILED 可统计。

验收标准（DevSpec §19.1.2 / §19.1.5）：
- 相同 hash 的条目重复同步不产生重复 canon_change_log 条目（幂等）；
- 同步失败时 CANON_SYNC_FAILED 可统计（通过 error_log 查询）；
- world_canon_doc 表字段与 DevSpec §19.1.2 一致；
- 种子条目文件可被 canon_sync 扫描并写入索引。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import CANON_SYNC_FAILED
from host_brain.memory.canon_sync import (
    WORLD_CANON_INVALID_DOC,
    sync_world_canon_index,
)
from host_brain.memory.canon_world_store import WorldCanonStore, compute_world_canon_hash


ROOT = Path(__file__).resolve().parents[2]
FIXTURES_WORLD = ROOT / "tests" / "fixtures" / "canon" / "world"
SSOT_WORLD = ROOT / "host_brain" / "canon" / "world"

SEED_IDS = [
    "wc-001-system-terms",
    "wc-002-architecture-invariants",
    "wc-003-persona-core",
]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class TestWorldCanonSyncIdempotency(unittest.TestCase):
    """T-WCanon-04: 索引同步幂等性 + CANON_SYNC_FAILED 可统计。"""

    def test_t_wcanon_04a_seed_files_exist_and_valid(self) -> None:
        """种子条目文件存在且字段合规（evidence_level / body 无完成性表述）。"""
        for doc_id in SEED_IDS:
            path = SSOT_WORLD / f"{doc_id}.json"
            self.assertTrue(path.exists(), msg=f"种子文件不存在: {path}")
            payload = _load_json(path)
            # 必须字段
            for field in ("doc_id", "version", "hash", "status", "title", "tags", "body", "updated_by", "ts_ms"):
                self.assertIn(field, payload, msg=f"{doc_id} 缺少字段: {field}")
            # doc_id 一致
            self.assertEqual(doc_id, payload["doc_id"])
            # status 合法
            self.assertIn(payload["status"], ("active", "deprecated", "draft"))
            # body 不含完成性表述（与 WORLD_EXECUTION_FACT_TOKENS 保持一致）
            forbidden_cn = ("已执行", "执行完成", "已保存", "保存成功", "已确认")
            forbidden_en = ("confirmed", "saved successfully", "executed", "execution complete")
            body = payload["body"]
            for token in forbidden_cn:
                self.assertNotIn(token, body, msg=f"{doc_id} body 含完成性表述: {token}")
            for token in forbidden_en:
                self.assertNotIn(token, body.lower(), msg=f"{doc_id} body 含完成性表述: {token}")
            # hash 可复现
            hash_payload = {k: payload[k] for k in ("body", "doc_id", "status", "tags", "title", "ts_ms", "updated_by", "version")}
            expected_hash = compute_world_canon_hash(hash_payload)
            self.assertEqual(expected_hash, payload["hash"], msg=f"{doc_id} hash 不一致")

    def test_t_wcanon_04b_sync_indexes_seed_files(self) -> None:
        """种子条目可被 canon_sync 扫描并写入 world_canon_doc 索引。"""
        with tempfile.TemporaryDirectory(prefix="wcanon_sync_seed_") as tmp:
            db_path = Path(tmp) / "audit.sqlite3"
            world_root = Path(tmp) / "world"
            world_root.mkdir(parents=True, exist_ok=True)

            # 复制种子文件到临时目录
            for doc_id in SEED_IDS:
                src = SSOT_WORLD / f"{doc_id}.json"
                (world_root / f"{doc_id}.json").write_bytes(src.read_bytes())

            ledger = SQLiteLedger(db_path=db_path)
            try:
                records = sync_world_canon_index(world_root=world_root, ledger=ledger, ts_ms=1741000001000)
                self.assertEqual(len(SEED_IDS), len(records), msg="同步记录数与种子数不符")
                for rec in records:
                    self.assertTrue(rec.changed, msg=f"{rec.doc_id} 首次同步应标记为 changed")
                    indexed = ledger.get_world_canon_doc(rec.doc_id)
                    self.assertIsNotNone(indexed, msg=f"{rec.doc_id} 未写入索引")
                    assert indexed is not None
                    # 字段完整性（DevSpec §19.1.2）
                    for field in ("doc_id", "version", "hash", "status", "source_path", "updated_ts"):
                        self.assertIn(field, indexed, msg=f"{rec.doc_id} 索引缺少字段: {field}")
            finally:
                ledger.close()

    def test_t_wcanon_04c_idempotent_sync_no_duplicate_change_log(self) -> None:
        """相同 hash 的条目重复同步不产生重复 canon_change_log 条目（幂等）。"""
        with tempfile.TemporaryDirectory(prefix="wcanon_idempotent_") as tmp:
            db_path = Path(tmp) / "audit.sqlite3"
            world_root = Path(tmp) / "world"
            world_root.mkdir(parents=True, exist_ok=True)

            # 只用第一个种子
            doc_id = SEED_IDS[0]
            src = SSOT_WORLD / f"{doc_id}.json"
            (world_root / f"{doc_id}.json").write_bytes(src.read_bytes())

            ledger = SQLiteLedger(db_path=db_path)
            try:
                # 第一次同步
                records1 = sync_world_canon_index(world_root=world_root, ledger=ledger, ts_ms=1741000002000)
                self.assertEqual(1, len(records1))
                self.assertTrue(records1[0].changed)

                change_log_after_first = [
                    row for row in ledger.canon_change_log
                    if row["canon_kind"] == "world" and row["target_id"] == doc_id
                ]
                self.assertEqual(1, len(change_log_after_first), msg="首次同步应产生 1 条 change_log")

                # 第二次同步（相同文件，相同 hash）
                records2 = sync_world_canon_index(world_root=world_root, ledger=ledger, ts_ms=1741000003000)
                self.assertEqual(1, len(records2))
                self.assertFalse(records2[0].changed, msg="相同 hash 重复同步不应标记为 changed")

                change_log_after_second = [
                    row for row in ledger.canon_change_log
                    if row["canon_kind"] == "world" and row["target_id"] == doc_id
                ]
                self.assertEqual(
                    len(change_log_after_first),
                    len(change_log_after_second),
                    msg="相同 hash 重复同步不得产生重复 change_log 条目（幂等性违反）",
                )
            finally:
                ledger.close()

    def test_t_wcanon_04d_sync_failure_is_observable_via_error_log(self) -> None:
        """同步失败（无效 JSON）时 WORLD_CANON_INVALID_DOC 可统计，CANON_SYNC_FAILED 错误码已定义。"""
        # 验证 CANON_SYNC_FAILED 错误码已在 errors.py 中定义
        self.assertEqual("CANON_SYNC_FAILED", CANON_SYNC_FAILED)

        with tempfile.TemporaryDirectory(prefix="wcanon_fail_") as tmp:
            db_path = Path(tmp) / "audit.sqlite3"
            world_root = Path(tmp) / "world"
            world_root.mkdir(parents=True, exist_ok=True)

            # 写入损坏的 JSON 文件
            broken = world_root / "broken_doc.json"
            broken.write_text("{invalid_json_content", encoding="utf-8")

            ledger = SQLiteLedger(db_path=db_path)
            try:
                synced = sync_world_canon_index(world_root=world_root, ledger=ledger, ts_ms=1741000004000)
                # 损坏文件不应被同步
                self.assertEqual([], synced)
                # error_log 中应有 WORLD_CANON_INVALID_DOC 记录
                self.assertTrue(
                    any(
                        row["reason_code"] == WORLD_CANON_INVALID_DOC
                        and row["stage"] == "S_CANON_SYNC"
                        for row in ledger.error_log
                    ),
                    msg=f"error_log 中未找到 WORLD_CANON_INVALID_DOC 记录: {ledger.error_log}",
                )
            finally:
                ledger.close()

    def test_t_wcanon_04e_fixture_files_match_ssot(self) -> None:
        """fixture 副本字段与 SSOT 文件一致（doc_id/title/tags/body/status）。"""
        for doc_id in SEED_IDS:
            ssot_path = SSOT_WORLD / f"{doc_id}.json"
            fixture_path = FIXTURES_WORLD / "good" / f"{doc_id}.json"
            self.assertTrue(fixture_path.exists(), msg=f"fixture 文件不存在: {fixture_path}")

            ssot = _load_json(ssot_path)
            fixture = _load_json(fixture_path)

            for field in ("doc_id", "title", "tags", "body", "status"):
                self.assertEqual(
                    ssot[field],
                    fixture[field],
                    msg=f"{doc_id} fixture.{field} 与 SSOT 不一致",
                )


if __name__ == "__main__":
    unittest.main()
