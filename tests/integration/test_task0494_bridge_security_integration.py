"""TASK-0494 bridge security integration without listeners or real devices.

The three bridge owners run against temporary durable stores and recording
effect adapters.  No socket, Electron window, MateEngine process or WeChat
provider is started.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from tools import mateengine_bridge_server as mate_bridge
from tools import wxautox4_bridge as wx_bridge


ROOT = Path(__file__).resolve().parents[2]
NOW_MS = 1_900_000_000_000

MATE_SECRET = "task0494-mate-integration-secret-00000001"
MATE_PLUGIN_SECRET = "task0494-mate-plugin-secret-00000002"
WX_SECRET = "task0494-wechat-integration-secret-00000003"
WX_CALLBACK_SECRET = "task0494-wechat-callback-secret-00000004"
DESKTOP_SECRET = "task0494-desktop-integration-secret-00000005"


class _RecordingWx:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def SendMsg(self, content: str, *, who: str) -> None:
        self.calls.append((content, who))


def _mate_runtime(tmp_path: Path) -> mate_bridge.BridgeSecurityRuntime:
    config = mate_bridge.build_security_config(
        {
            "NATSUME_BRIDGE_EXPOSURE_MODE": "LOOPBACK",
            "HOST_MATEENGINE_BRIDGE_ENABLED": "true",
            "NATSUME_MATEENGINE_BRIDGE_HMAC_KEY_ID": "mate.integration.ingress",
            "NATSUME_MATEENGINE_BRIDGE_HMAC_SECRET": MATE_SECRET,
            "NATSUME_MATEENGINE_BRIDGE_ALLOWED_PRINCIPALS": "host.integration",
            "NATSUME_MATEENGINE_BRIDGE_ALLOWED_SCOPES": ",".join(
                sorted(mate_bridge.KNOWN_SCOPES)
            ),
            "NATSUME_MATEENGINE_BRIDGE_AUDIT_JSONL_PATH": str(
                tmp_path / "mate-audit.jsonl"
            ),
            "NATSUME_MATEENGINE_BRIDGE_REPLAY_LEDGER_PATH": str(
                tmp_path / "mate-replay.sqlite3"
            ),
            "NATSUME_MATEENGINE_PLUGIN_HMAC_KEY_ID": "mate.integration.plugin",
            "NATSUME_MATEENGINE_PLUGIN_HMAC_SECRET": MATE_PLUGIN_SECRET,
            "NATSUME_MATEENGINE_PLUGIN_PRINCIPAL": "host.mateengine.bridge",
            "MATEENGINE_BRIDGE_HTTP_HOST": "127.0.0.1",
            "MATEENGINE_BRIDGE_UDP_HOST": "127.0.0.1",
            "MATEENGINE_BRIDGE_UDP_ENABLED": "false",
            "MATEENGINE_SIGNAL_HOST": "127.0.0.1",
            "MATEENGINE_PLUGIN_HOST": "127.0.0.1",
        }
    )
    return mate_bridge.BridgeSecurityRuntime(config)


def _exercise_mate(tmp_path: Path) -> None:
    runtime = _mate_runtime(tmp_path)
    raw_body = b'{"type":"say","text":"integration-only"}'
    metadata = {
        "version": mate_bridge.AUTH_VERSION,
        "key_id": runtime.config.key_id,
        "timestamp_ms": NOW_MS,
        "nonce": "nonce-mate-integration-0001",
        "principal": "host.integration",
        "scope": mate_bridge.SCOPE_SAY,
    }
    metadata["signature"] = mate_bridge.build_hmac_signature(
        runtime.config.hmac_secret,
        version=metadata["version"],
        key_id=metadata["key_id"],
        transport="http",
        method="POST",
        path="/command",
        timestamp_ms=NOW_MS,
        nonce=metadata["nonce"],
        principal=metadata["principal"],
        scope=metadata["scope"],
        body_digest=mate_bridge.body_sha256(raw_body),
    )
    effects: list[dict[str, object]] = []
    first = mate_bridge.process_command_request(
        raw_body=raw_body,
        metadata=metadata,
        transport="http",
        method="POST",
        path="/command",
        remote_host="127.0.0.1",
        runtime=runtime,
        command_handler=lambda payload: effects.append(payload) or {"success": True},
        now_ms=NOW_MS,
        now_monotonic=10.0,
    )
    replay = _mate_runtime(tmp_path).authorize(
        transport="http",
        method="POST",
        path="/command",
        remote_host="127.0.0.1",
        raw_body=raw_body,
        metadata=metadata,
        required_scopes=(mate_bridge.SCOPE_SAY,),
        now_ms=NOW_MS,
        now_monotonic=11.0,
    )

    assert first.status == 200
    assert [effect["type"] for effect in effects] == ["say"]
    assert replay.allowed is False
    assert replay.reason == "replay_detected"


def _exercise_wechat(tmp_path: Path) -> None:
    queue_path = tmp_path / "wechat-commands.jsonl"
    result_path = tmp_path / "wechat-results.jsonl"
    config = wx_bridge.load_bridge_exposure_config(
        edge_url="http://127.0.0.1:19000",
        command_queue_file=str(queue_path),
        command_result_file=str(result_path),
        environ={
            wx_bridge.BRIDGE_EXPOSURE_MODE_ENV: wx_bridge.BRIDGE_MODE_LOOPBACK,
            wx_bridge.LEGACY_BRIDGE_ENABLED_ENV: "true",
            wx_bridge.BRIDGE_HMAC_KEY_ID_ENV: "wechat.integration.key",
            wx_bridge.BRIDGE_HMAC_SECRET_ENV: WX_SECRET,
            wx_bridge.BRIDGE_CALLBACK_HMAC_SECRET_ENV: WX_CALLBACK_SECRET,
            wx_bridge.BRIDGE_PRINCIPAL_ENV: "gateway.integration",
            wx_bridge.BRIDGE_ALLOWED_SCOPES_ENV: wx_bridge.BRIDGE_REQUIRED_SCOPE,
            wx_bridge.BRIDGE_RATE_PER_MINUTE_ENV: "60",
            wx_bridge.BRIDGE_RATE_BURST_ENV: "4",
            wx_bridge.BRIDGE_COMMAND_TTL_MS_ENV: "60000",
            wx_bridge.BRIDGE_LEDGER_PATH_ENV: str(tmp_path / "wechat-ledger.sqlite3"),
            wx_bridge.BRIDGE_AUDIT_PATH_ENV: str(tmp_path / "wechat-audit.jsonl"),
        },
    )
    assert config.enabled is True
    command = wx_bridge.sign_bridge_command_row(
        {
            "command_id": "wechat.integration.command.1",
            "request_id": "wechat.integration.request.1",
            "action": "send_text",
            "target": "Recording Contact",
            "content": "integration-only",
        },
        secret=WX_SECRET,
        key_id=config.key_id,
        principal=config.principal_id,
        timestamp_ms=NOW_MS,
        nonce="nonce-wechat-integration-0001",
    )
    wx = _RecordingWx()

    for _ in range(2):
        authority = wx_bridge.BridgeCommandAuthority(config, clock_ms=lambda: NOW_MS)
        wx_bridge.OutboundProcessor(
            wx,
            command_queue_file=str(queue_path),
            command_result_file=str(result_path),
            authority=authority,
        )._execute_command(command)

    assert wx.calls == [("integration-only", "Recording Contact")]
    result_rows = [
        json.loads(line)
        for line in result_path.read_text(encoding="utf-8").splitlines()
    ]
    assert result_rows[-1]["reason_code"] == wx_bridge.BRIDGE_REASON_REPLAYED


def _exercise_desktop(tmp_path: Path) -> None:
    audit_path = tmp_path / "desktop-audit.jsonl"
    replay_path = tmp_path / "desktop-replay.jsonl"
    env = os.environ.copy()
    env.update(
        {
            "NATSUME_BRIDGE_EXPOSURE_MODE": "LOOPBACK",
            "NATSUME_DESKTOP_PET_BRIDGE_ENABLED": "true",
            "NATSUME_DESKTOP_PET_HOST": "127.0.0.1",
            "NATSUME_DESKTOP_PET_HMAC_KEY_ID": "desktop.integration.key",
            "NATSUME_DESKTOP_PET_HMAC_SECRET": DESKTOP_SECRET,
            "NATSUME_DESKTOP_PET_ALLOWED_PRINCIPAL": "desktop.integration.principal",
            "NATSUME_DESKTOP_PET_ALLOWED_SCOPES": (
                "bridge:desktop-pet:action,bridge:desktop-pet:read"
            ),
            "NATSUME_DESKTOP_PET_AUDIT_JSONL_PATH": str(audit_path),
            "NATSUME_DESKTOP_PET_REPLAY_LEDGER_PATH": str(replay_path),
        }
    )
    probe = subprocess.run(
        [
            "node",
            "-e",
            r"""
const security = require("./desktop_pet/bridge_security.cjs");
const nowMs = 1900000000000;
const config = security.resolveBridgeConfig(process.env);
if (!config.valid || !config.listenerEnabled) throw new Error(config.errors.join(","));
const rawBody = Buffer.from('{"action":"wave"}', "utf8");
const headers = security.buildSignedHeaders({
  secret: process.env.NATSUME_DESKTOP_PET_HMAC_SECRET,
  keyId: config.keyId,
  method: "POST",
  requestPath: "/action",
  timestampMs: nowMs,
  nonce: "nonce-desktop-integration-0001",
  principal: config.allowedPrincipals[0],
  scope: security.ACTION_SCOPE,
  rawBody,
});
const rawHeaders = Object.entries(headers).flat();
const request = {
  method: "POST",
  requestPath: "/action",
  rawHeaders,
  rawBody,
  peer: "127.0.0.1",
  requiredScope: security.ACTION_SCOPE,
};
const accepted = security.createBridgeSecurity(config, { clock: () => nowMs }).authorize(request);
let replayReason = "";
try {
  security.createBridgeSecurity(config, { clock: () => nowMs }).authorize(request);
} catch (error) {
  replayReason = error.reasonCode || "";
}
process.stdout.write(JSON.stringify({ principal: accepted.principal, replayReason }));
""",
        ],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    result = json.loads(probe.stdout)
    assert result == {
        "principal": "desktop.integration.principal",
        "replayReason": "BRIDGE_REPLAY_DETECTED",
    }
    audit_text = audit_path.read_text(encoding="utf-8")
    assert DESKTOP_SECRET not in audit_text
    assert "wave" not in audit_text


def test_signed_bridge_effects_are_one_shot_across_all_three_owners(
    tmp_path: Path,
) -> None:
    _exercise_mate(tmp_path / "mate")
    _exercise_desktop(tmp_path / "desktop")
    _exercise_wechat(tmp_path / "wechat")
