"""Unified Message Gateway 启动入口。"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("Natsume.edge.gateway.bootstrap")


@dataclass
class GatewayConfig:
    """Gateway 启动配置。"""

    host_url: str = "http://127.0.0.1:8000"
    hmac_key: str | None = None
    gateway_port: int = 19000
    enabled_platforms: tuple[str, ...] = ("wechat", "qq")

    wx_db_storage_dir: str = ""
    wx_decrypt_output_dir: str = ""
    wx_key_cache_path: str | None = None
    wx_decrypt_refresh_interval_s: int = 3600
    wx_self_wxid: str | None = None
    wx_watermark_file: str | None = None
    wx_poll_interval_s: float = 5.0

    qq_onebot_api_base: str | None = None
    qq_self_id: str | None = None

    feishu_app_id: str | None = None
    feishu_app_secret: str | None = None
    feishu_encrypt_key: str | None = None
    feishu_verification_token: str | None = None

    wecom_corp_id: str | None = None
    wecom_corp_secret: str | None = None
    wecom_agent_id: str | None = None
    wecom_token: str | None = None
    wecom_encoding_aes_key: str | None = None


class GatewayInstance:
    """Gateway 运行实例，持有可停止的组件。"""

    def __init__(self) -> None:
        self.watcher = None
        self.refresher = None
        self.reporter = None
        self.server_thread: threading.Thread | None = None
        self._shutdown = threading.Event()

    def stop(self) -> None:
        self._shutdown.set()
        if self.watcher is not None:
            try:
                self.watcher.stop()
            except Exception as exc:
                logger.warning("Gateway: 停止 watcher 异常: %s", exc)
        if self.refresher is not None:
            try:
                self.refresher.stop()
            except Exception as exc:
                logger.warning("Gateway: 停止 decrypt refresher 异常: %s", exc)
        if self.reporter is not None:
            try:
                self.reporter.stop_retry_loop()
            except Exception as exc:
                logger.warning("Gateway: 停止 reporter retry loop 异常: %s", exc)
        logger.info("Gateway: 已停止")


def _normalize_platforms(raw: tuple[str, ...]) -> set[str]:
    normalized: set[str] = set()
    for item in raw:
        value = str(item or "").strip().lower()
        if value in {"wechat", "qq", "feishu", "wecom"}:
            normalized.add(value)
    if not normalized:
        normalized.add("wechat")
    return normalized


def start_gateway(config: GatewayConfig) -> GatewayInstance:
    """组装并启动 Unified Message Gateway。"""

    from edge_client.channels.input_reporter import EdgeInputReporter
    from edge_client.gateway.models import Platform
    from edge_client.gateway.normalizer import MessageNormalizer
    from edge_client.gateway.outbound import OutboundDispatcher
    from edge_client.gateway.router import GatewayRouter
    from edge_client.gateway.server import create_gateway_app

    instance = GatewayInstance()
    enabled_platforms = _normalize_platforms(config.enabled_platforms)

    reporter = EdgeInputReporter(
        host_url=config.host_url,
        hmac_key=config.hmac_key,
    )
    reporter.start_retry_loop()
    instance.reporter = reporter

    normalizer = MessageNormalizer()
    router = GatewayRouter(reporter)
    outbound = OutboundDispatcher()
    adapters: dict[str, Any] = {}

    if "wechat" in enabled_platforms:
        if not config.wx_decrypt_output_dir:
            logger.warning("Gateway: wechat 已启用但缺少 WECHAT_DECRYPT_OUTPUT_DIR，跳过注册")
        else:
            from edge_client.gateway.adapters.wechat_adapter import WeChatAdapter
            from edge_client.services.wx_db_service import WxDbService

            wx_svc = WxDbService(
                config.wx_decrypt_output_dir,
                poll_watermark_file=config.wx_watermark_file,
            )
            wx_adapter = WeChatAdapter(wx_svc, self_wxid=config.wx_self_wxid)
            outbound.register(wx_adapter)
            adapters[Platform.WECHAT.value] = wx_adapter

            if config.wx_db_storage_dir:
                from edge_client.services.wx_decrypt_refresher import WxDecryptRefresher

                refresher = WxDecryptRefresher(
                    db_storage_dir=config.wx_db_storage_dir,
                    decrypt_output_dir=config.wx_decrypt_output_dir,
                    key_cache_path=config.wx_key_cache_path,
                    refresh_interval_s=config.wx_decrypt_refresh_interval_s,
                )
                mode = refresher.start()
                instance.refresher = refresher
                logger.info("Gateway: 微信解密刷新已启动 mode=%s", mode)
            else:
                logger.warning("Gateway: 未配置 WECHAT_DB_STORAGE_DIR，跳过自动 refresh")

            from edge_client.services.wx_watcher import WxDbWatcher

            def _on_new_messages(msgs: list[Any]) -> None:
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
            watch_mode = watcher.start()
            instance.watcher = watcher
            logger.info("Gateway: 微信监听已启动 mode=%s", watch_mode)

    if "qq" in enabled_platforms:
        if config.qq_onebot_api_base:
            from edge_client.gateway.adapters.qq_adapter import QQAdapter

            qq_adapter = QQAdapter(
                config.qq_onebot_api_base,
                self_id=config.qq_self_id,
            )
            outbound.register(qq_adapter)
            adapters[Platform.QQ.value] = qq_adapter
        else:
            logger.warning("Gateway: qq 已启用但缺少 QQ_ONEBOT_API_BASE，跳过注册")

    if "feishu" in enabled_platforms:
        if config.feishu_app_id and config.feishu_app_secret:
            from edge_client.gateway.adapters.feishu_adapter import FeishuAdapter

            feishu_adapter = FeishuAdapter(
                app_id=config.feishu_app_id,
                app_secret=config.feishu_app_secret,
                encrypt_key=config.feishu_encrypt_key,
                verification_token=config.feishu_verification_token,
            )
            outbound.register(feishu_adapter)
            adapters[Platform.FEISHU.value] = feishu_adapter
        else:
            logger.warning("Gateway: feishu 已启用但缺少 FEISHU_APP_ID/FEISHU_APP_SECRET，跳过注册")

    if "wecom" in enabled_platforms:
        if config.wecom_corp_id and config.wecom_corp_secret and config.wecom_agent_id:
            from edge_client.gateway.adapters.wecom_adapter import WeComAdapter

            wecom_adapter = WeComAdapter(
                corp_id=config.wecom_corp_id,
                corp_secret=config.wecom_corp_secret,
                agent_id=config.wecom_agent_id,
                token=config.wecom_token,
                encoding_aes_key=config.wecom_encoding_aes_key,
            )
            outbound.register(wecom_adapter)
            adapters[Platform.WECOM.value] = wecom_adapter
        else:
            logger.warning("Gateway: wecom 已启用但缺少 corp 配置，跳过注册")

    app = create_gateway_app(
        router=router,
        normalizer=normalizer,
        adapters=adapters,
        outbound=outbound,
    )

    def _run_server() -> None:
        try:
            import uvicorn

            uvicorn.run(app, host="0.0.0.0", port=config.gateway_port, log_level="info")
        except Exception as exc:
            logger.error("Gateway: server 启动失败: %s", exc)

    server_thread = threading.Thread(
        target=_run_server,
        name="gateway-server",
        daemon=True,
    )
    server_thread.start()
    instance.server_thread = server_thread
    logger.info(
        "Gateway: 已启动 port=%d platforms=%s",
        config.gateway_port,
        ",".join(sorted(enabled_platforms)),
    )
    return instance

