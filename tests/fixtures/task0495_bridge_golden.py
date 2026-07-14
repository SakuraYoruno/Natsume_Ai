"""Recording-only Mate and WeChat bridge golden scenario for TASK-0495."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

from tests.fixtures.m48b_isolation import M48BIsolationHarness
from tests.fixtures.m48b_p0_validation import resolve_m48b_owned_state_root
from tools import mateengine_bridge_server as mate_bridge
from tools import wxautox4_bridge as wx_bridge


class _HarnessWx:
    def __init__(self, harness: M48BIsolationHarness) -> None:
        self._harness = harness

    def SendMsg(self, content: str, *, who: str) -> None:
        self._harness.sink.emit(
            "bridge.wechat.send",
            {"target": str(who), "content_bytes": len(content.encode("utf-8"))},
        )


def _mate_runtime(
    *,
    root: Path,
    harness: M48BIsolationHarness,
) -> mate_bridge.BridgeSecurityRuntime:
    config = mate_bridge.build_security_config(
        {
            "NATSUME_BRIDGE_EXPOSURE_MODE": "LOOPBACK",
            "HOST_MATEENGINE_BRIDGE_ENABLED": "true",
            "NATSUME_MATEENGINE_BRIDGE_HMAC_KEY_ID": "task0495.mate.ingress",
            "NATSUME_MATEENGINE_BRIDGE_HMAC_SECRET": harness.test_secret,
            "NATSUME_MATEENGINE_BRIDGE_ALLOWED_PRINCIPALS": harness.principal,
            "NATSUME_MATEENGINE_BRIDGE_ALLOWED_SCOPES": ",".join(
                sorted(mate_bridge.KNOWN_SCOPES)
            ),
            "NATSUME_MATEENGINE_BRIDGE_AUDIT_JSONL_PATH": str(
                root / "mate-audit.jsonl"
            ),
            "NATSUME_MATEENGINE_BRIDGE_REPLAY_LEDGER_PATH": str(
                root / "mate-replay.sqlite3"
            ),
            "NATSUME_MATEENGINE_PLUGIN_HMAC_KEY_ID": "task0495.mate.plugin",
            "NATSUME_MATEENGINE_PLUGIN_HMAC_SECRET": (
                harness.test_secret + ".plugin-distinct"
            ),
            "NATSUME_MATEENGINE_PLUGIN_PRINCIPAL": "test:m48b:mate-plugin",
            "MATEENGINE_BRIDGE_HTTP_HOST": "127.0.0.1",
            "MATEENGINE_BRIDGE_UDP_HOST": "127.0.0.1",
            "MATEENGINE_BRIDGE_UDP_ENABLED": "false",
            "MATEENGINE_SIGNAL_HOST": "127.0.0.1",
            "MATEENGINE_PLUGIN_HOST": "127.0.0.1",
        }
    )
    return mate_bridge.build_bridge_security_runtime(config)


def _line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line)


def run_bridge_golden(
    *,
    state_root: Path,
    harness: M48BIsolationHarness,
    now_ms: int,
) -> dict[str, object]:
    """Execute two authenticated recording effects and reject every replay."""

    if harness.domain != "bridge":
        raise ValueError("TASK0495_BRIDGE_DOMAIN_INVALID")
    root = resolve_m48b_owned_state_root(
        harness=harness,
        state_root=state_root,
    )
    effect_count_before = len(harness.sink.effects)

    mate_runtime = _mate_runtime(root=root, harness=harness)
    mate_body = b'{"type":"say","text":"task0495-recording-only"}'
    mate_metadata: dict[str, Any] = {
        "version": mate_bridge.AUTH_VERSION,
        "key_id": mate_runtime.config.key_id,
        "timestamp_ms": now_ms,
        "nonce": "task0495-mate-nonce-0001",
        "principal": harness.principal,
        "scope": mate_bridge.SCOPE_SAY,
    }
    mate_metadata["signature"] = mate_bridge.build_hmac_signature(
        mate_runtime.config.hmac_secret,
        version=str(mate_metadata["version"]),
        key_id=str(mate_metadata["key_id"]),
        transport="http",
        method="POST",
        path="/command",
        timestamp_ms=int(mate_metadata["timestamp_ms"]),
        nonce=str(mate_metadata["nonce"]),
        principal=str(mate_metadata["principal"]),
        scope=str(mate_metadata["scope"]),
        body_digest=mate_bridge.body_sha256(mate_body),
    )

    def _record_mate(_payload: dict[str, Any]) -> dict[str, bool]:
        harness.sink.emit(
            "bridge.mate.say",
            {"principal": harness.principal},
        )
        return {"success": True}

    mate_first = mate_bridge.process_command_request(
        raw_body=mate_body,
        metadata=mate_metadata,
        transport="http",
        method="POST",
        path="/command",
        remote_host="127.0.0.1",
        runtime=mate_runtime,
        command_handler=_record_mate,
        now_ms=now_ms,
        now_monotonic=10.0,
    )
    assert mate_first.status == 200

    mate_replay = _mate_runtime(root=root, harness=harness).authorize(
        transport="http",
        method="POST",
        path="/command",
        remote_host="127.0.0.1",
        raw_body=mate_body,
        metadata=mate_metadata,
        required_scopes=(mate_bridge.SCOPE_SAY,),
        now_ms=now_ms,
        now_monotonic=11.0,
    )
    assert mate_replay.allowed is False
    assert mate_replay.reason == "replay_detected"

    mate_bad_metadata = dict(mate_metadata)
    mate_bad_metadata["nonce"] = "task0495-mate-nonce-tampered"
    mate_tampered = mate_runtime.authorize(
        transport="http",
        method="POST",
        path="/command",
        remote_host="127.0.0.1",
        raw_body=mate_body,
        metadata=mate_bad_metadata,
        required_scopes=(mate_bridge.SCOPE_SAY,),
        now_ms=now_ms,
        now_monotonic=12.0,
    )
    assert mate_tampered.allowed is False
    assert mate_tampered.reason == "invalid_signature"

    wx_root = root / "wechat"
    wx_root.mkdir(parents=True, exist_ok=True)
    queue_path = wx_root / "commands.jsonl"
    result_path = wx_root / "results.jsonl"
    wx_secret = harness.test_secret + ".wechat"
    callback_secret = harness.test_secret + ".callback-distinct"
    wx_config = wx_bridge.load_bridge_exposure_config(
        edge_url="http://127.0.0.1:19000",
        command_queue_file=str(queue_path),
        command_result_file=str(result_path),
        environ={
            wx_bridge.BRIDGE_EXPOSURE_MODE_ENV: wx_bridge.BRIDGE_MODE_LOOPBACK,
            wx_bridge.LEGACY_BRIDGE_ENABLED_ENV: "true",
            wx_bridge.BRIDGE_HMAC_KEY_ID_ENV: "task0495.wechat.key",
            wx_bridge.BRIDGE_HMAC_SECRET_ENV: wx_secret,
            wx_bridge.BRIDGE_CALLBACK_HMAC_SECRET_ENV: callback_secret,
            wx_bridge.BRIDGE_PRINCIPAL_ENV: harness.principal,
            wx_bridge.BRIDGE_ALLOWED_SCOPES_ENV: wx_bridge.BRIDGE_REQUIRED_SCOPE,
            wx_bridge.BRIDGE_RATE_PER_MINUTE_ENV: "60",
            wx_bridge.BRIDGE_RATE_BURST_ENV: "4",
            wx_bridge.BRIDGE_COMMAND_TTL_MS_ENV: "60000",
            wx_bridge.BRIDGE_LEDGER_PATH_ENV: str(wx_root / "ledger.sqlite3"),
            wx_bridge.BRIDGE_AUDIT_PATH_ENV: str(wx_root / "audit.jsonl"),
        },
    )
    assert wx_config.enabled is True
    command = wx_bridge.sign_bridge_command_row(
        {
            "command_id": "task0495.wechat.command.1",
            "request_id": "task0495.wechat.request.1",
            "action": "send_text",
            "target": "Recording Contact",
            "content": "task0495-recording-only",
        },
        secret=wx_secret,
        key_id=wx_config.key_id,
        principal=wx_config.principal_id,
        timestamp_ms=now_ms,
        nonce="task0495-wechat-nonce-0001",
    )
    wx = _HarnessWx(harness)
    for _ in range(2):
        authority = wx_bridge.BridgeCommandAuthority(
            wx_config,
            clock_ms=lambda: now_ms,
        )
        wx_bridge.OutboundProcessor(
            wx,
            command_queue_file=str(queue_path),
            command_result_file=str(result_path),
            authority=authority,
        )._execute_command(command)  # noqa: SLF001 - recording worker seam

    tampered_command = wx_bridge.sign_bridge_command_row(
        {
            "command_id": "task0495.wechat.command.bad",
            "request_id": "task0495.wechat.request.bad",
            "action": "send_text",
            "target": "Recording Contact",
            "content": "must-not-run",
        },
        secret=wx_secret,
        key_id=wx_config.key_id,
        principal=wx_config.principal_id,
        timestamp_ms=now_ms,
        nonce="task0495-wechat-nonce-bad",
    )
    tampered_command["hmac_sha256"] = "0" * 64
    wx_bridge.OutboundProcessor(
        wx,
        command_queue_file=str(queue_path),
        command_result_file=str(result_path),
        authority=wx_bridge.BridgeCommandAuthority(
            wx_config,
            clock_ms=lambda: now_ms,
        ),
    )._execute_command(tampered_command)  # noqa: SLF001 - recording worker seam

    result_rows = [
        json.loads(line)
        for line in result_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert result_rows[-2]["reason_code"] == wx_bridge.BRIDGE_REASON_REPLAYED
    assert (
        result_rows[-1]["reason_code"]
        == wx_bridge.BRIDGE_REASON_SIGNATURE_INVALID
    )

    effect_count = len(harness.sink.effects) - effect_count_before
    assert effect_count == 2
    with sqlite3.connect(str(root / "mate-replay.sqlite3")) as conn:
        mate_claims = int(conn.execute(
            "SELECT COUNT(*) FROM mateengine_nonce_claim",
        ).fetchone()[0])
    with sqlite3.connect(str(wx_root / "ledger.sqlite3")) as conn:
        wx_claims = int(conn.execute(
            "SELECT COUNT(*) FROM bridge_command_claim",
        ).fetchone()[0])
    audit_count = _line_count(root / "mate-audit.jsonl") + _line_count(
        wx_root / "audit.jsonl"
    )
    assert mate_claims == 1
    assert wx_claims == 1
    # Two Mate runtime constructions each emit an audit_ready record, the
    # accepted command emits accepted/completed, and WeChat emits allow,
    # replay and tamper decisions.
    assert audit_count == 7

    projection: dict[str, object] = {
        "status": "COMPLETED",
        "reason_code": "BRIDGE_GOLDEN_COMPLETED",
        "effect_count": effect_count,
        "durable_claim_count": mate_claims + wx_claims,
        "audit_count": audit_count,
        "uncertain": False,
    }
    assert harness.test_secret not in json.dumps(projection, sort_keys=True)
    return projection


__all__ = ["run_bridge_golden"]
