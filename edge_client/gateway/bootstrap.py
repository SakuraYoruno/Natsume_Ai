"""Unified Message Gateway — 启动入口。

将 Gateway 各组件组装并启动：
  1. WxDbService（解密数据库查询层）
  2. WxDbWatcher（文件监听 / 定时轮询）
  3. WeChatAdapter（微信适配器）
  4. MessageNormalizer（去重 + 脱敏）
  5. GatewayRouter（路由到 Host Brain）
  6. OutboundDispatcher（回复分发）
  7. FastAPI Gateway Server（:19000）

使用方式：
  from edge_client.gateway.bootstrap import start_gateway
  gateway = start_gateway(config)
  # ... 运行中 ...
  gateway.stop()
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("Natsume.edge.gateway.bootstrap")


@dataclass
class GatewayConfig:
    """Gateway 启动配置。"""
    # Host Brain 地址（用于路由消息）
    host_url: str = "http://127.0.0.1:8000"
    # HMAC 签名密钥（复用 EdgeConfig.hmac_shared_key）
    hmac_key: str | None = None
    # 微信解密输出目录
    wx_decrypt_output_dir: str = ""
    # 本机微信 wxid（用于过滤自己发出的消息）
    wx_self_wxid: str | None = None
    # 水位线持久化文件路径
    wx_watermark_file: str | None = None
    # watchdog 不可用时的降级轮询间隔（秒）
    wx_poll_interval_s: float = 5.0
    # Gateway 监听端口
    gateway_port: int = 19000
    # 是否启用微信适配器
    wx_enabled: bool = True


class GatewayInstance:
    """Gateway 运行实例，持有所有组件引用，支持优雅停止。"""

    def __init__(self) -> None:
        self.watcher = None
        self.server_thread: threading.Thread | None = None
        self._shutdown = threading.Event()

    def stop(self) -> None:
        """停止 Gateway 所有组件。"""
        self._shutdown.set()
        if self.watcher is not None:
            try:
                self.watcher.stop()
            except Exception as exc:
                logger.warning("Gateway: 停止 watcher 异常: %s", exc)
        logger.info("Gateway: 已停止")


def start_gateway(config: GatewayConfig) -> GatewayInstance:
    """组装并启动 Unified Message Gateway。

    Args:
        config: GatewayConfig 配置

    Returns:
        GatewayInstance（可调用 .stop() 优雅停止）
    """
    from edge_client.channels.input_reporter import EdgeInputReporter
    from edge_client.gateway.models import Platform
    from edge_client.gateway.normalizer import MessageNormalizer
    from edge_client.gateway.outbound import OutboundDispatcher
    from edge_client.gateway.router import GatewayRouter
    from edge_client.gateway.server import create_gateway_app

    instance = GatewayInstance()

    # 1. EdgeInputReporter（复用现有通道）
    reporter = EdgeInputReporter(
        host_url=config.host_url,
        hmac_key=config.hmac_key,
    )
    reporter.start_retry_loop()

    # 2. 核心组件
    normalizer = MessageNormalizer()
    router = GatewayRouter(reporter)
    outbound = OutboundDispatcher()
    adapters: dict[str, Any] = {}

    # 3. 微信适配器
    if config.wx_enabled and config.wx_decrypt_output_dir:
        from edge_client.services.wx_db_service import WxDbService
        from edge_client.services.wx_watcher import WxDbWatcher
        from edge_client.gateway.adapters.wechat_adapter import WeChatAdapter

        wx_svc = WxDbService(
            config.wx_decrypt_output_dir,
            poll_watermark_file=config.wx_watermark_file,
        )
        wx_adapter = WeChatAdapter(wx_svc, self_wxid=config.wx_self_wxid)
        outbound.register(wx_adapter)
        adapters[Platform.WECHAT.value] = wx_adapter

        # 4. 文件监听器（新消息 → 标准化 → 路由）
        def _on_new_messages(msgs):
            for wx_msg in msgs:
                unified = wx_adapter.convert_wx_message(wx_msg)
                if unified is None:
                    continue
                normalized = normalizer.normalize(unified)
                if normalized is None:
                    continue
                router.route(normalized)

        watcher = WxDbWatcher(
            wx_svc,
            _on_new_messages,
            poll_interval_s=config.wx_poll_interval_s,
        )
        mode = watcher.start()
        instance.watcher = watcher
        logger.info("Gateway: 微信监听已启动（模式: %s）", mode)
    else:
        logger.info("Gateway: 微信适配器未启用（wx_enabled=False 或未配置解密目录）")

    # 5. FastAPI Gateway Server
    app = create_gateway_app(
        router=router,
        normalizer=normalizer,
        adapters=adapters,
        outbound=outbound,
    )

    def _run_server():
        try:
            import uvicorn
            uvicorn.run(app, host="0.0.0.0", port=config.gateway_port, log_level="info")
        except Exception as exc:
            logger.error("Gateway: Server 启动失败: %s", exc)

    server_thread = threading.Thread(
        target=_run_server,
        name="gateway-server",
        daemon=True,
    )
    server_thread.start()
    instance.server_thread = server_thread

    logger.info("Gateway: 已启动，监听端口 %d", config.gateway_port)
    return instance
