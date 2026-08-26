"""微信语音消息处理器（.silk 格式）。

微信语音消息使用腾讯私有 silk 编码格式（.silk / .amr）。
本模块提供：
  - 接收：.silk 解码 → PCM → 扬声器播放（✅ 可用）
  - 发送：TTS PCM → .silk 编码；实际投递由微信命令队列和 wxautox4 bridge 执行

依赖：
  pip install pysilk-mod pyaudio

注意：
  微信语音/视频通话走 TRTC 私有 P2P 协议，与本模块无关，不纳入范围。
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger("Natsume.edge.gateway.media.voice_handler")


# ---------------------------------------------------------------------------
# 接收：.silk 解码播放
# ---------------------------------------------------------------------------

def play_silk_voice(silk_path: str | Path) -> bool:
    """解码微信语音消息（.silk）并通过扬声器播放。

    Args:
        silk_path: .silk 文件的本地路径（来自 WxMessage.media_url，type=34）

    Returns:
        True 表示播放成功
    """
    silk_path = Path(silk_path)
    if not silk_path.exists():
        logger.warning("VoiceHandler: .silk 文件不存在: %s", silk_path)
        return False

    try:
        import pysilk
    except ImportError:
        logger.error("VoiceHandler: pysilk-mod 未安装，请执行: pip install pysilk-mod")
        return False

    try:
        import pyaudio
    except ImportError:
        logger.error("VoiceHandler: pyaudio 未安装，请执行: pip install pyaudio")
        return False

    try:
        # 1. 读取 .silk 文件
        with open(silk_path, "rb") as f:
            silk_data = f.read()

        # 2. pysilk 解码 .silk → PCM（16kHz, 16bit, mono）
        pcm_data: bytes = pysilk.decode(silk_data, to_wav=False)

        # 3. 通过 pyaudio 播放 PCM
        pa = pyaudio.PyAudio()
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=16000,
            output=True,
        )
        stream.write(pcm_data)
        stream.stop_stream()
        stream.close()
        pa.terminate()

        logger.debug("VoiceHandler: 播放完成 %s", silk_path.name)
        return True

    except Exception as exc:
        logger.error("VoiceHandler: 播放失败 %s: %s", silk_path, exc)
        return False


def decode_silk_to_pcm(silk_path: str | Path) -> bytes | None:
    """将 .silk 文件解码为 PCM bytes（不播放，供 ASR 等处理使用）。

    Args:
        silk_path: .silk 文件路径

    Returns:
        PCM bytes（16kHz, 16bit, mono），失败返回 None
    """
    try:
        import pysilk
    except ImportError:
        logger.error("VoiceHandler: pysilk-mod 未安装")
        return None

    try:
        with open(silk_path, "rb") as f:
            silk_data = f.read()
        return pysilk.decode(silk_data, to_wav=False)
    except Exception as exc:
        logger.error("VoiceHandler: silk 解码失败 %s: %s", silk_path, exc)
        return None


# ---------------------------------------------------------------------------
# 发送：PCM → .silk 编码 + 剪贴板文件写入
# ---------------------------------------------------------------------------

def encode_pcm_to_silk(pcm_bytes: bytes, *, sample_rate: int = 16000) -> bytes | None:
    # 编码失败必须留在媒体边界；调用者据 None 终止语音出站，避免把 PCM
    # 原始字节误当作可被微信桥识别的文件内容。
    """将 PCM 音频编码为 .silk 格式（用于微信语音消息发送）。

    Args:
        pcm_bytes: PCM 音频数据（16bit, mono）
        sample_rate: 采样率（默认 16000Hz）

    Returns:
        .silk 编码后的 bytes，失败返回 None
    """
    try:
        import pysilk
    except ImportError:
        logger.error("VoiceHandler: pysilk-mod 未安装，请执行: pip install pysilk-mod")
        return None

    try:
        silk_data: bytes = pysilk.encode(pcm_bytes, sample_rate=sample_rate)
        return silk_data
    except Exception as exc:
        logger.error("VoiceHandler: silk 编码失败: %s", exc)
        return None


def save_silk_file(silk_data: bytes, output_path: str | Path | None = None) -> Path | None:
    # 未给路径时使用系统唯一临时名，避免并发 TTS 请求争抢同一个 .silk 文件；
    # 文件的后续清理由命令消费者/调用链负责，本函数只承担落盘结果。
    """将 .silk 数据保存到文件。

    Args:
        silk_data: .silk 编码数据
        output_path: 输出路径，None 时创建临时文件

    Returns:
        保存的文件路径，失败返回 None
    """
    try:
        if output_path is None:
            fd, tmp_path = tempfile.mkstemp(suffix=".silk")
            os.close(fd)
            output_path = Path(tmp_path)
        else:
            output_path = Path(output_path)

        output_path.write_bytes(silk_data)
        return output_path
    except Exception as exc:
        logger.error("VoiceHandler: 保存 .silk 文件失败: %s", exc)
        return None
