"""句子级分片检测器：将 LLM 流式 chunk 按句子边界切分，供 TTS 流水线使用。

支持日语（。！？…」）、中文（。！？…」）标点作为句子边界。
"""

from __future__ import annotations

# 句子边界标点集合（日语 + 中文）
_SENTENCE_END_CHARS = frozenset("。！？…\n」）")


class SentenceSplitter:
    """将 LLM 逐 chunk 输出按句子边界切分。

    用法示例::

        splitter = SentenceSplitter()
        async for chunk in astream_llm_chunks(...):
            for sentence in splitter.feed(chunk):
                # 每完成一句，立即推送给 TTS
                yield sentence
        # 生成结束后，flush 剩余未成句文本
        remainder = splitter.flush()
        if remainder:
            yield remainder

    设计原则：
    - 无外部依赖，纯 Python 实现，零延迟。
    - 边界字符本身保留在句子末尾（不丢弃标点）。
    - 连续边界字符（如「！！」）合并为同一句末尾，不产生空句。
    """

    def __init__(self) -> None:
        self._buffer: str = ""

    def feed(self, chunk: str) -> list[str]:
        """输入一个 chunk，返回已完整的句子列表（可能为空列表）。

        Args:
            chunk: LLM 返回的文本片段。

        Returns:
            已完整的句子列表。每个句子末尾保留边界标点。
            若 chunk 中没有句子边界，返回空列表。
        """
        if not chunk:
            return []

        self._buffer += chunk
        sentences: list[str] = []
        start = 0

        i = 0
        while i < len(self._buffer):
            ch = self._buffer[i]
            if ch in _SENTENCE_END_CHARS:
                # 消费连续的边界字符（如「！！」「。。」）
                j = i + 1
                while j < len(self._buffer) and self._buffer[j] in _SENTENCE_END_CHARS:
                    j += 1
                sentence = self._buffer[start:j].strip()
                if sentence:
                    sentences.append(sentence)
                start = j
                i = j
            else:
                i += 1

        # 剩余未成句部分留在 buffer
        self._buffer = self._buffer[start:]
        return sentences

    def flush(self) -> str:
        """返回 buffer 中剩余的未成句文本，并清空 buffer。

        在 LLM 生成结束后调用，确保最后一段文本不被丢弃。

        Returns:
            剩余文本（可能为空字符串）。
        """
        remainder = self._buffer.strip()
        self._buffer = ""
        return remainder

    @property
    def buffer(self) -> str:
        """当前未成句缓冲区内容（只读，用于调试）。"""
        return self._buffer
