"""Unified Message Gateway 启动入口。"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("Natsume.edge.gateway.bootstrap")


@dataclass
class GatewayConfig:
    """Gateway 启动配置。"""

    host_url: str = "http://127.0.0.1:8000"
    hmac_key: str | None = None
    gateway_port: int = 19000
    enabled_platforms: tuple[str, ...] = ("qq",)

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

    # Telegram Userbot (Telethon)
    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    telegram_phone: str | None = None
    telegram_session_name: str = "nanami"
    telegram_self_id: str | None = None
    telegram_typing_speed: float = 15.0
    telegram_min_delay_s: float = 1.0
    telegram_max_delay_s: float = 3.0
    telegram_proxy_type: str | None = None
    telegram_proxy_addr: str | None = None
    telegram_proxy_port: int | None = None

    # LINE Messaging API
    line_channel_access_token: str | None = None
    line_channel_secret: str | None = None


class GatewayInstance:
    """Gateway 运行实例，持有可停止的组件。"""

    def __init__(self) -> None:
        self.watcher = None
        self.refresher = None
        self.reporter = None
        self.server_thread: threading.Thread | None = None
        self.uia_bridge_process: subprocess.Popen | None = None
        self.uia_bridge_watchdog: threading.Thread | None = None
        self._shutdown = threading.Event()

    def stop(self) -> None:
        self._shutdown.set()
        if self.uia_bridge_process is not None:
            try:
                self.uia_bridge_process.terminate()
                self.uia_bridge_process.wait(timeout=5)
                logger.info("Gateway: UIA Bridge 进程已停止")
            except Exception as exc:
                logger.warning("Gateway: 停止 UIA Bridge 进程异常: %s", exc)
                try:
                    self.uia_bridge_process.kill()
                except Exception:
                    pass
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
        if value in {"wechat", "qq", "feishu", "wecom", "telegram", "line"}:
            normalized.add(value)
    if not normalized:
        normalized.add("qq")
    return normalized


def _parse_bool_env(raw: str | None, *, default: bool = False) -> bool:
    value = str(raw or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _wechat_uia_bridge_enabled() -> bool:
    env = os.environ
    enabled = _parse_bool_env(env.get("EDGE_WECHAT_BRIDGE_ENABLED"), default=False)
    if not enabled:
        return False
    backend = str(env.get("EDGE_WECHAT_BRIDGE_BACKEND") or "uia").strip().lower() or "uia"
    return backend == "uia"


def _should_auto_start_uia_bridge() -> bool:
    """Check whether bootstrap should auto-launch the UIA bridge subprocess."""
    raw = os.environ.get("EDGE_WECHAT_UIA_BRIDGE_AUTO_START", "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    # Default to True when bridge mode is enabled
    return _wechat_uia_bridge_enabled()


def _start_uia_bridge_process(instance: GatewayInstance) -> None:
    """Launch ``tools/wechat_uia_bridge.py`` as a subprocess and start a watchdog."""
    repo_root = Path(__file__).resolve().parents[2]
    bridge_script = repo_root / "tools" / "wechat_uia_bridge.py"
    if not bridge_script.exists():
        logger.warning("Gateway: UIA Bridge 脚本不存在: %s，跳过自动启动", bridge_script)
        return

    # Resolve command queue paths so the bridge and adapter share the same files
    runtime_edge = repo_root / "runtime" / "edge"
    runtime_edge.mkdir(parents=True, exist_ok=True)
    queue_file = os.environ.get("EDGE_WECHAT_COMMAND_QUEUE_FILE") or str(runtime_edge / "wechat_command_queue.jsonl")
    result_file = os.environ.get("EDGE_WECHAT_COMMAND_RESULT_FILE") or str(runtime_edge / "wechat_command_result.jsonl")

    python_exe = sys.executable or "python"
    cmd = [
        python_exe, str(bridge_script),
        "--command-queue-file", queue_file,
        "--command-result-file", result_file,
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(repo_root),
        )
        instance.uia_bridge_process = proc
        logger.info("Gateway: UIA Bridge 子进程已启动 pid=%d cmd=%s", proc.pid, " ".join(cmd))
    except Exception as exc:
        logger.error("Gateway: UIA Bridge 子进程启动失败: %s", exc)
        return

    # Watchdog thread — restart the bridge if it dies unexpectedly
    def _watchdog() -> None:
        restart_count = 0
        max_restarts = 10
        while not instance._shutdown.is_set():
            retcode = instance.uia_bridge_process.poll()
            if retcode is not None:
                if instance._shutdown.is_set():
                    break
                restart_count += 1
                if restart_count > max_restarts:
                    logger.error(
                        "Gateway: UIA Bridge 已重启 %d 次，超过上限 %d，不再尝试",
                        restart_count, max_restarts,
                    )
                    break
                logger.warning(
                    "Gateway: UIA Bridge 进程退出 code=%d，尝试第 %d 次重启",
                    retcode, restart_count,
                )
                time.sleep(2.0)
                try:
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        cwd=str(repo_root),
                    )
                    instance.uia_bridge_process = proc
                    logger.info("Gateway: UIA Bridge 子进程已重启 pid=%d", proc.pid)
                except Exception as exc:
                    logger.error("Gateway: UIA Bridge 重启失败: %s", exc)
                    break
            instance._shutdown.wait(timeout=5.0)

    watchdog_thread = threading.Thread(target=_watchdog, name="uia-bridge-watchdog", daemon=True)
    watchdog_thread.start()
    instance.uia_bridge_watchdog = watchdog_thread


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
        from edge_client.gateway.adapters.wechat_adapter import WeChatAdapter

        bridge_mode = _wechat_uia_bridge_enabled()
        wx_svc = None
        if config.wx_decrypt_output_dir:
            from edge_client.services.wx_db_service import WxDbService

            wx_svc = WxDbService(
                config.wx_decrypt_output_dir,
                poll_watermark_file=config.wx_watermark_file,
            )

        if wx_svc is None and not bridge_mode:
            logger.warning(
                "Gateway: wechat 已启用但既未配置 WECHAT_DECRYPT_OUTPUT_DIR，也未启用 EDGE_WECHAT_BRIDGE_ENABLED，跳过注册"
            )
        else:
            wx_adapter = WeChatAdapter(
                wx_svc,
                self_wxid=config.wx_self_wxid,
                bridge_mode=bridge_mode,
                db_watch_enabled=(wx_svc is not None and not bridge_mode),
                bridge_inbound_backend=str(os.environ.get("EDGE_WECHAT_INBOUND_BACKEND") or "").strip().lower() or None,
            )
            outbound.register(wx_adapter)
            adapters[Platform.WECHAT.value] = wx_adapter

            if bridge_mode:
                logger.info(
                    "Gateway: 微信 UIAutomation bridge mode enabled，使用 webhook + command queue，跳过 DB watcher"
                )
                if _should_auto_start_uia_bridge():
                    _start_uia_bridge_process(instance)
            elif wx_svc is not None:
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

    if "telegram" in enabled_platforms:
        if config.telegram_api_id and config.telegram_api_hash:
            from edge_client.gateway.adapters.telegram_adapter import TelegramAdapter

            tg_proxy: dict[str, Any] | None = None
            if config.telegram_proxy_type and config.telegram_proxy_addr and config.telegram_proxy_port:
                tg_proxy = {
                    "proxy_type": config.telegram_proxy_type,
                    "addr": config.telegram_proxy_addr,
                    "port": config.telegram_proxy_port,
                }

            tg_adapter = TelegramAdapter(
                api_id=config.telegram_api_id,
                api_hash=config.telegram_api_hash,
                phone=config.telegram_phone or "",
                session_name=config.telegram_session_name,
                self_id=config.telegram_self_id,
                typing_speed=config.telegram_typing_speed,
                min_delay_s=config.telegram_min_delay_s,
                max_delay_s=config.telegram_max_delay_s,
                proxy=tg_proxy,
            )
            outbound.register(tg_adapter)
            adapters[Platform.TELEGRAM.value] = tg_adapter

            # 异步连接 + 注册消息监听
            import asyncio as _aio

            def _tg_on_message(msg: Any) -> None:
                normalized = normalizer.normalize(msg)
                if normalized is not None:
                    router.route(normalized)

            async def _tg_boot() -> None:
                await tg_adapter.connect()
                await tg_adapter.start_listening(_tg_on_message)

            try:
                loop = _aio.get_running_loop()
                loop.create_task(_tg_boot())
            except RuntimeError:
                _aio.run(_tg_boot())

            logger.info("Gateway: Telegram Userbot 适配器已注册 session=%s", config.telegram_session_name)
        else:
            logger.warning("Gateway: telegram 已启用但缺少 TELEGRAM_API_ID/TELEGRAM_API_HASH，跳过注册")

    if "line" in enabled_platforms:
        if config.line_channel_access_token and config.line_channel_secret:
            from edge_client.gateway.adapters.line_adapter import LineAdapter

            line_adapter = LineAdapter(
                channel_access_token=config.line_channel_access_token,
                channel_secret=config.line_channel_secret,
            )
            outbound.register(line_adapter)
            adapters[Platform.LINE.value] = line_adapter
            logger.info("Gateway: LINE Messaging API 适配器已注册")
        else:
            logger.warning("Gateway: line 已启用但缺少 LINE_CHANNEL_ACCESS_TOKEN/LINE_CHANNEL_SECRET，跳过注册")

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
