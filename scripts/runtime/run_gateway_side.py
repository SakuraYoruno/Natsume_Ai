#!/usr/bin/env python
"""Run Unified Gateway as a Host-side companion without starting remote Edge.

This is the outer composition root: Edge keeps depending only on contracts,
while the executable injects the concrete SQLite transport repository and
applies the canonical migration catalog before opening intake.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import signal
import sys
import threading
import time
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from edge_client.config import EdgeConfig, load_edge_config  # noqa: E402
from edge_client.gateway.bootstrap import (  # noqa: E402
    GatewayConfig,
    GatewayInstance,
    start_gateway,
)
from edge_client.tribe.edge_tribe_bridge import EdgeTribeBridge  # noqa: E402
from host_brain.audit.migration_runner import SQLiteMigrationRunner  # noqa: E402
from host_brain.persistence.gateway_transport_repository import (  # noqa: E402
    SQLiteGatewayTransportStore,
)


LOGGER = logging.getLogger("Natsume.gateway.side")
_ALLOWED_PLATFORMS = frozenset(
    {"wechat", "qq", "feishu", "wecom", "telegram", "line"}
)


@dataclass(frozen=True, slots=True)
class GatewaySideArguments:
    bind_host: str
    port: int
    host_url: str
    db_path: Path
    platforms: tuple[str, ...] | None
    stop_file: Path | None
    disable_tribe_events: bool


def _platforms(raw: str | None) -> tuple[str, ...] | None:
    if raw is None:
        return None
    rendered = str(raw).strip()
    if not rendered:
        return None
    if rendered.casefold() in {"none", "off", "disabled"}:
        return ()
    result: list[str] = []
    for item in rendered.split(","):
        value = item.strip().lower()
        if value not in _ALLOWED_PLATFORMS:
            raise ValueError(f"GW_PLATFORM_INVALID:{value or '<empty>'}")
        if value not in result:
            result.append(value)
    return tuple(result)


def parse_args(argv: Sequence[str] | None = None) -> GatewaySideArguments:
    parser = argparse.ArgumentParser(
        description="Start Unified Gateway only; remote Edge services stay stopped.",
    )
    parser.add_argument(
        "--bind-host",
        default=str(os.environ.get("EDGE_GATEWAY_BIND_HOST") or "127.0.0.1"),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(str(os.environ.get("EDGE_GATEWAY_PORT") or "19000")),
    )
    parser.add_argument(
        "--host-url",
        default=str(
            os.environ.get("EDGE_EVENT_REPORT_HOST_URL")
            or "http://127.0.0.1:18080"
        ),
    )
    parser.add_argument(
        "--db-path",
        default=str(
            os.environ.get("EDGE_GATEWAY_TRANSPORT_DB_PATH")
            or os.environ.get("NATSUME_AUDIT_DB")
            or os.environ.get("Natsume_AUDIT_DB")
            or ""
        ),
    )
    parser.add_argument(
        "--platforms",
        default=None,
        help="Comma-separated platform adapters. Use 'none' for reply-only mode.",
    )
    parser.add_argument("--stop-file", default="")
    parser.add_argument("--disable-tribe-events", action="store_true")
    namespace = parser.parse_args(list(argv) if argv is not None else None)

    bind_host = str(namespace.bind_host or "").strip()
    host_url = str(namespace.host_url or "").strip().rstrip("/")
    db_raw = str(namespace.db_path or "").strip()
    port = int(namespace.port)
    if not bind_host:
        raise ValueError("GW_BIND_HOST_REQUIRED")
    if not (1 <= port <= 65_535):
        raise ValueError("GW_PORT_INVALID")
    if not host_url.startswith(("http://", "https://")):
        raise ValueError("GW_HOST_URL_INVALID")
    if not db_raw or db_raw == ":memory:":
        raise ValueError("GW_DURABLE_DB_PATH_REQUIRED")
    db_path = Path(db_raw).expanduser().resolve()
    stop_file = (
        Path(str(namespace.stop_file)).expanduser().resolve()
        if str(namespace.stop_file or "").strip()
        else None
    )
    return GatewaySideArguments(
        bind_host=bind_host,
        port=port,
        host_url=host_url,
        db_path=db_path,
        platforms=_platforms(namespace.platforms),
        stop_file=stop_file,
        disable_tribe_events=bool(namespace.disable_tribe_events),
    )


def build_gateway_config(
    *,
    edge_config: EdgeConfig,
    arguments: GatewaySideArguments,
) -> GatewayConfig:
    enabled_platforms = (
        edge_config.gateway_platforms
        if arguments.platforms is None
        else arguments.platforms
    )
    return GatewayConfig(
        host_url=arguments.host_url,
        hmac_key=edge_config.hmac_shared_key,
        bind_host=arguments.bind_host,
        gateway_port=arguments.port,
        enabled_platforms=tuple(enabled_platforms),
        wx_db_storage_dir=edge_config.wx_db_storage_dir or "",
        wx_decrypt_output_dir=edge_config.wx_decrypt_output_dir or "",
        wx_key_cache_path=edge_config.wx_key_cache_path,
        wx_decrypt_refresh_interval_s=edge_config.wx_decrypt_refresh_interval_s,
        wx_self_wxid=edge_config.wx_self_wxid,
        wx_watermark_file=str(
            arguments.db_path.with_name("wechat_gateway_watermark.json")
        ),
        wx_poll_interval_s=edge_config.wx_poll_interval_s,
        qq_onebot_api_base=edge_config.qq_onebot_api_base,
        qq_self_id=edge_config.qq_self_id,
        feishu_app_id=edge_config.feishu_app_id,
        feishu_app_secret=edge_config.feishu_app_secret,
        feishu_encrypt_key=edge_config.feishu_encrypt_key,
        feishu_verification_token=edge_config.feishu_verification_token,
        wecom_corp_id=edge_config.wecom_corp_id,
        wecom_corp_secret=edge_config.wecom_corp_secret,
        wecom_agent_id=edge_config.wecom_agent_id,
        wecom_token=edge_config.wecom_token,
        wecom_encoding_aes_key=edge_config.wecom_encoding_aes_key,
        telegram_api_id=edge_config.telegram_api_id,
        telegram_api_hash=edge_config.telegram_api_hash,
        telegram_phone=edge_config.telegram_phone,
        telegram_session_name=edge_config.telegram_session_name,
        telegram_self_id=edge_config.telegram_self_id,
        telegram_typing_speed=edge_config.telegram_typing_speed,
        telegram_min_delay_s=edge_config.telegram_min_delay_s,
        telegram_max_delay_s=edge_config.telegram_max_delay_s,
        telegram_proxy_type=edge_config.telegram_proxy_type,
        telegram_proxy_addr=edge_config.telegram_proxy_addr,
        telegram_proxy_port=edge_config.telegram_proxy_port,
        line_channel_access_token=edge_config.line_channel_access_token,
        line_channel_secret=edge_config.line_channel_secret,
    )


def _load_edge_config(env: Mapping[str, Any]) -> EdgeConfig:
    loaded = load_edge_config(env=env)
    if not loaded.success or loaded.config is None:
        reason = str(loaded.reason_code or "GW_EDGE_CONFIG_INVALID")
        fields = sorted(
            {
                str(issue.field)
                for issue in loaded.issues
                if str(issue.field or "").strip()
            }
        )
        suffix = ",".join(fields) if fields else "unknown"
        raise RuntimeError(f"{reason}:fields={suffix}")
    return loaded.config


def _apply_migrations(db_path: Path) -> None:
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"gateway-side-start::{os.getpid()}::{time.time_ns()}",
    )
    if not result.success:
        raise RuntimeError(
            f"{result.reason_code or 'GW_MIGRATION_FAILED'}:{result.detail}"
        )


def _install_signal_handlers(stop_requested: threading.Event) -> None:
    def _request_stop(_signum: int, _frame: object) -> None:
        stop_requested.set()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        signum = getattr(signal, name, None)
        if signum is not None:
            signal.signal(signum, _request_stop)


def _wait_until_stopped(
    *,
    instance: GatewayInstance,
    stop_requested: threading.Event,
    stop_file: Path | None,
) -> None:
    while not stop_requested.wait(0.25):
        if stop_file is not None and stop_file.exists():
            stop_requested.set()
            break
        server_thread = instance.server_thread
        if server_thread is not None and not server_thread.is_alive():
            raise RuntimeError("GW_SERVER_EXITED_UNEXPECTEDLY")


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    instance: GatewayInstance | None = None
    arguments: GatewaySideArguments | None = None
    try:
        arguments = parse_args(argv)
        arguments.db_path.parent.mkdir(parents=True, exist_ok=True)
        if arguments.stop_file is not None:
            arguments.stop_file.parent.mkdir(parents=True, exist_ok=True)
            arguments.stop_file.unlink(missing_ok=True)
        os.environ["EDGE_GATEWAY_TRANSPORT_DB_PATH"] = str(arguments.db_path)
        os.environ["EDGE_EVENT_REPORT_HOST_URL"] = arguments.host_url
        os.environ["EDGE_GATEWAY_PORT"] = str(arguments.port)
        os.environ["EDGE_GATEWAY_TRANSPORT_MODE"] = "DURABLE"

        edge_config = _load_edge_config(os.environ)
        gateway_config = build_gateway_config(
            edge_config=edge_config,
            arguments=arguments,
        )
        _apply_migrations(arguments.db_path)
        tribe_bridge = EdgeTribeBridge(
            host_base_url=arguments.host_url,
            token=str(os.environ.get("HOST_INTERNAL_TRIBE_TOKEN") or "").strip()
            or None,
            enabled=not arguments.disable_tribe_events,
        )
        instance = start_gateway(
            gateway_config,
            tribe_bridge=tribe_bridge,
            transport_store_factory=SQLiteGatewayTransportStore,
        )
        print(
            json.dumps(
                {
                    "status": "started",
                    "pid": os.getpid(),
                    "bind_host": arguments.bind_host,
                    "port": arguments.port,
                    "host_url": arguments.host_url,
                    "platforms": list(gateway_config.enabled_platforms),
                    "transport": "DURABLE_SQLITE",
                    "db_path": str(arguments.db_path),
                    "remote_edge_started": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        stop_requested = threading.Event()
        _install_signal_handlers(stop_requested)
        _wait_until_stopped(
            instance=instance,
            stop_requested=stop_requested,
            stop_file=arguments.stop_file,
        )
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        LOGGER.error("Gateway side failed: %s", exc)
        return 1
    finally:
        if instance is not None:
            try:
                instance.stop()
            except Exception:
                LOGGER.exception("Gateway side shutdown failed")
        if arguments is not None and arguments.stop_file is not None:
            arguments.stop_file.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
