"""文件消息处理器。

提供微信文件消息的接收（本地路径读取）和发送（剪贴板粘贴文件）功能。
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path

logger = logging.getLogger("Natsume.edge.gateway.media.file_handler")


def get_file_info(file_path: str | Path) -> dict | None:
    """获取文件基本信息。

    Args:
        file_path: 文件本地路径（来自 WxMessage.media_url，type=49 中的文件类型）

    Returns:
        {"name": str, "size": int, "path": str}，失败返回 None
    """
    file_path = Path(file_path)
    if not file_path.exists():
        logger.warning("FileHandler: 文件不存在: %s", file_path)
        return None
    try:
        return {
            "name": file_path.name,
            "size": file_path.stat().st_size,
            "path": str(file_path),
        }
    except Exception as exc:
        logger.error("FileHandler: 获取文件信息失败 %s: %s", file_path, exc)
        return None


def set_clipboard_file(file_path: str | Path) -> bool:
    """将文件路径写入剪贴板（CF_HDROP 格式，用于微信发送文件）。

    Args:
        file_path: 文件本地路径

    Returns:
        True 表示成功
    """
    try:
        import win32clipboard
        import win32con

        file_path = Path(file_path).resolve()
        if not file_path.exists():
            logger.warning("FileHandler: 文件不存在: %s", file_path)
            return False

        # 构造 DROPFILES 结构（CF_HDROP 格式）
        path_str = str(file_path) + "\x00\x00"
        path_bytes = path_str.encode("utf-16-le")
        # DROPFILES header: pFiles(4) + pt.x(4) + pt.y(4) + fNC(4) + fWide(4) = 20 bytes
        header = struct.pack("IIIII", 20, 0, 0, 0, 1)
        data = header + path_bytes

        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_HDROP, data)
        win32clipboard.CloseClipboard()
        return True
    except ImportError:
        logger.error("FileHandler: 需要安装 pywin32: pip install pywin32")
        return False
    except Exception as exc:
        logger.error("FileHandler: 设置文件剪贴板失败: %s", exc)
        return False
