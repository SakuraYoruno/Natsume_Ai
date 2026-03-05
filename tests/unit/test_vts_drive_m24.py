"""VTS 表情驱动完整链路测试（TASK-0214 M24-B）。

测试编号：T-VTS-DRIVE-01~14
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from host_brain.channels.vts_mapping_store import VTSMappingStore, DEFAULT_EMOTION_MAPPING
from host_brain.channels.vts_rate_limiter import VTSRateLimiter
from host_brain.core.errors import (
    AVATAR_INVALID_EMOTION_TAG,
    VTS_MAPPING_LOAD_FAILED,
    VTS_RATE_LIMIT_EXCEEDED,
)


# ---------------------------------------------------------------------------
# T-VTS-DRIVE-01~05: 映射表持久化与版本化
# ---------------------------------------------------------------------------

class TestVTSMappingStorePersistence:
    """T-VTS-DRIVE-01~05: 映射表持久化与版本化。"""

    def test_t_vts_drive_01_version_updated_on_save(self):
        """T-VTS-DRIVE-01：映射表变更后版本号更新，vts_mapping_change_log 可追溯。"""
        store = VTSMappingStore()
        # 保存新版本
        new_mapping = {**DEFAULT_EMOTION_MAPPING, "EXCITED": "exp_excited_01"}
        store.save_mapping(new_mapping, version="v1.1.0", changed_by="test")

        versions = store.list_versions()
        assert len(versions) >= 2, "应有至少 2 个版本（初始 v1.0.0 + v1.1.0）"
        latest = versions[0]
        assert latest["version"] == "v1.1.0"
        assert latest["changed_by"] == "test"
        assert latest["new_hash"] is not None
        store.close()

    def test_t_vts_drive_02_invalid_emotion_tag_rejected(self):
        """T-VTS-DRIVE-02：非法 emotion_tag 被拒绝（AVATAR_INVALID_EMOTION_TAG）。"""
        store = VTSMappingStore()
        with pytest.raises(ValueError) as exc_info:
            store.get_expression_id("UNKNOWN_TAG_XYZ")
        assert AVATAR_INVALID_EMOTION_TAG in str(exc_info.value)
        store.close()

    def test_t_vts_drive_02b_empty_emotion_tag_rejected(self):
        """T-VTS-DRIVE-02b：空 emotion_tag 被拒绝。"""
        store = VTSMappingStore()
        with pytest.raises(ValueError) as exc_info:
            store.get_expression_id("")
        assert AVATAR_INVALID_EMOTION_TAG in str(exc_info.value)
        store.close()

    def test_t_vts_drive_03_mapping_load_failure_raises(self):
        """T-VTS-DRIVE-03：映射表加载失败时抛出 RuntimeError（VTS_MAPPING_LOAD_FAILED）。"""
        store = VTSMappingStore()
        with pytest.raises(RuntimeError) as exc_info:
            store.load_mapping(version="v999.0.0")
        assert VTS_MAPPING_LOAD_FAILED in str(exc_info.value)
        store.close()

    def test_t_vts_drive_04_same_version_same_output(self):
        """T-VTS-DRIVE-04：映射表版本化：同版本同输入输出一致。"""
        store = VTSMappingStore()
        exp1 = store.get_expression_id("HAPPY")
        exp2 = store.get_expression_id("HAPPY")
        assert exp1 == exp2, "同版本同输入应输出一致"
        store.close()

    def test_t_vts_drive_05_persistence_survives_reload(self):
        """T-VTS-DRIVE-05：映射表持久化：重新加载后可恢复。"""
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            # 写入
            store1 = VTSMappingStore(db_path=db_path)
            store1.save_mapping(
                {"CUSTOM": "exp_custom_01"},
                version="v2.0.0",
                changed_by="test",
            )
            store1.close()

            # 重新加载
            store2 = VTSMappingStore(db_path=db_path)
            version = store2.load_mapping(version="v2.0.0")
            assert version.mapping.get("CUSTOM") == "exp_custom_01"
            store2.close()
        finally:
            os.unlink(db_path)


# ---------------------------------------------------------------------------
# T-VTS-DRIVE-06~10: VTS RPC 调用链路与审计串联
# ---------------------------------------------------------------------------

class TestVTSRPCCallAndAudit:
    """T-VTS-DRIVE-06~10: VTS RPC 调用链路与审计串联。"""

    def test_t_vts_drive_06_vts_action_log_complete(self):
        """T-VTS-DRIVE-06：VTS 调用成功，vts_action_log 记录完整（expression_id/status/request_id）。"""
        from host_brain.channels.vts import resolve_vts_expression, STATUS_APPLIED

        mock_ledger = MagicMock()
        mock_ledger.log_avatar_action = MagicMock()

        result = resolve_vts_expression(
            emotion_tag="HAPPY",
            ledger=mock_ledger,
            request_id="req_vts_06",
            edge_available=True,
        )
        assert result.status == STATUS_APPLIED
        assert result.expression_id == "exp_happy_01"
        assert result.request_id == "req_vts_06"
        # 审计日志被调用
        assert mock_ledger.log_avatar_action.called
        call_args = mock_ledger.log_avatar_action.call_args[0][0]
        assert call_args["expression_id"] == "exp_happy_01"
        assert call_args["request_id"] == "req_vts_06"

    def test_t_vts_drive_07_edge_unavailable_degraded_not_fake(self):
        """T-VTS-DRIVE-07：VTS 不可用时降级，reason_code=VTS_DEGRADED_FALLBACK，不伪造。"""
        from host_brain.channels.vts import resolve_vts_expression, STATUS_DEGRADED
        from host_brain.core.errors import VTS_DEGRADED_FALLBACK

        mock_ledger = MagicMock()
        mock_ledger.log_avatar_action = MagicMock()

        result = resolve_vts_expression(
            emotion_tag="HAPPY",
            ledger=mock_ledger,
            request_id="req_vts_07",
            edge_available=False,
        )
        assert result.status == STATUS_DEGRADED
        assert result.reason_code == VTS_DEGRADED_FALLBACK
        # 降级到 neutral，不伪造"表情已触发"
        assert result.expression_id == "exp_neutral_01"

    def test_t_vts_drive_08_evidence_ref_in_audit(self):
        """T-VTS-DRIVE-08：每次触发有 evidence_ref 指向 vts_action_log（request_id 串联）。"""
        from host_brain.channels.vts import resolve_vts_expression

        mock_ledger = MagicMock()
        mock_ledger.log_avatar_action = MagicMock()

        result = resolve_vts_expression(
            emotion_tag="NEUTRAL",
            ledger=mock_ledger,
            request_id="req_vts_08",
            edge_available=True,
        )
        # request_id 串联到审计日志
        call_args = mock_ledger.log_avatar_action.call_args[0][0]
        assert call_args["request_id"] == "req_vts_08", "审计日志应串联 request_id"

    def test_t_vts_drive_09_unknown_tag_degraded_to_neutral(self):
        """T-VTS-DRIVE-09：未知 emotion_tag 降级到 neutral，结构稳定。"""
        from host_brain.channels.vts import resolve_vts_expression, STATUS_DEGRADED

        mock_ledger = MagicMock()
        mock_ledger.log_avatar_action = MagicMock()

        result = resolve_vts_expression(
            emotion_tag="UNKNOWN_EMOTION",
            ledger=mock_ledger,
            request_id="req_vts_09",
            edge_available=True,
        )
        assert result.status == STATUS_DEGRADED
        assert result.expression_id == "exp_neutral_01"

    def test_t_vts_drive_10_audit_log_contains_intent_id(self):
        """T-VTS-DRIVE-10：vts_action_log 串联 request_id（intent_id 通过 request_id 关联）。"""
        from host_brain.channels.vts import resolve_vts_expression

        mock_ledger = MagicMock()
        mock_ledger.log_avatar_action = MagicMock()

        resolve_vts_expression(
            emotion_tag="SAD",
            ledger=mock_ledger,
            request_id="req_vts_10",
            edge_available=True,
        )
        call_args = mock_ledger.log_avatar_action.call_args[0][0]
        assert "request_id" in call_args
        assert call_args["request_id"] == "req_vts_10"


# ---------------------------------------------------------------------------
# T-VTS-DRIVE-11~14: 频率限制
# ---------------------------------------------------------------------------

class TestVTSRateLimiter:
    """T-VTS-DRIVE-11~14: VTS 频率限制。"""

    def test_t_vts_drive_11_rate_limit_exceeded_logged(self):
        """T-VTS-DRIVE-11：超限时触发拒绝，reason_code=VTS_RATE_LIMIT_EXCEEDED。"""
        limiter = VTSRateLimiter(max_per_second=1.0, burst_size=1.0)
        # 第一次允许
        result1 = limiter.check()
        assert result1.allowed is True
        # 立即第二次超限
        result2 = limiter.check()
        assert result2.allowed is False
        assert result2.reason_code == VTS_RATE_LIMIT_EXCEEDED

    def test_t_vts_drive_12_hot_update_rate_takes_effect(self):
        """T-VTS-DRIVE-12：频率配置热更新后立即生效。"""
        limiter = VTSRateLimiter(max_per_second=1.0, burst_size=1.0)
        # 消耗令牌
        limiter.check()
        # 热更新为更高频率
        limiter.update_rate(max_per_second=100.0, burst_size=100.0)
        # 热更新后应立即允许（burst_size 增大）
        result = limiter.check()
        assert result.allowed is True, "热更新后应立即允许"

    def test_t_vts_drive_13_fifo_drop_oldest(self):
        """T-VTS-DRIVE-13：令牌桶耗尽时拒绝最新请求（FIFO 策略）。"""
        limiter = VTSRateLimiter(max_per_second=2.0, burst_size=2.0)
        # 消耗所有令牌
        r1 = limiter.check()
        r2 = limiter.check()
        assert r1.allowed is True
        assert r2.allowed is True
        # 第三次超限
        r3 = limiter.check()
        assert r3.allowed is False
        assert r3.reason_code == VTS_RATE_LIMIT_EXCEEDED

    def test_t_vts_drive_14_rate_limit_log_written(self):
        """T-VTS-DRIVE-14：超限时写 vts_rate_limit_log（通过 ledger.log_avatar_action）。"""
        mock_ledger = MagicMock()
        mock_ledger.log_avatar_action = MagicMock()

        limiter = VTSRateLimiter(max_per_second=1.0, burst_size=1.0)
        limiter.check()  # 消耗令牌
        result = limiter.check()  # 超限
        assert result.allowed is False

        # 写入审计日志
        limiter.log_rate_limit_event(
            ledger=mock_ledger,
            request_id="req_vts_14",
            emotion_tag="HAPPY",
        )
        assert mock_ledger.log_avatar_action.called
        call_args = mock_ledger.log_avatar_action.call_args[0][0]
        assert call_args["reason_code"] == VTS_RATE_LIMIT_EXCEEDED
        assert call_args["emotion_tag"] == "HAPPY"

    def test_t_vts_drive_11b_tokens_refill_over_time(self):
        """T-VTS-DRIVE-11b：令牌随时间补充（等待后可再次触发）。"""
        limiter = VTSRateLimiter(max_per_second=10.0, burst_size=1.0)
        limiter.check()  # 消耗令牌
        time.sleep(0.15)  # 等待 150ms，应补充约 1.5 个令牌
        result = limiter.check()
        assert result.allowed is True, "等待后令牌应补充，允许触发"
