"""Verify durable Gateway outbox recovery over a real loopback HTTP socket.

The acceptance has two deliberately different boundaries:

* adapter selection fails before dispatch starts, so the frozen outbox may be
  retried and then delivered exactly once when a OneBot peer becomes available;
* the OneBot peer receives a request but drops the HTTP response, so the
  started external effect is quarantined and is never blindly replayed.

The loopback peer proves local protocol and persistence behaviour only.  It is
not a QQ client and its response is not physical-platform delivery evidence.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from edge_client.gateway.adapters.qq_adapter import QQAdapter
from edge_client.gateway.delivery_runtime import (
    GatewayDeliveryRuntime,
    GatewayTransportMode,
)
from edge_client.gateway.outbound import OutboundDispatcher
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.gateway_transport_repository import (
    GatewayTransportStoreError,
    SQLiteGatewayTransportStore,
)


ACCEPTANCE_SCHEMA_VERSION = "natsume.gateway-outbox-network-recovery.v1"
MIGRATIONS = REPOSITORY_ROOT / "host_brain" / "audit" / "migrations"


class _OneBotPeerState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mode = "success"
        self._request_digests: list[str] = []
        self._paths: list[str] = []

    def set_mode(self, mode: str) -> None:
        if mode not in {"success", "drop_response"}:
            raise ValueError("unsupported OneBot peer mode")
        with self._lock:
            self._mode = mode

    def record(self, *, path: str, body: bytes) -> str:
        digest = hashlib.sha256(body).hexdigest()
        with self._lock:
            self._request_digests.append(digest)
            self._paths.append(str(path))
            return self._mode

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "request_count": len(self._request_digests),
                "request_sha256": list(self._request_digests),
                "paths": list(self._paths),
                "mode": self._mode,
            }


class _OneBotHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "NatsumeAcceptanceOneBot/1.0"

    def do_POST(self) -> None:  # noqa: N802
        size = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(size)
        state = getattr(self.server, "peer_state")
        mode = state.record(path=self.path, body=body)
        if mode == "drop_response":
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
            return
        response = json.dumps(
            {
                "status": "ok",
                "retcode": 0,
                "data": {"message_id": "loopback-onebot-accepted"},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


class _OneBotPeer:
    def __init__(self) -> None:
        self.state = _OneBotPeerState()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _OneBotHandler)
        self.server.daemon_threads = True
        setattr(self.server, "peer_state", self.state)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="a15-loopback-onebot",
            daemon=True,
        )

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5.0)
        if self.thread.is_alive():
            raise RuntimeError("ONEBOT_LOOPBACK_PEER_STOP_TIMEOUT")


def _commit_sha(repository_root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    value = completed.stdout.strip().lower()
    if len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
        raise RuntimeError("ACCEPTANCE_COMMIT_SHA_INVALID")
    return value


def _migrate(db_path: Path, *, run_id: str) -> tuple[str, int]:
    result = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=MIGRATIONS,
    ).apply_migrations(
        request_id=f"gateway-outbox-network-recovery::{run_id}",
        ts_ms=int(time.time() * 1000),
    )
    if not result.success:
        raise RuntimeError("ACCEPTANCE_MIGRATION_FAILED")
    summary = dict(result.summary or {})
    current = int(summary.get("current_version") or 0)
    latest = int(summary.get("latest_version") or 0)
    if current < 1 or current != latest:
        raise RuntimeError("ACCEPTANCE_MIGRATION_HEAD_NOT_CURRENT")
    prefix = f"{latest:04d}_"
    names = sorted(
        str(record.migration_id)
        for record in tuple(result.records or ())
        if str(record.migration_id).startswith(prefix)
    )
    if not names:
        raise RuntimeError("ACCEPTANCE_MIGRATION_HEAD_ASSET_MISSING")
    return names[-1] + ".sql", latest


def _wait_for_row(
    store: SQLiteGatewayTransportStore,
    *,
    outbox_id: str,
    predicate: Callable[[dict[str, Any]], bool],
    timeout_s: float = 8.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = store.get_outbox(outbox_id=outbox_id)
        if predicate(last):
            return last
        time.sleep(0.02)
    raise RuntimeError(
        "ACCEPTANCE_OUTBOX_WAIT_TIMEOUT:"
        f"{outbox_id}:{(last or {}).get('status')}"
    )


def _payload(*, content: str, probe_id: str) -> dict[str, Any]:
    return {
        "platform": "qq",
        "conversation_id": "qq_dm_150001",
        "msg_type": "text",
        "content": content,
        "raw_extra": {"acceptance_probe_id": probe_id},
    }


def _runtime(
    *,
    store: SQLiteGatewayTransportStore,
    outbound: OutboundDispatcher,
    worker_id: str,
) -> GatewayDeliveryRuntime:
    return GatewayDeliveryRuntime(
        mode=GatewayTransportMode.DURABLE,
        store=store,
        reporter=None,
        outbound=outbound,
        worker_interval_s=0.02,
        lease_ms=2_000,
        worker_id=worker_id,
    )


def _attempts(db_path: Path, *, outbox_id: str) -> list[dict[str, Any]]:
    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT attempt_no, replay_no, fencing_token, outcome, reason_code,
                   dispatch_started_at_ms IS NOT NULL AS dispatch_started
            FROM gateway_transport_attempt
            WHERE direction = 'OUTBOX' AND message_id = ?
            ORDER BY attempt_no, started_at_ms
            """,
            (outbox_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _safe_pre_dispatch_recovery(
    *,
    db_path: Path,
    peer: _OneBotPeer,
    run_id: str,
) -> dict[str, Any]:
    store = SQLiteGatewayTransportStore(db_path)
    outbound = OutboundDispatcher()
    runtime = _runtime(
        store=store,
        outbound=outbound,
        worker_id=f"{run_id}:safe:first",
    )
    runtime.start()
    payload = _payload(content="A15 safe retry", probe_id="safe-pre-dispatch")
    try:
        receipt = runtime.enqueue_reply(
            tenant_id="acceptance.local",
            principal_id="gateway.acceptance",
            idempotency_key=f"{run_id}:safe",
            payload=payload,
            ordering_key="qq:acceptance:safe",
            max_attempts=3,
        )
        first = _wait_for_row(
            store,
            outbox_id=receipt.record_id,
            predicate=lambda row: (
                row["status"] == "RETRY_WAIT"
                and row["attempt_count"] == 1
                and row["last_reason_code"] == "GW_REPLY_ADAPTER_UNAVAILABLE"
            ),
        )
        if peer.state.snapshot()["request_count"] != 0:
            raise RuntimeError("PREFLIGHT_FAILURE_REACHED_NETWORK")
        outbound.register(
            QQAdapter(peer.base_url, self_id="9000", http_timeout_s=1.0)
        )
        _wait_for_row(
            store,
            outbox_id=receipt.record_id,
            predicate=lambda row: row["status"] == "ACKED",
        )
        after_ack = peer.state.snapshot()
        if after_ack["request_count"] != 1:
            raise RuntimeError("SAFE_RECOVERY_WAS_NOT_EXACTLY_ONCE")
        duplicate = runtime.enqueue_reply(
            tenant_id="acceptance.local",
            principal_id="gateway.acceptance",
            idempotency_key=f"{run_id}:safe",
            payload=payload,
            ordering_key="qq:acceptance:safe",
            max_attempts=3,
        )
        time.sleep(0.15)
        if peer.state.snapshot()["request_count"] != 1:
            raise RuntimeError("SAFE_DUPLICATE_WAS_REDISPATCHED")
    finally:
        runtime.stop()

    restarted_store = SQLiteGatewayTransportStore(db_path)
    restarted_outbound = OutboundDispatcher()
    restarted_outbound.register(
        QQAdapter(peer.base_url, self_id="9000", http_timeout_s=1.0)
    )
    restarted = _runtime(
        store=restarted_store,
        outbound=restarted_outbound,
        worker_id=f"{run_id}:safe:restart",
    )
    restarted.start()
    try:
        restart_duplicate = restarted.enqueue_reply(
            tenant_id="acceptance.local",
            principal_id="gateway.acceptance",
            idempotency_key=f"{run_id}:safe",
            payload=payload,
            ordering_key="qq:acceptance:safe",
            max_attempts=3,
        )
        time.sleep(0.2)
        final_peer = peer.state.snapshot()
        final = restarted_store.get_outbox(outbox_id=receipt.record_id)
        if final_peer["request_count"] != 1 or final["status"] != "ACKED":
            raise RuntimeError("SAFE_RESTART_DUPLICATE_WAS_REDISPATCHED")
    finally:
        restarted.stop()

    return {
        "status": "PASS",
        "outbox_id": receipt.record_id,
        "pre_dispatch_status": first["status"],
        "pre_dispatch_reason_code": first["last_reason_code"],
        "pre_dispatch_network_request_count": 0,
        "final_status": final["status"],
        "attempt_count": final["attempt_count"],
        "replay_count": final["replay_count"],
        "adapter_confirmation_digest": final["ack_digest"],
        "duplicate_acceptance": duplicate.status,
        "restart_duplicate_acceptance": restart_duplicate.status,
        "loopback_http_request_count": final_peer["request_count"],
        "loopback_request_sha256": final_peer["request_sha256"],
        "attempts": _attempts(db_path, outbox_id=receipt.record_id),
    }


def _response_lost_fail_closed(
    *,
    db_path: Path,
    peer: _OneBotPeer,
    run_id: str,
) -> dict[str, Any]:
    before = peer.state.snapshot()["request_count"]
    peer.state.set_mode("drop_response")
    store = SQLiteGatewayTransportStore(db_path)
    outbound = OutboundDispatcher()
    outbound.register(QQAdapter(peer.base_url, self_id="9000", http_timeout_s=1.0))
    runtime = _runtime(
        store=store,
        outbound=outbound,
        worker_id=f"{run_id}:uncertain:first",
    )
    runtime.start()
    payload = _payload(content="A15 lost response", probe_id="response-lost")
    try:
        receipt = runtime.enqueue_reply(
            tenant_id="acceptance.local",
            principal_id="gateway.acceptance",
            idempotency_key=f"{run_id}:uncertain",
            payload=payload,
            ordering_key="qq:acceptance:uncertain",
            max_attempts=3,
        )
        _wait_for_row(
            store,
            outbox_id=receipt.record_id,
            predicate=lambda row: (
                row["status"] == "DLQ"
                and row["last_reason_code"]
                == "GATEWAY_TRANSPORT_DELIVERY_UNCERTAIN"
            ),
        )
        after_drop = peer.state.snapshot()
        if after_drop["request_count"] - before != 1:
            raise RuntimeError("UNCERTAIN_DELIVERY_REQUEST_COUNT_INVALID")
        duplicate = runtime.enqueue_reply(
            tenant_id="acceptance.local",
            principal_id="gateway.acceptance",
            idempotency_key=f"{run_id}:uncertain",
            payload=payload,
            ordering_key="qq:acceptance:uncertain",
            max_attempts=3,
        )
        time.sleep(0.2)
        if peer.state.snapshot()["request_count"] != after_drop["request_count"]:
            raise RuntimeError("UNCERTAIN_DUPLICATE_WAS_REDISPATCHED")
    finally:
        runtime.stop()

    peer.state.set_mode("success")
    restarted_store = SQLiteGatewayTransportStore(db_path)
    restarted_outbound = OutboundDispatcher()
    restarted_outbound.register(
        QQAdapter(peer.base_url, self_id="9000", http_timeout_s=1.0)
    )
    restarted = _runtime(
        store=restarted_store,
        outbound=restarted_outbound,
        worker_id=f"{run_id}:uncertain:restart",
    )
    restarted.start()
    try:
        restart_duplicate = restarted.enqueue_reply(
            tenant_id="acceptance.local",
            principal_id="gateway.acceptance",
            idempotency_key=f"{run_id}:uncertain",
            payload=payload,
            ordering_key="qq:acceptance:uncertain",
            max_attempts=3,
        )
        time.sleep(0.25)
        final_peer = peer.state.snapshot()
        final = restarted_store.get_outbox(outbox_id=receipt.record_id)
        if final_peer["request_count"] != after_drop["request_count"]:
            raise RuntimeError("UNCERTAIN_RESTART_WAS_REDISPATCHED")
        try:
            restarted_store.replay_outbox(
                outbox_id=receipt.record_id,
                operator_ref="operator://a15/plain-replay-forbidden",
            )
        except GatewayTransportStoreError as exc:
            replay_reason = exc.reason_code
        else:
            raise RuntimeError("UNCERTAIN_PLAIN_REPLAY_WAS_ALLOWED")
        if replay_reason != "GATEWAY_TRANSPORT_REPLAY_REQUIRES_RECONCILIATION":
            raise RuntimeError("UNCERTAIN_REPLAY_REASON_INVALID")
        after_replay_rejection = restarted_store.get_outbox(
            outbox_id=receipt.record_id
        )
    finally:
        restarted.stop()

    return {
        "status": "PASS_FAIL_CLOSED",
        "outbox_id": receipt.record_id,
        "final_status": final["status"],
        "last_reason_code": final["last_reason_code"],
        "attempt_count": final["attempt_count"],
        "replay_count": after_replay_rejection["replay_count"],
        "plain_replay_reason_code": replay_reason,
        "duplicate_acceptance": duplicate.status,
        "restart_duplicate_acceptance": restart_duplicate.status,
        "loopback_http_request_count": final_peer["request_count"] - before,
        "loopback_request_sha256": final_peer["request_sha256"][before:],
        "adapter_or_platform_ack_attested": False,
        "attempts": _attempts(db_path, outbox_id=receipt.record_id),
    }


def run_acceptance(*, repository_root: Path, run_id: str) -> dict[str, Any]:
    # httpx honours system proxy variables.  The acceptance peer must remain a
    # direct local socket even on developer machines configured with a proxy.
    proxy_env = {name: os.environ.get(name) for name in ("NO_PROXY", "no_proxy")}
    for name in proxy_env:
        current = str(proxy_env[name] or "").strip()
        parts = [item.strip() for item in current.split(",") if item.strip()]
        for loopback in ("127.0.0.1", "localhost"):
            if loopback not in parts:
                parts.append(loopback)
        os.environ[name] = ",".join(parts)

    peer = _OneBotPeer()
    peer.start()
    try:
        with tempfile.TemporaryDirectory(prefix="natsume-gateway-outbox-network-") as raw:
            root = Path(raw).resolve()
            db_path = root / "gateway.sqlite3"
            migration_head, migration_head_version = _migrate(
                db_path,
                run_id=run_id,
            )
            safe = _safe_pre_dispatch_recovery(
                db_path=db_path,
                peer=peer,
                run_id=run_id,
            )
            uncertain = _response_lost_fail_closed(
                db_path=db_path,
                peer=peer,
                run_id=run_id,
            )
            store = SQLiteGatewayTransportStore(db_path)
            counts = store.transport_counts()
            with closing(sqlite3.connect(db_path)) as connection:
                integrity = str(
                    connection.execute("PRAGMA integrity_check").fetchone()[0]
                )
                outbox_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM gateway_transport_outbox"
                    ).fetchone()[0]
                )
            if integrity != "ok" or outbox_count != 2:
                raise RuntimeError("ACCEPTANCE_DURABLE_STATE_INVALID")
            receipt = {
                "schema_version": ACCEPTANCE_SCHEMA_VERSION,
                "run_id": str(run_id).strip(),
                "commit_sha": _commit_sha(repository_root),
                "database_kind": "ISOLATED_REAL_SQLITE",
                "migration_head": migration_head,
                "migration_head_version": migration_head_version,
                "protocol_peer": {
                    "kind": "LOOPBACK_ONEBOT_V11_HTTP_REAL_SOCKET",
                    "base_url": peer.base_url,
                    "physical_platform_client": False,
                },
                "safe_pre_dispatch_recovery": safe,
                "response_lost_unknown_effect": uncertain,
                "durable_state": {
                    "status": "PASS",
                    "integrity_check": integrity,
                    "outbox_count": outbox_count,
                    "transport_counts": counts,
                    "database_sha256": hashlib.sha256(db_path.read_bytes()).hexdigest(),
                },
                "physical_platform_delivery": (
                    "NOT_TESTED_PLATFORM_CLIENT_UNAVAILABLE"
                ),
                "local_dlq_claimed_as_physical_delivery": False,
                "private_or_user_data_used": False,
                "temporary_database_removed": True,
                "remote_ci": "NOT_RUN_ACCOUNT_QUOTA",
                "status": "PASS_LOCAL_PROTOCOL_RECOVERY_AND_FAIL_CLOSED",
            }
    finally:
        peer.stop()
        for name, value in proxy_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run durable Gateway outbox network recovery acceptance",
    )
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--receipt", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    receipt = run_acceptance(
        repository_root=args.repository_root.resolve(),
        run_id=args.run_id,
    )
    encoded = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True)
    if args.receipt is not None:
        target = args.receipt.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
