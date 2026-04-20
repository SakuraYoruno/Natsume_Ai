"""Gateway 视频附件轻量处理器。"""

from __future__ import annotations

import logging
from pathlib import Path


logger = logging.getLogger("Natsume.edge.gateway.media.video_handler")


def get_video_info(video_path: str | Path) -> dict | None:
    """返回视频基础元数据。

    当前版本只做轻量探测；复杂抽帧和音轨处理交给 host 侧异步观察层。
    """
    path = Path(video_path)
    if not path.exists():
        logger.warning("VideoHandler: 视频不存在: %s", path)
        return None
    try:
        stat = path.stat()
        return {
            "name": path.name,
            "size": stat.st_size,
            "path": str(path),
        }
    except Exception as exc:
        logger.error("VideoHandler: 获取视频信息失败 %s: %s", path, exc)
        return None


__all__ = ["get_video_info"]
