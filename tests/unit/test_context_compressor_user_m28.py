"""M28-I：ContextCompressor 保存用户轮次测试（T-COMPRESS-USER-01~02）"""

from __future__ import annotations

import pytest
from host_brain.memory.context_compressor import ContextCompressor


class TestContextCompressorUser:
    """T-COMPRESS-USER-01~02：ContextCompressor 双声道记忆测试"""

    def _make_compressor(self, saved_records: list) -> ContextCompressor:
        """创建带 mock episodic_writer 的 ContextCompressor"""
        def mock_writer(content: str, refs: list, **kwargs):
            saved_records.append({"content": content, "refs": refs, **kwargs})

        return ContextCompressor(
            max_tokens=1000,
            episodic_writer=mock_writer,
            pre_compress_save_enabled=True,
        )

    def test_user_turns_are_saved(self):
        """T-COMPRESS-USER-01：压缩后的记录包含 user 轮次"""
        saved = []
        compressor = self._make_compressor(saved)
        turns = [
            {"role": "user", "content": "你好，我今天很开心"},
            {"role": "assistant", "content": "很高兴听到你开心！"},
        ]
        compressor._pre_compress_save(turns, strategy="sliding", session_id="s1")
        roles = [r.get("refs", [""])[0] for r in saved]
        # 验证 user 轮次被保存（refs 中包含 /user/）
        assert any("/user/" in ref for ref in roles)

    def test_user_turn_with_emotion_tag_is_saved(self):
        """T-COMPRESS-USER-02：user 轮次含 emotion_tag 字段时，被传递给 episodic_writer"""
        saved = []
        compressor = self._make_compressor(saved)
        turns = [
            {"role": "user", "content": "我今天很开心", "emotion_tag": "HAPPY"},
            {"role": "assistant", "content": "太好了！"},
        ]
        compressor._pre_compress_save(turns, strategy="sliding", session_id="s1")
        # 找到 user 轮次的记录
        user_records = [r for r in saved if "emotion_tag" in r]
        assert len(user_records) >= 1
        assert user_records[0]["emotion_tag"] == "HAPPY"

    def test_both_roles_saved(self):
        """user 和 assistant 轮次都被保存"""
        saved = []
        compressor = self._make_compressor(saved)
        turns = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
        ]
        compressor._pre_compress_save(turns, strategy="sliding", session_id="s1")
        assert len(saved) == 2
