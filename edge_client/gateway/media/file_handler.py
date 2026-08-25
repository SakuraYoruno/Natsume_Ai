"""文件消息处理器。

提供微信文件消息的接收（本地路径读取）和发送（剪贴板粘贴文件）功能。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("Natsume.edge.gateway.media.file_handler")


def get_file_info(file_path: str | Path) -> dict | None:
    # 这是附件引用的轻量探测，不打开或解析文件内容；后续读取仍应在具备相应
    # 授权、扫描和大小限制的媒体管道中完成。
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
    # 先解析为绝对存在路径，再写入 CF_HDROP；这只准备桌面粘贴所需的引用，
    # 不等同平台已发送文件。失败必须保留给调用方的持久投递状态机。
    """将文件路径写入剪贴板（CF_HDROP 格式，用于微信发送文件）。

    Args:
        file_path: 文件本地路径

    Returns:
        True 表示成功
    """
    try:
        import struct
        import win32clipboard
        import win32con

        file_path = Path(file_path).resolve()
        if not file_path.exists():
            logger.warning("FileHandler: 文件不存在: %s", file_path)
            return False

        # DROPFILES 结构：20 字节头 + null-terminated UTF-16LE 路径 + 双 null 终止符。
        header = struct.pack("IiiII", 20, 0, 0, 0, 1)
        wide_path = str(file_path).encode("utf-16-le") + b"\x00\x00"
        data = header + wide_path + b"\x00\x00"

        clipboard_open = False
        win32clipboard.OpenClipboard()
        clipboard_open = True
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_HDROP, data)
        finally:
            if clipboard_open:
                win32clipboard.CloseClipboard()
        return True
    except ImportError:
        logger.error("FileHandler: 需要安装 pywin32: pip install pywin32")
        return False
    except Exception as exc:
        logger.error("FileHandler: 设置文件剪贴板失败: %s", exc)
        return False
