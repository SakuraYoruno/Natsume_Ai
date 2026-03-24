"""图片消息处理器。

提供微信图片消息的接收（本地路径读取）和发送（剪贴板粘贴）功能。
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

logger = logging.getLogger("Natsume.edge.gateway.media.image_handler")


def read_image_as_base64(image_path: str | Path) -> str | None:
    """读取图片文件并返回 base64 编码字符串。

    Args:
        image_path: 图片文件本地路径（来自 WxMessage.media_url，type=3）

    Returns:
        base64 编码字符串（不含 data URI 前缀），失败返回 None
    """
    image_path = Path(image_path)
    if not image_path.exists():
        logger.warning("ImageHandler: 图片文件不存在: %s", image_path)
        return None
    try:
        data = image_path.read_bytes()
        return base64.b64encode(data).decode("ascii")
    except Exception as exc:
        logger.error("ImageHandler: 读取图片失败 %s: %s", image_path, exc)
        return None


def set_clipboard_image(image_path: str | Path) -> bool:
    """将图片文件写入剪贴板（用于微信发送图片）。

    Args:
        image_path: 图片文件本地路径

    Returns:
        True 表示成功
    """
    try:
        from PIL import Image
        import io
        import win32clipboard
        import win32con

        image_path = Path(image_path)
        img = Image.open(image_path)
        # 转换为 BMP 格式写入剪贴板
        output = io.BytesIO()
        img.convert("RGB").save(output, "BMP")
        data = output.getvalue()[14:]  # 去掉 BMP 文件头

        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_DIB, data)
        win32clipboard.CloseClipboard()
        return True
    except ImportError:
        logger.error("ImageHandler: 需要安装 Pillow 和 pywin32: pip install Pillow pywin32")
        return False
    except Exception as exc:
        logger.error("ImageHandler: 设置图片剪贴板失败: %s", exc)
        return False
