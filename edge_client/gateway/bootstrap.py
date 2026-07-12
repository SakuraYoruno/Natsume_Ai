"""Unified Message Gateway 启动入口。"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import os
import platform
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("Natsume.edge.gateway.bootstrap")

# Keep lifecycle threads independent from tests and integrations that replace
# ``threading.Thread`` for delivery-worker observation.  These two threads own
# process infrastructure and must always execute their targets.
_THREAD_CLASS = threading.Thread


def _configure_windows_event_loop_policy() -> None:
    if platform.system().lower() != "windows":
        return
    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy is None:
        return
    try:
        asyncio.set_event_loop_policy(selector_policy())
    except Exception:
        logger.warning("Gateway: failed to set Windows selector event loop policy", exc_info=True)


_configure_windows_event_loop_policy()


def _resolve_input_report_timeout_s() -> float:
    """Resolve Edge → Host gateway input report timeout.

    QQ / gateway chat replies on Host commonly take several seconds.  The old
    3s default caused EdgeInputReporter to time out, cache, and retry even when
    Host eventually processed the request successfully.  Keep a safer default
    while still allowing env override.
    """
    raw = str(os.environ.get("EDGE_GATEWAY_INPUT_REPORT_TIMEOUT_S") or "").strip()
    if not raw:
        return 15.0
    try:
        return max(3.0, float(raw))
    except (TypeError, ValueError):
        logger.warning(
            "Gateway: invalid EDGE_GATEWAY_INPUT_REPORT_TIMEOUT_S=%r, fallback to 15s",
            raw,
        )
        return 15.0


def _resolve_input_report_max_retry() -> int:
    raw = str(os.environ.get("EDGE_GATEWAY_INPUT_REPORT_MAX_RETRY") or "").strip()
    if not raw:
        return 8640
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        logger.warning(
            "Gateway: invalid EDGE_GATEWAY_INPUT_REPORT_MAX_RETRY=%r, fallback to 8640",
            raw,
        )
        return 8640


def _resolve_input_report_retry_interval_s() -> float:
    raw = str(os.environ.get("EDGE_GATEWAY_INPUT_REPORT_RETRY_INTERVAL_S") or "").strip()
    if not raw:
        return 10.0
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        logger.warning(
            "Gateway: invalid EDGE_GATEWAY_INPUT_REPORT_RETRY_INTERVAL_S=%r, fallback to 10s",
            raw,
        )
        return 10.0


def _resolve_input_report_cache_file() -> str:
    raw = str(os.environ.get("EDGE_GATEWAY_INPUT_REPORT_CACHE_FILE") or "").strip()
    if raw:
        return raw
    repo_root = Path(__file__).resolve().parents[2]
    return str((repo_root / "runtime" / "edge" / "gateway_input_retry_queue.json").resolve())


def _resolve_gateway_transport_db_path() -> str:
    raw = str(
        os.environ.get("EDGE_GATEWAY_TRANSPORT_DB_PATH")
        or os.environ.get("NATSUME_AUDIT_DB")
        or os.environ.get("Natsume_AUDIT_DB")
        or ""
    ).strip()
    if raw:
        return str(Path(raw).expanduser().resolve())
    repo_root = Path(__file__).resolve().parents[2]
    return str((repo_root / "host_brain" / "data" / "audit.sqlite3").resolve())


def _resolve_gateway_reply_hmac_secret() -> str | None:
    """Resolve the isolated Host -> Gateway reply secret without RPC-key fallback."""

    direct = str(os.environ.get("EDGE_GATEWAY_REPLY_HMAC_SECRET") or "").strip()
    if direct:
        return direct
    secret_ref = str(os.environ.get("EDGE_GATEWAY_REPLY_HMAC_SECRET_REF") or "").strip()
    if not secret_ref:
        return None
    try:
        from edge_client.config import _resolve_runtime_key_materials  # noqa: PLC0415

        _profiles, secret_store, _epoch, _node_id = _resolve_runtime_key_materials(
            env=os.environ,
            key_profiles=None,
            secret_store=None,
            node_id=None,
            current_epoch=None,
        )
    except Exception as exc:
        logger.warning("Gateway: isolated reply secret resolver unavailable: %s", exc)
        return None
    value = str(secret_store.get(secret_ref) or "").strip()
    return value or None


def _resolve_gateway_host_hmac_secret() -> str | None:
    """Resolve the isolated Edge -> Host input credential.

    This key grants Host ``host:write`` and therefore must never fall back to
    a Bridge webhook or generic Edge RPC key.
    """

    direct = str(os.environ.get("EDGE_GATEWAY_HOST_HMAC_SECRET") or "").strip()
    if direct:
        return direct
    secret_ref = str(
        os.environ.get("EDGE_GATEWAY_HOST_HMAC_SECRET_REF") or ""
    ).strip()
    if not secret_ref:
        return None
    try:
        from edge_client.config import _resolve_runtime_key_materials  # noqa: PLC0415

        _profiles, secret_store, _epoch, _node_id = _resolve_runtime_key_materials(
            env=os.environ,
            key_profiles=None,
            secret_store=None,
            node_id=None,
            current_epoch=None,
        )
    except Exception as exc:
        logger.warning("Gateway: isolated Host ingress secret resolver unavailable: %s", exc)
        return None
    value = str(secret_store.get(secret_ref) or "").strip()
    return value or None


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


def _resolve_gateway_startup_timeout_s(
    env_name: str = "EDGE_GATEWAY_SERVER_STARTUP_TIMEOUT_S",
    *,
    default: float = 10.0,
) -> float:
    raw = str(os.environ.get(env_name) or "").strip()
    if not raw:
        return default
    try:
        return min(120.0, max(0.1, float(raw)))
    except (TypeError, ValueError):
        logger.warning("Gateway: invalid %s=%r, fallback to %.1fs", env_name, raw, default)
        return default


class _ManagedUvicornServer:
    """Own a uvicorn server thread with explicit startup/failure handoff."""

    def __init__(self, app: Any, *, host: str, port: int) -> None:
        import uvicorn

        self._server = uvicorn.Server(uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="info",
            loop="asyncio",
        ))
        self._failure: BaseException | None = None
        self._finished = threading.Event()
        self._stop_requested = threading.Event()
        self.thread = _THREAD_CLASS(
            target=self._run,
            name="gateway-server",
            daemon=True,
        )

    @property
    def server(self) -> Any:
        return self._server

    def _run(self) -> None:
        try:
            _configure_windows_event_loop_policy()
            self._server.run()
            if not self._stop_requested.is_set():
                self._failure = RuntimeError("uvicorn server exited unexpectedly")
        except BaseException as exc:  # includes uvicorn's bind-failure SystemExit
            self._failure = exc
        finally:
            self._finished.set()

    def start(self, *, timeout_s: float) -> None:
        self.thread.start()
        deadline = time.monotonic() + max(0.1, timeout_s)
        while True:
            if self._failure is not None:
                failure = self._failure
                raise RuntimeError(
                    f"GW_SERVER_START_FAILED:{type(failure).__name__}:{failure}"
                ) from failure
            if bool(getattr(self._server, "started", False)):
                if self.thread.is_alive():
                    return
                raise RuntimeError("GW_SERVER_START_FAILED:server exited after readiness")
            if self._finished.is_set():
                raise RuntimeError("GW_SERVER_START_FAILED:server exited before readiness")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.stop(timeout_s=min(1.0, timeout_s))
                raise TimeoutError("GW_SERVER_START_TIMEOUT")
            self._finished.wait(min(0.02, remaining))

    def stop(self, *, timeout_s: float = 5.0) -> None:
        self._stop_requested.set()
        try:
            self._server.should_exit = True
        except Exception:
            logger.warning("Gateway: failed to request uvicorn shutdown", exc_info=True)
        if self.thread.ident is None or self.thread is threading.current_thread():
            return
        total_timeout = max(0.0, timeout_s)
        graceful_timeout = total_timeout * 0.75
        force_timeout = total_timeout - graceful_timeout
        self.thread.join(timeout=graceful_timeout)
        if self.thread.is_alive():
            try:
                self._server.force_exit = True
            except Exception:
                logger.warning("Gateway: failed to force uvicorn shutdown", exc_info=True)
            self.thread.join(timeout=force_timeout)
        if self.thread.is_alive():
            raise RuntimeError("GW_SERVER_STOP_TIMEOUT")


class _PersistentAsyncLoop:
    """A bounded, process-owned asyncio loop for long-lived async adapters."""

    def __init__(self, *, name: str = "gateway-async-loop") -> None:
        self._name = name
        self._ready = threading.Event()
        self._stop_requested = threading.Event()
        self._failure: BaseException | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread = _THREAD_CLASS(target=self._run, name=name, daemon=True)

    def _run(self) -> None:
        loop: asyncio.AbstractEventLoop | None = None
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self.loop = loop
            if not self._stop_requested.is_set():
                # Signal readiness from inside the running loop; setting the
                # event immediately before run_forever creates a race where
                # start() observes is_running() == False and fails spuriously.
                loop.call_soon(self._ready.set)
                loop.run_forever()
            else:
                self._ready.set()
        except BaseException as exc:
            self._failure = exc
            self._ready.set()
        finally:
            if loop is not None:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
                asyncio.set_event_loop(None)
                loop.close()

    def start(self, *, timeout_s: float) -> None:
        self.thread.start()
        if not self._ready.wait(max(0.1, timeout_s)):
            raise TimeoutError(f"GW_ASYNC_LOOP_START_TIMEOUT:{self._name}")
        if self._failure is not None:
            failure = self._failure
            raise RuntimeError(
                f"GW_ASYNC_LOOP_START_FAILED:{type(failure).__name__}:{failure}"
            ) from failure
        if self.loop is None or not self.loop.is_running() or not self.thread.is_alive():
            raise RuntimeError(f"GW_ASYNC_LOOP_START_FAILED:{self._name}:not_running")

    def run(self, coroutine: Any, *, timeout_s: float) -> Any:
        loop = self.loop
        if loop is None or loop.is_closed() or not loop.is_running():
            close = getattr(coroutine, "close", None)
            if callable(close):
                close()
            raise RuntimeError(f"GW_ASYNC_LOOP_UNAVAILABLE:{self._name}")
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        try:
            return future.result(timeout=max(0.1, timeout_s))
        except TimeoutError:
            future.cancel()
            raise TimeoutError(f"GW_ASYNC_OPERATION_TIMEOUT:{self._name}") from None

    def stop(self, *, timeout_s: float = 5.0) -> None:
        self._stop_requested.set()
        loop = self.loop
        if loop is not None and not loop.is_closed() and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if self.thread.ident is None or self.thread is threading.current_thread():
            return
        self.thread.join(timeout=max(0.0, timeout_s))
        if self.thread.is_alive():
            raise RuntimeError(f"GW_ASYNC_LOOP_STOP_TIMEOUT:{self._name}")


class GatewayInstance:
    """Gateway 运行实例，持有可停止的组件。"""

    def __init__(self) -> None:
        self.watcher = None
        self.refresher = None
        self.reporter = None
        self.delivery_runtime = None
        self.telegram_adapter = None
        self.telegram_task = None
        self.async_loop: _PersistentAsyncLoop | None = None
        self.server: _ManagedUvicornServer | None = None
        self.server_thread: threading.Thread | None = None
        self.wechat_bridge_process: subprocess.Popen | None = None
        self.wechat_bridge_watchdog: threading.Thread | None = None
        self.peripheral_agent = None
        self.connected_platforms: set[str] = set()
        self._shutdown = threading.Event()
        self._intake_shutdown = threading.Event()
        self._stop_lock = threading.Lock()
        self._stopped = False
        self._stopping = False

    def stop(self) -> None:
        with self._stop_lock:
            if self._stopped:
                return
            if self._stopping:
                raise RuntimeError("GW_GATEWAY_STOP_ALREADY_IN_PROGRESS")
            self._stopping = True
        try:
            self._stop_components()
        except BaseException:
            with self._stop_lock:
                self._stopping = False
            raise
        with self._stop_lock:
            self._stopping = False
            self._stopped = True

    def _stop_components(self) -> None:
        self._intake_shutdown.set()
        if self.delivery_runtime is not None:
            self.delivery_runtime.disable_intake()
        # Stop public intake first.  This also joins the uvicorn thread, so a
        # failed/partial startup cannot leave a bound listener behind.
        if self.server is not None:
            self.server.stop(timeout_s=5.0)
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
        # Keep outbound dependencies and the Host reporter alive until the
        # durable worker has definitely exited.  Timeout is fail-closed and a
        # later stop() call may retry the join.
        if self.delivery_runtime is not None:
            self.delivery_runtime.stop(timeout_s=_resolve_gateway_startup_timeout_s(
                "EDGE_GATEWAY_WORKER_STOP_TIMEOUT_S",
                default=75.0,
            ))
        # Telegram connect/listen/disconnect all run on the same persistent
        # loop; only after disconnect completes do we close and join it.
        if self.telegram_adapter is not None:
            disconnect = getattr(self.telegram_adapter, "disconnect", None)
            if callable(disconnect):
                try:
                    result = disconnect()
                    if inspect.isawaitable(result):
                        if self.async_loop is not None:
                            self.async_loop.run(result, timeout_s=5.0)
                        else:  # partial startup before the loop was installed
                            asyncio.run(result)
                except Exception as exc:
                    logger.warning("Gateway: Telegram disconnect failed: %s", exc)
        if self.async_loop is not None:
            self.async_loop.stop(timeout_s=5.0)
        # Keep the bridge watchdog alive until every asynchronous dependency
        # has stopped successfully; a timeout leaves the instance retryable.
        self._shutdown.set()
        if self.wechat_bridge_process is not None:
            try:
                self.wechat_bridge_process.terminate()
                self.wechat_bridge_process.wait(timeout=5)
                logger.info("Gateway: WeChat bridge process stopped")
            except Exception as exc:
                logger.warning("Gateway: failed to stop WeChat bridge process: %s", exc)
                try:
                    self.wechat_bridge_process.kill()
                except Exception:
                    pass
        if self.peripheral_agent is not None and self.connected_platforms:
            for platform in sorted(self.connected_platforms):
                try:
                    self.peripheral_agent.publish_platform_disconnected(
                        platform=platform,
                        detail="gateway_stopped",
                    )
                except Exception as exc:
                    logger.debug("Gateway: platform_disconnected emit degraded: %s", exc)
            self.connected_platforms.clear()
        if self.reporter is not None:
            try:
                self.reporter.stop_retry_loop()
            except Exception as exc:
                logger.warning("Gateway: 停止 reporter retry loop 异常: %s", exc)
        if (
            self.wechat_bridge_watchdog is not None
            and self.wechat_bridge_watchdog.is_alive()
            and self.wechat_bridge_watchdog is not threading.current_thread()
        ):
            self.wechat_bridge_watchdog.join(timeout=5.0)
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


def _wechat_bridge_backend() -> str:
    """Return the configured WeChat bridge backend after removing UIA support."""
    backend = str(os.environ.get("EDGE_WECHAT_BRIDGE_BACKEND") or "wxautox4").strip().lower() or "wxautox4"
    if backend == "uia":
        logger.warning(
            "Gateway: legacy EDGE_WECHAT_BRIDGE_BACKEND=uia is no longer supported; falling back to wxautox4"
        )
        return "wxautox4"
    if backend in {"wxautox4", "external"}:
        return backend
    if backend:
        logger.warning("Gateway: invalid EDGE_WECHAT_BRIDGE_BACKEND=%s; falling back to wxautox4", backend)
    return "wxautox4"


def _wechat_bridge_enabled() -> bool:
    env = os.environ
    enabled = _parse_bool_env(env.get("EDGE_WECHAT_BRIDGE_ENABLED"), default=False)
    if not enabled:
        return False
    return _wechat_bridge_backend() == "wxautox4"


def _should_auto_start_wechat_bridge() -> bool:
    """Check whether bootstrap should auto-launch the WeChat bridge subprocess."""
    raw = os.environ.get("EDGE_WECHAT_BRIDGE_AUTO_START", "").strip().lower()
    if not raw:
        legacy_raw = os.environ.get("EDGE_WECHAT_UIA_BRIDGE_AUTO_START", "").strip().lower()
        if legacy_raw:
            logger.warning(
                "Gateway: EDGE_WECHAT_UIA_BRIDGE_AUTO_START is deprecated; use EDGE_WECHAT_BRIDGE_AUTO_START"
            )
            raw = legacy_raw
    if raw in {"0", "false", "no", "off"}:
        return False
    # Default to True when bridge mode is enabled
    return _wechat_bridge_enabled()


def _start_wechat_bridge_process(instance: GatewayInstance) -> bool:
    """Launch the wxautox4 bridge subprocess and start a watchdog."""
    repo_root = Path(__file__).resolve().parents[2]
    bridge_script = repo_root / "tools" / "wxautox4_bridge.py"
    bridge_label = "wxautox4 Bridge"
    if not bridge_script.exists():
        logger.warning("Gateway: %s 脚本不存在: %s，跳过自动启动", bridge_label, bridge_script)
        return False

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
        instance.wechat_bridge_process = proc
        logger.info("Gateway: %s 子进程已启动 pid=%d cmd=%s", bridge_label, proc.pid, " ".join(cmd))
    except Exception as exc:
        logger.error("Gateway: %s 子进程启动失败: %s", bridge_label, exc)
        return False

    # Watchdog thread — restart the bridge if it dies unexpectedly
    def _watchdog() -> None:
        restart_count = 0
        max_restarts = 10
        while not instance._shutdown.is_set():
            retcode = instance.wechat_bridge_process.poll()
            if retcode is not None:
                if instance._shutdown.is_set():
                    break
                restart_count += 1
                if restart_count > max_restarts:
                    logger.error(
                        "Gateway: %s 已重启 %d 次，超过上限 %d，不再尝试",
                        bridge_label, restart_count, max_restarts,
                    )
                    break
                logger.warning(
                    "Gateway: %s 进程退出 code=%d，尝试第 %d 次重启",
                    bridge_label, retcode, restart_count,
                )
                time.sleep(2.0)
                try:
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        cwd=str(repo_root),
                    )
                    instance.wechat_bridge_process = proc
                    logger.info("Gateway: %s 子进程已重启 pid=%d", bridge_label, proc.pid)
                except Exception as exc:
                    logger.error("Gateway: %s 重启失败: %s", bridge_label, exc)
                    break
            instance._shutdown.wait(timeout=5.0)

    watchdog_thread = threading.Thread(target=_watchdog, name="wechat-bridge-watchdog", daemon=True)
    watchdog_thread.start()
    instance.wechat_bridge_watchdog = watchdog_thread
    return True


def start_gateway(config: GatewayConfig) -> GatewayInstance:
    """组装并启动 Unified Message Gateway。"""

    from edge_client.channels.input_reporter import EdgeInputReporter
    from edge_client.gateway.models import Platform
    from edge_client.gateway.normalizer import MessageNormalizer
    from edge_client.gateway.outbound import OutboundDispatcher
    from edge_client.gateway.router import GatewayRouter
    from edge_client.gateway.server import create_gateway_app
    from edge_client.gateway.delivery_runtime import (
        GatewayDeliveryRuntime,
        GatewayDeliveryError,
        GatewayTransportMode,
        load_gateway_accept_new_intake,
        load_gateway_transport_mode,
    )
    from edge_client.gateway.webhook_security import (
        FeishuWebhookVerifier,
        InternalHmacWebhookVerifier,
        LineWebhookVerifier,
        VerifierRegistry,
        WeComWebhookVerifier,
    )
    from edge_client.gateway.voice_asr import GatewayVoiceAsr
    from edge_client.tribe.peripheral_agent import PeripheralAgent
    from edge_client.tribe.registry import build_edge_tribe_bridge

    instance = GatewayInstance()
    enabled_platforms = _normalize_platforms(config.enabled_platforms)
    transport_mode = load_gateway_transport_mode()
    accept_new_intake = load_gateway_accept_new_intake()
    gateway_host_hmac_secret = _resolve_gateway_host_hmac_secret()
    gateway_reply_hmac_secret = _resolve_gateway_reply_hmac_secret()
    wechat_callback_hmac_secret = str(
        os.environ.get("NATSUME_WECHAT_GATEWAY_CALLBACK_HMAC_SECRET") or ""
    ).strip()

    reporter = EdgeInputReporter(
        host_url=config.host_url,
        hmac_key=gateway_host_hmac_secret,
        timeout_s=_resolve_input_report_timeout_s(),
        max_retry=_resolve_input_report_max_retry(),
        retry_interval_s=_resolve_input_report_retry_interval_s(),
        cache_file=_resolve_input_report_cache_file(),
    )
    instance.reporter = reporter

    tribe_bridge = None
    try:
        tribe_bridge = build_edge_tribe_bridge()
    except Exception as exc:  # pragma: no cover - non-blocking sidecar
        logger.debug("Gateway: tribe bridge build degraded: %s", exc)
    instance.peripheral_agent = PeripheralAgent(bridge=tribe_bridge)

    normalizer = MessageNormalizer()
    gateway_voice_asr = GatewayVoiceAsr()
    router = GatewayRouter(
        reporter,
        voice_asr=(gateway_voice_asr if gateway_voice_asr.enabled else None),
    )
    if gateway_voice_asr.enabled:
        logger.info("Gateway: voice-message ASR trigger enabled")
    else:
        logger.info("Gateway: voice-message ASR trigger disabled")
    try:
        router.set_tribe_bridge(tribe_bridge)
    except Exception as exc:  # pragma: no cover - non-blocking sidecar
        logger.debug("Gateway: tribe bridge init degraded: %s", exc)
    outbound = OutboundDispatcher()
    adapters: dict[str, Any] = {}
    pending_platform_events: list[tuple[str, str, str | None]] = []
    pending_wechat_bridge_start = False
    pending_refresher = None
    pending_watcher = None
    pending_telegram_connect = None
    pending_telegram_listen = None
    delivery_runtime: GatewayDeliveryRuntime | None = None

    def _mark_platform_connected(platform: str, detail: str | None = None) -> None:
        normalized = str(platform or "").strip().lower()
        if not normalized:
            return
        pending_platform_events.append(("connected", normalized, detail))

    def _mark_platform_unavailable(platform: str, reason: str) -> None:
        normalized = str(platform or "").strip().lower()
        if not normalized:
            return
        pending_platform_events.append(("disconnected", normalized, reason))

    def _accept_non_http_message(unified: Any, *, principal_id: str) -> None:
        """Normalize then commit non-HTTP input without touching legacy cache.

        TASK-0592 remove-by: replace this bootstrap closure with the shared
        channel-ingress port once watcher/listener ownership is separated.
        """

        runtime = delivery_runtime
        if runtime is None or not runtime.intake_enabled:
            logger.warning(
                "Gateway: non-HTTP intake rejected platform=%s reason=%s",
                str(getattr(getattr(unified, "platform", None), "value", "unknown")),
                "GW_TRANSPORT_DISABLED",
            )
            return
        normalized = normalizer.normalize(unified, deduplicate=False)
        if normalized is None:
            return
        platform_value = str(normalized.platform.value).strip().lower()
        try:
            receipt = runtime.accept_non_http(
                platform=platform_value,
                principal_id=principal_id,
                tenant_id="local",
                platform_event_id=str(normalized.msg_id),
                normalized_payload=normalized.as_report_payload(),
                ordering_key=str(normalized.conversation_id),
            )
        except GatewayDeliveryError as exc:
            logger.error(
                "Gateway: non-HTTP durable accept failed platform=%s event_sha256=%s reason=%s",
                platform_value,
                hashlib.sha256(
                    str(normalized.msg_id).encode("utf-8")
                ).hexdigest()[:16],
                exc.reason_code,
            )
            return
        logger.info(
            "Gateway: non-HTTP durable accept platform=%s event_sha256=%s status=%s",
            platform_value,
            hashlib.sha256(
                str(normalized.msg_id).encode("utf-8")
            ).hexdigest()[:16],
            receipt.status,
        )

    if "wechat" in enabled_platforms:
        from edge_client.gateway.adapters.wechat_adapter import WeChatAdapter

        bridge_mode = _wechat_bridge_enabled()
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
            _mark_platform_unavailable(
                Platform.WECHAT.value,
                "wechat_missing_decrypt_output_or_bridge",
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
            _mark_platform_connected(
                Platform.WECHAT.value,
                "adapter_registered",
            )

            if bridge_mode:
                logger.info(
                    "Gateway: wechat bridge mode enabled; using webhook + command queue and skipping DB watcher"
                )
                if _should_auto_start_wechat_bridge():
                    pending_wechat_bridge_start = True
            elif wx_svc is not None:
                if config.wx_db_storage_dir:
                    from edge_client.services.wx_decrypt_refresher import WxDecryptRefresher

                    refresher = WxDecryptRefresher(
                        db_storage_dir=config.wx_db_storage_dir,
                        decrypt_output_dir=config.wx_decrypt_output_dir,
                        key_cache_path=config.wx_key_cache_path,
                        refresh_interval_s=config.wx_decrypt_refresh_interval_s,
                    )
                    pending_refresher = refresher
                else:
                    logger.warning("Gateway: 未配置 WECHAT_DB_STORAGE_DIR，跳过自动 refresh")

                from edge_client.services.wx_watcher import WxDbWatcher

                def _on_new_messages(msgs: list[Any]) -> None:
                    for wx_msg in msgs:
                        unified = wx_adapter.convert_wx_message(wx_msg)
                        if unified is None:
                            continue
                        _accept_non_http_message(
                            unified,
                            principal_id="gateway.non-http.wechat-db",
                        )

                watcher = WxDbWatcher(
                    wx_svc,
                    _on_new_messages,
                    poll_interval_s=config.wx_poll_interval_s,
                )
                pending_watcher = watcher

    if "qq" in enabled_platforms:
        if config.qq_onebot_api_base:
            from edge_client.gateway.adapters.qq_adapter import QQAdapter

            qq_adapter = QQAdapter(
                config.qq_onebot_api_base,
                self_id=config.qq_self_id,
            )
            outbound.register(qq_adapter)
            adapters[Platform.QQ.value] = qq_adapter
            _mark_platform_connected(
                Platform.QQ.value,
                "adapter_registered",
            )
        else:
            logger.warning("Gateway: qq 已启用但缺少 QQ_ONEBOT_API_BASE，跳过注册")
            _mark_platform_unavailable(
                Platform.QQ.value,
                "qq_missing_api_base",
            )

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
            _mark_platform_connected(
                Platform.FEISHU.value,
                "adapter_registered",
            )
        else:
            logger.warning("Gateway: feishu 已启用但缺少 FEISHU_APP_ID/FEISHU_APP_SECRET，跳过注册")
            _mark_platform_unavailable(
                Platform.FEISHU.value,
                "feishu_missing_app_credentials",
            )

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
            _mark_platform_connected(
                Platform.WECOM.value,
                "adapter_registered",
            )
        else:
            logger.warning("Gateway: wecom 已启用但缺少 corp 配置，跳过注册")
            _mark_platform_unavailable(
                Platform.WECOM.value,
                "wecom_missing_corp_credentials",
            )

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
            _mark_platform_connected(
                Platform.TELEGRAM.value,
                "adapter_registered",
            )

            def _tg_on_message(msg: Any) -> None:
                if instance._intake_shutdown.is_set():
                    return
                _accept_non_http_message(
                    msg,
                    principal_id="gateway.non-http.telegram-userbot",
                )

            async def _tg_connect() -> None:
                connected = await tg_adapter.connect()
                if connected is not True:
                    raise RuntimeError("GW_TELEGRAM_CONNECT_FAILED")

            async def _tg_listen() -> None:
                await tg_adapter.start_listening(_tg_on_message)

            pending_telegram_connect = _tg_connect
            pending_telegram_listen = _tg_listen

            logger.info("Gateway: Telegram Userbot 适配器已注册 session=%s", config.telegram_session_name)
        else:
            logger.warning("Gateway: telegram 已启用但缺少 TELEGRAM_API_ID/TELEGRAM_API_HASH，跳过注册")
            _mark_platform_unavailable(
                Platform.TELEGRAM.value,
                "telegram_missing_api_credentials",
            )

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
            _mark_platform_connected(
                Platform.LINE.value,
                "adapter_registered",
            )
        else:
            logger.warning("Gateway: line 已启用但缺少 LINE_CHANNEL_ACCESS_TOKEN/LINE_CHANNEL_SECRET，跳过注册")
            _mark_platform_unavailable(
                Platform.LINE.value,
                "line_missing_channel_credentials",
            )

    webhook_verifiers = VerifierRegistry()
    if Platform.LINE.value in adapters:
        webhook_verifiers.register(LineWebhookVerifier(channel_secret=config.line_channel_secret))
    if Platform.FEISHU.value in adapters:
        webhook_verifiers.register(FeishuWebhookVerifier(
            encrypt_key=config.feishu_encrypt_key,
            verification_token=config.feishu_verification_token,
            verify_only=False,
        ))
    if Platform.WECOM.value in adapters:
        webhook_verifiers.register(WeComWebhookVerifier(
            token=config.wecom_token,
            encoding_aes_key=config.wecom_encoding_aes_key,
            corp_id=config.wecom_corp_id,
            verify_only=False,
        ))
    for internal_platform in (Platform.QQ.value, Platform.TELEGRAM.value):
        if internal_platform in adapters:
            webhook_verifiers.register(InternalHmacWebhookVerifier(
                platform=internal_platform,
                shared_secret=config.hmac_key,
            ))
    if Platform.WECHAT.value in adapters:
        if not wechat_callback_hmac_secret:
            raise RuntimeError("GW_WECHAT_CALLBACK_SECRET_MISSING")
        webhook_verifiers.register(InternalHmacWebhookVerifier(
            platform=Platform.WECHAT.value,
            shared_secret=wechat_callback_hmac_secret,
            principal_id="edge.gateway.wechat.bridge",
            credential_ref="edge.gateway.wechat.callback.hmac.v1",
        ))

    reply_verifiers = VerifierRegistry((
        InternalHmacWebhookVerifier(
            platform="internal_reply",
            shared_secret=gateway_reply_hmac_secret,
            principal_id="service.host.gateway-reply",
            credential_ref="edge.gateway.reply.hmac.v1",
        ),
    ))

    if transport_mode in {GatewayTransportMode.SHADOW, GatewayTransportMode.DURABLE}:
        if not str(gateway_reply_hmac_secret or "").strip():
            raise RuntimeError("GW_REPLY_INGRESS_SECRET_MISSING")
        missing_verifiers = sorted(set(adapters) - set(webhook_verifiers.platforms))
        if missing_verifiers:
            raise RuntimeError(
                "GW_VERIFIER_PREFLIGHT_FAILED:" + ",".join(missing_verifiers)
            )
        internal_platforms = {Platform.QQ.value, Platform.TELEGRAM.value}
        if internal_platforms.intersection(adapters) and not str(config.hmac_key or "").strip():
            raise RuntimeError("GW_INTERNAL_WEBHOOK_SECRET_MISSING")
        if Platform.LINE.value in adapters and not str(config.line_channel_secret or "").strip():
            raise RuntimeError("GW_LINE_WEBHOOK_SECRET_MISSING")
        if Platform.FEISHU.value in adapters and (
            not str(config.feishu_encrypt_key or "").strip()
            or not str(config.feishu_verification_token or "").strip()
        ):
            raise RuntimeError("GW_FEISHU_WEBHOOK_SECRET_MISSING")
        if Platform.WECOM.value in adapters and (
            not str(config.wecom_token or "").strip()
            or not str(config.wecom_encoding_aes_key or "").strip()
            or not str(config.wecom_corp_id or "").strip()
        ):
            raise RuntimeError("GW_WECOM_WEBHOOK_SECRET_MISSING")

    transport_store = None
    if transport_mode is GatewayTransportMode.DURABLE:
        if not str(gateway_host_hmac_secret or "").strip():
            raise RuntimeError("GW_HOST_INGRESS_SECRET_MISSING")
        # TASK-0584 remove-by: composition currently imports the central Host
        # implementation; do not copy this store into Edge.
        from host_brain.eventing.gateway_transport import SQLiteGatewayTransportStore  # noqa: PLC0415

        transport_store = SQLiteGatewayTransportStore(
            _resolve_gateway_transport_db_path(),
            verify_schema=True,
        )
    delivery_runtime = GatewayDeliveryRuntime(
        mode=transport_mode,
        store=transport_store,
        reporter=reporter,
        outbound=outbound,
        accept_new_intake=accept_new_intake,
    )
    # Schema, store and credentials are all checked before any bridge,
    # refresher, watcher, listener or worker thread can start.
    delivery_runtime.assert_ready()
    instance.delivery_runtime = delivery_runtime

    app_kwargs: dict[str, Any] = {
        "router": router,
        "normalizer": normalizer,
        "adapters": adapters,
        "outbound": outbound,
        "delivery_runtime": delivery_runtime,
        "webhook_verifiers": webhook_verifiers,
        "reply_verifiers": reply_verifiers,
    }
    # Preserve bootstrap test doubles and older injected factories while the
    # real factory consumes every durable-security dependency above.
    try:
        factory_signature = inspect.signature(create_gateway_app)
        if not any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in factory_signature.parameters.values()
        ):
            app_kwargs = {
                key: value
                for key, value in app_kwargs.items()
                if key in factory_signature.parameters
            }
    except (TypeError, ValueError):
        pass
    app = create_gateway_app(**app_kwargs)

    try:
        # Preflight and app construction are complete.  Establish HTTP
        # readiness and every outbound dependency before the durable worker can
        # claim an old record.
        server = _ManagedUvicornServer(
            app,
            host="0.0.0.0",
            port=config.gateway_port,
        )
        instance.server = server
        instance.server_thread = server.thread
        server.start(timeout_s=_resolve_gateway_startup_timeout_s())

        if pending_wechat_bridge_start and delivery_runtime.delivery_enabled:
            if not _start_wechat_bridge_process(instance):
                raise RuntimeError("GW_WECHAT_BRIDGE_START_FAILED")

        telegram_timeout_s = _resolve_gateway_startup_timeout_s(
            "EDGE_GATEWAY_TELEGRAM_STARTUP_TIMEOUT_S",
            default=30.0,
        )
        if pending_telegram_connect is not None and delivery_runtime.delivery_enabled:
            instance.telegram_adapter = adapters.get(Platform.TELEGRAM.value)
            instance.async_loop = _PersistentAsyncLoop(name="gateway-telegram-loop")
            instance.async_loop.start(timeout_s=telegram_timeout_s)
            instance.async_loop.run(
                pending_telegram_connect(),
                timeout_s=telegram_timeout_s,
            )

        delivery_runtime.start()

        # LEGACY_DRAIN owns only the pre-existing reporter compatibility queue.
        if transport_mode is GatewayTransportMode.LEGACY_DRAIN:
            reporter.start_retry_loop()

        # New intake producers start only after delivery is ready.
        if pending_refresher is not None and delivery_runtime.intake_enabled:
            instance.refresher = pending_refresher
            refresh_mode = pending_refresher.start()
            logger.info("Gateway: 微信解密刷新已启动 mode=%s", refresh_mode)
        if pending_watcher is not None and delivery_runtime.intake_enabled:
            instance.watcher = pending_watcher
            watch_mode = pending_watcher.start()
            logger.info("Gateway: 微信监听已启动 mode=%s", watch_mode)

        if pending_telegram_listen is not None and delivery_runtime.intake_enabled:
            instance.async_loop.run(
                pending_telegram_listen(),
                timeout_s=telegram_timeout_s,
            )

        for event_type, event_platform, event_detail in pending_platform_events:
            try:
                if event_type == "connected":
                    instance.connected_platforms.add(event_platform)
                    instance.peripheral_agent.publish_platform_connected(
                        platform=event_platform,
                        detail=event_detail,
                    )
                else:
                    instance.peripheral_agent.publish_platform_disconnected(
                        platform=event_platform,
                        detail=event_detail,
                    )
            except Exception as exc:  # pragma: no cover - non-blocking sidecar
                logger.debug("Gateway: platform lifecycle emit degraded: %s", exc)

    except BaseException:
        # Any partial activation is rolled back.  Preflight failures occur
        # before this block and therefore have zero background components.
        instance.stop()
        raise
    logger.info(
        "Gateway: 已启动 port=%d platforms=%s transport=%s intake=%s",
        config.gateway_port,
        ",".join(sorted(enabled_platforms)),
        transport_mode.value,
        delivery_runtime.intake_enabled,
    )
    return instance
