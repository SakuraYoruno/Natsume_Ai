from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.runtime.run_gateway_side import (
    GatewaySideArguments,
    _platforms,
    build_gateway_config,
    parse_args,
)


def _edge_config() -> SimpleNamespace:
    return SimpleNamespace(
        gateway_platforms=("wechat", "qq"),
        hmac_shared_key="webhook-secret",
        wx_db_storage_dir=None,
        wx_decrypt_output_dir=None,
        wx_key_cache_path=None,
        wx_decrypt_refresh_interval_s=3600,
        wx_self_wxid=None,
        wx_poll_interval_s=5.0,
        qq_onebot_api_base="http://127.0.0.1:5700",
        qq_self_id="10001",
        feishu_app_id=None,
        feishu_app_secret=None,
        feishu_encrypt_key=None,
        feishu_verification_token=None,
        wecom_corp_id=None,
        wecom_corp_secret=None,
        wecom_agent_id=None,
        wecom_token=None,
        wecom_encoding_aes_key=None,
        telegram_api_id=None,
        telegram_api_hash=None,
        telegram_phone=None,
        telegram_session_name="nanami",
        telegram_self_id=None,
        telegram_typing_speed=15.0,
        telegram_min_delay_s=1.0,
        telegram_max_delay_s=3.0,
        telegram_proxy_type=None,
        telegram_proxy_addr=None,
        telegram_proxy_port=None,
        line_channel_access_token=None,
        line_channel_secret=None,
    )


def test_platform_override_is_deduplicated_and_rejects_unknown() -> None:
    assert _platforms("qq,qq,telegram") == ("qq", "telegram")
    assert _platforms("none") == ()
    assert _platforms(None) is None
    with pytest.raises(ValueError, match="GW_PLATFORM_INVALID"):
        _platforms("qq,local_fake")


def test_gateway_side_config_keeps_remote_edge_stopped_and_binds_loopback(tmp_path: Path) -> None:
    arguments = GatewaySideArguments(
        bind_host="127.0.0.1",
        port=29000,
        host_url="http://127.0.0.1:28080",
        db_path=tmp_path / "audit.sqlite3",
        platforms=("qq",),
        stop_file=tmp_path / "gateway.stop",
        disable_tribe_events=True,
    )

    config = build_gateway_config(
        edge_config=_edge_config(),  # type: ignore[arg-type]
        arguments=arguments,
    )

    assert config.bind_host == "127.0.0.1"
    assert config.gateway_port == 29000
    assert config.host_url == "http://127.0.0.1:28080"
    assert config.enabled_platforms == ("qq",)
    assert config.qq_onebot_api_base == "http://127.0.0.1:5700"
    assert config.wx_watermark_file == str(tmp_path / "wechat_gateway_watermark.json")


def test_gateway_side_requires_file_backed_sqlite() -> None:
    with pytest.raises(ValueError, match="GW_DURABLE_DB_PATH_REQUIRED"):
        parse_args(["--db-path", ":memory:"])
