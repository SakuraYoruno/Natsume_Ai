"""
WeChatFerry (wcferry) 桥接脚本：在 Edge 所在机器上运行，把微信收到的消息 POST 到 Edge 的 /v1/edge/wechat/callback。

前置条件：
  - Edge 机器已登录微信小号
  - 已安装 wcferry：pip install wcferry
  - Edge 已启动且 EDGE_WECHAT_LISTENER_ENABLED=1、EDGE_EVENT_REPORT_HOST_URL 已配置

用法（在 Edge 所在机器上）：
  python tools/wechat_wcferry_bridge.py
  python tools/wechat_wcferry_bridge.py --edge-url http://127.0.0.1:17800

注意：wcferry 会注入当前机器的微信进程，首次运行可能需以管理员权限启动微信或 wcferry。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.error
import urllib.request
from pathlib import Path
from threading import Thread

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("Natsume.wechat_wcferry_bridge")

DEFAULT_EDGE_URL = "http://127.0.0.1:17800"
CALLBACK_PATH = "/v1/edge/wechat/callback"
TIMEOUT_S = 10


def _wxmsg_to_body(msg, self_wxid: str) -> dict:
    """将 wcferry WxMsg 转为 Edge callback 期望的 JSON 字段。"""
    # WeChatListener 兼容: id/msg_id, sender/from_wxid, receiver/to_wxid, content/text, type, roomid, ts
    body = {
        "id": getattr(msg, "id", None) or "",
        "msg_id": getattr(msg, "id", None) or "",
        "sender": getattr(msg, "sender", "") or "",
        "from_wxid": getattr(msg, "sender", "") or "",
        "receiver": self_wxid,
        "to_wxid": self_wxid,
        "content": getattr(msg, "content", "") or "",
        "text": getattr(msg, "content", "") or "",
        "type": getattr(msg, "type", 1),
        "msg_type": str(getattr(msg, "type", 1)),
        "roomid": getattr(msg, "roomid", "") or "",
        "ts": getattr(msg, "ts", None),
    }
    # 去掉空字符串以便 listener 用默认逻辑
    return {k: v for k, v in body.items() if v != "" and v is not None}


def _post_to_edge(edge_url: str, body: dict) -> bool:
    url = f"{edge_url.rstrip('/')}{CALLBACK_PATH}"
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            if resp.status == 200:
                return True
            logger.warning("Edge callback returned status %s", resp.status)
            return False
    except urllib.error.HTTPError as e:
        logger.warning("Edge callback HTTP error: %s %s", e.code, e.reason)
        return False
    except Exception as e:
        logger.warning("Edge callback request failed: %s", e)
        return False


def _run_msg_loop(edge_url: str, self_wxid: str) -> None:
    try:
        from wcferry import Wcf
    except ImportError:
        logger.error("未安装 wcferry，请执行: pip install wcferry")
        sys.exit(1)

    wcf = Wcf(debug=False)
    self_wxid = self_wxid or (getattr(wcf, "self_wxid", None) or "")

    if not wcf.enable_receiving_msg(pyq=False):
        logger.error("enable_receiving_msg 失败")
        sys.exit(1)
    logger.info("wcferry 收消息已开启，self_wxid=%s，推送到 %s%s", self_wxid or "(未获取)", edge_url, CALLBACK_PATH)

    is_receiving = getattr(wcf, "is_receiving_msg", None)
    while (is_receiving() if callable(is_receiving) else True):
        try:
            msg = wcf.get_msg(block=True)
        except Exception as e:
            logger.debug("get_msg: %s", e)
            continue
        if msg is None:
            continue
        # 可选：只转发别人发来的文本，不转发自己发的
        if getattr(msg, "from_self", lambda: False)():
            continue
        if not getattr(msg, "is_text", lambda: True)():
            continue
        body = _wxmsg_to_body(msg, self_wxid)
        if not body.get("content") and not body.get("text"):
            continue
        ok = _post_to_edge(edge_url, body)
        if ok:
            logger.debug("已推送 msg_id=%s -> Edge", body.get("id") or body.get("msg_id"))
        else:
            logger.warning("推送失败 sender=%s content=%.40s", body.get("sender"), body.get("content", ""))


def main() -> int:
    parser = argparse.ArgumentParser(description="wcferry 收消息并 POST 到 Edge /v1/edge/wechat/callback")
    parser.add_argument(
        "--edge-url",
        default=DEFAULT_EDGE_URL,
        help="Edge 基地址 (default: %s)" % DEFAULT_EDGE_URL,
    )
    parser.add_argument(
        "--self-wxid",
        default="",
        help="本机登录的微信小号 wxid，不填则使用 wcf 自动获取",
    )
    args = parser.parse_args()

    logger.info("Edge URL: %s", args.edge_url)
    th = Thread(target=_run_msg_loop, args=(args.edge_url, args.self_wxid), daemon=False)
    th.start()
    th.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
