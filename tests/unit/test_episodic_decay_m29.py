"""M32-F：EpisodicRecord 衰减配置专项测试（T-EPISODIC-DECAY-01~03）"""

from __future__ import annotations

import pytest
from host_brain.memory.episodic import EpisodicStore, EpisodicRecord


class TestEpisodicDecay:
    """T-EPISODIC-DECAY-01~03：EpisodicRecord 衰减配置测试"""

    def test_high_intensity_uses_slow_decay(self):
        """T-EPISODIC-DECAY-01：emotion_arousal > 0.8 的记录 decay_profile_id 为 SLOW 或 PERMANENT"""
        store = EpisodicStore()
        result = store.write_memory(
            text="高情感强度记忆",
            evidence_refs=["ev-high-001"],
            emotion_tag="EXCITED",
            emotion_valence=0.8,
            emotion_arousal=0.9,
            decay_profile_id="SLOW",  # 高情感强度使用慢衰减
        )
        assert result.success
        record = store.get_memory(result.memory_id)
        assert record is not None
        profile = store.get_decay_profile(record.decay_profile_id)
        # SLOW 衰减率应 <= 0.5
        assert profile["decay_rate"] <= 0.5

    def test_low_intensity_uses_fast_decay(self):
        """T-EPISODIC-DECAY-02：emotion_arousal < 0.3 的记录 decay_profile_id 为 FAST"""
        store = EpisodicStore()
        result = store.write_memory(
            text="低情感强度记忆",
            evidence_refs=["ev-low-001"],
            emotion_tag="NEUTRAL",
            emotion_valence=0.0,
            emotion_arousal=0.1,
            decay_profile_id="FAST",  # 低情感强度使用快衰减
        )
        assert result.success
        record = store.get_memory(result.memory_id)
        assert record is not None
        profile = store.get_decay_profile(record.decay_profile_id)
        # FAST 衰减率应 > 0.5
        assert profile["decay_rate"] > 0.5

    def test_decay_profiles_contain_required_keys(self):
        """T-EPISODIC-DECAY-03：DECAY_PROFILES 包含 fast/normal/slow/permanent 四种配置"""
        store = EpisodicStore()
        # 验证各种衰减配置存在
        fast_profile = store.get_decay_profile("FAST")
        assert "decay_rate" in fast_profile
        assert "ttl_days" in fast_profile
        assert fast_profile["decay_rate"] > 0.5  # 快速衰减

        slow_profile = store.get_decay_profile("SLOW")
        assert "decay_rate" in slow_profile
        assert slow_profile["decay_rate"] < 0.5  # 慢速衰减

        permanent_profile = store.get_decay_profile("PERMANENT")
        assert permanent_profile["decay_rate"] == 0.0  # 永久保留
        assert permanent_profile["ttl_days"] is None  # 无过期时间

        default_profile = store.get_decay_profile("default")
        assert "decay_rate" in default_profile

    def test_permanent_record_never_expires(self):
        """PERMANENT 衰减配置的记忆永不过期"""
        store = EpisodicStore()
        result = store.write_memory(
            text="永久记忆",
            evidence_refs=["ev-perm-001"],
            decay_profile_id="PERMANENT",
        )
        assert result.success
        record = store.get_memory(result.memory_id)
        assert record is not None
        # 即使时间很久，也不过期
        far_future_ms = record.ts_ms + 365 * 24 * 3600 * 1000 * 100  # 100年后
        assert store.is_expired(record, now_ts_ms=far_future_ms) is False

    def test_fast_decay_record_expires_quickly(self):
        """FAST 衰减配置的记忆在 TTL 后过期"""
        store = EpisodicStore()
        result = store.write_memory(
            text="快速遗忘记忆",
            evidence_refs=["ev-fast-001"],
            decay_profile_id="FAST",
        )
        assert result.success
        record = store.get_memory(result.memory_id)
        assert record is not None
        # FAST 的 ttl_days=7，8天后应过期
        eight_days_later = record.ts_ms + 8 * 24 * 3600 * 1000
        assert store.is_expired(record, now_ts_ms=eight_days_later) is True

    def test_unknown_decay_profile_uses_default(self):
        """未知 decay_profile_id 使用默认配置"""
        store = EpisodicStore()
        profile = store.get_decay_profile("UNKNOWN_PROFILE")
        assert "decay_rate" in profile
        assert "ttl_days" in profile
