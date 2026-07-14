"""wxautox4 Bridge for Natsume_Ai.

Uses the ``wxautox4`` Python package variant and exposes the current
Gateway bridge protocol:

  Inbound  → HTTP POST to Gateway webhook  ``/v1/gateway/wechat/webhook``
  Outbound → poll JSONL command queue → execute wxautox4 API → write result

Select this bridge with:

    EDGE_WECHAT_BRIDGE_BACKEND=wxautox4

Requires:
  - wxautox4 activated (``wxautox4 -a <activation_code>`` or vendor wheel equivalent)
  - WeChat 4.1+ running and logged-in on the same desktop

Phase 1 additions (2026-03-27):
  - IsOnline() health check with periodic keepalive
  - GetMyInfo() self-info at startup
  - get_friends / get_groups / get_recent_chats query actions
  - get_history_messages query action
  - media download on inbound messages (image/video/voice)
  - RemoveListenChat / StopListening management actions
  - ChatWith navigation action

Phase 2 additions (2026-03-27):
  - GetSubWindow / GetAllSubWindow window management
  - ChatInfo / GetDialog session & popup detection
  - SendUrlCard link card sending
  - GetAllMessage / GetNewFriends / GetFriendDetails
  - LinkMessage.get_url() inbound link URL extraction

Phase 3 additions (2026-03-27):
  - PublishMoment / GetMoments / Like / Comment — moments (朋友圈)
  - AddGroupMembers — group management
  - Shared message-object cache (InboundProcessor → OutboundProcessor, TTL 5min)
  - capture_emotion (EmotionMessage.capture)
  - download_quote_image (QuoteMessage.download_quote_image)
  - delete_message (HumanMessage.delete)
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Any, Mapping

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from natsume_contracts.bridge_auth import build_hmac_digest

logger = logging.getLogger("Natsume.wxautox4_bridge")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_EDGE_URL = "http://127.0.0.1:19000"
CALLBACK_PATH = "/v1/gateway/wechat/webhook"
DEFAULT_COMMAND_POLL_INTERVAL_S = 0.5
DEFAULT_CALLBACK_TIMEOUT_S = 6.0
DEFAULT_INBOUND_POLL_INTERVAL_S = 1.0
DEFAULT_DEDUP_WINDOW_S = 12.0
DEFAULT_HEALTH_CHECK_INTERVAL_S = 30.0   # Phase 1: IsOnline keepalive interval
DEFAULT_MEDIA_DOWNLOAD_DIR = ""          # Phase 1: empty = no auto-download
PHASE3_MSG_CACHE_TTL_S = float(         # Phase 3: message object cache TTL (default 5 min)
    os.environ.get("EDGE_WECHAT_WXAUTOX4_MSG_CACHE_TTL_S") or 300
)

BRIDGE_EXPOSURE_MODE_ENV = "NATSUME_BRIDGE_EXPOSURE_MODE"
BRIDGE_HMAC_SECRET_ENV = "NATSUME_WECHAT_BRIDGE_HMAC_SECRET"
BRIDGE_HMAC_KEY_ID_ENV = "NATSUME_WECHAT_BRIDGE_HMAC_KEY_ID"
BRIDGE_PRINCIPAL_ENV = "NATSUME_WECHAT_BRIDGE_PRINCIPAL_ID"
BRIDGE_ALLOWED_SCOPES_ENV = "NATSUME_WECHAT_BRIDGE_ALLOWED_SCOPES"
BRIDGE_ALLOWED_SOURCES_ENV = "NATSUME_WECHAT_BRIDGE_REMOTE_ALLOWED_SOURCES"
BRIDGE_ALLOWED_ACTIONS_ENV = "NATSUME_WECHAT_BRIDGE_REMOTE_ALLOWED_ACTIONS"
BRIDGE_REMOTE_APPROVAL_REF_ENV = "NATSUME_WECHAT_BRIDGE_REMOTE_APPROVAL_REF"
BRIDGE_RATE_PER_MINUTE_ENV = "NATSUME_BRIDGE_RATE_PER_MINUTE"
BRIDGE_RATE_BURST_ENV = "NATSUME_BRIDGE_RATE_BURST"
BRIDGE_COMMAND_TTL_MS_ENV = "NATSUME_BRIDGE_COMMAND_TTL_MS"
BRIDGE_LEDGER_PATH_ENV = "NATSUME_WECHAT_BRIDGE_SECURITY_LEDGER_PATH"
BRIDGE_AUDIT_PATH_ENV = "NATSUME_WECHAT_BRIDGE_SECURITY_AUDIT_PATH"
BRIDGE_CALLBACK_HMAC_SECRET_ENV = "NATSUME_WECHAT_GATEWAY_CALLBACK_HMAC_SECRET"
LEGACY_BRIDGE_ENABLED_ENV = "EDGE_WECHAT_BRIDGE_ENABLED"

BRIDGE_MODE_DISABLED = "DISABLED"
BRIDGE_MODE_LOOPBACK = "LOOPBACK"
BRIDGE_MODE_REMOTE_APPROVED = "REMOTE_APPROVED"
BRIDGE_COMMAND_VERSION = "natsume.bridge-auth.v1"
BRIDGE_COMMAND_TRANSPORT = "jsonl"
BRIDGE_COMMAND_METHOD = "COMMAND"
BRIDGE_COMMAND_PATH = "wxautox4"
BRIDGE_REQUIRED_SCOPE = "bridge:write"
BRIDGE_CALLBACK_PLATFORM = "wechat"
BRIDGE_CALLBACK_METHOD = "POST"
BRIDGE_MAX_COMMAND_ROW_BYTES = 256 * 1024
BRIDGE_MAX_COMMAND_BODY_BYTES = 128 * 1024
BRIDGE_MAX_COMMAND_ROWS_PER_CYCLE = 256
BRIDGE_MAX_COMMAND_BATCH_BYTES = 4 * 1024 * 1024
BRIDGE_MAX_AUDIT_ROW_BYTES = 64 * 1024
BRIDGE_MAX_CALLBACK_RESPONSE_BYTES = 64 * 1024
BRIDGE_MAX_FUTURE_SKEW_MS = 30_000
BRIDGE_DEFAULT_COMMAND_TTL_MS = 300_000
BRIDGE_DEFAULT_RATE_PER_MINUTE = 120
BRIDGE_DEFAULT_RATE_BURST = 20

BRIDGE_REASON_ALLOWED = "BRIDGE_COMMAND_ALLOWED"
BRIDGE_REASON_DISABLED = "BRIDGE_EXPOSURE_DISABLED"
BRIDGE_REASON_MODE_INVALID = "BRIDGE_EXPOSURE_MODE_INVALID"
BRIDGE_REASON_LEGACY_DISABLED = "BRIDGE_LEGACY_KILL_SWITCH_DISABLED"
BRIDGE_REASON_CONFIG_INVALID = "BRIDGE_EXPOSURE_CONFIG_INVALID"
BRIDGE_REASON_REMOTE_NOT_APPROVED = "BRIDGE_REMOTE_APPROVAL_REQUIRED"
BRIDGE_REASON_EDGE_URL_NOT_LOOPBACK = "BRIDGE_EDGE_URL_NOT_LOOPBACK"
BRIDGE_REASON_SCHEMA_INVALID = "BRIDGE_COMMAND_SCHEMA_INVALID"
BRIDGE_REASON_BODY_TOO_LARGE = "BRIDGE_COMMAND_BODY_TOO_LARGE"
BRIDGE_REASON_BODY_DIGEST_INVALID = "BRIDGE_COMMAND_BODY_DIGEST_INVALID"
BRIDGE_REASON_PRINCIPAL_DENIED = "BRIDGE_COMMAND_PRINCIPAL_DENIED"
BRIDGE_REASON_SCOPE_DENIED = "BRIDGE_COMMAND_SCOPE_DENIED"
BRIDGE_REASON_SOURCE_DENIED = "BRIDGE_COMMAND_SOURCE_DENIED"
BRIDGE_REASON_APPROVAL_DENIED = "BRIDGE_COMMAND_APPROVAL_DENIED"
BRIDGE_REASON_EXPIRED = "BRIDGE_COMMAND_EXPIRED"
BRIDGE_REASON_SIGNATURE_INVALID = "BRIDGE_COMMAND_SIGNATURE_INVALID"
BRIDGE_REASON_RATE_LIMITED = "BRIDGE_COMMAND_RATE_LIMITED"
BRIDGE_REASON_REPLAYED = "BRIDGE_COMMAND_REPLAYED"
BRIDGE_REASON_AUDIT_UNAVAILABLE = "BRIDGE_COMMAND_AUDIT_UNAVAILABLE"
BRIDGE_REASON_AUTHORITY_UNAVAILABLE = "BRIDGE_COMMAND_AUTHORITY_UNAVAILABLE"

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+\-]{0,254}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SECURITY_FIELDS = frozenset({
    "version",
    "key_id",
    "transport",
    "method",
    "path",
    "principal",
    "scope",
    "timestamp_ms",
    "nonce",
    "body_sha256",
    "hmac_sha256",
})

# Media type codes matching the Gateway bridge convention
MEDIA_TYPE_CODE_MAP: dict[str, int] = {
    "text": 1,
    "image": 3,
    "voice": 34,
    "video": 43,
    "emoji": 47,
    "link": 49,
    "file": 49,      # wxautox4 may report files as link/49
    "system": 10000,
}


@dataclass(frozen=True, slots=True)
class BridgeExposureConfig:
    """Fail-closed process configuration captured before wxautox4 import."""

    mode: str
    enabled: bool
    reason_code: str
    edge_url: str
    key_id: str = ""
    principal_id: str = ""
    allowed_scopes: tuple[str, ...] = ()
    allowed_sources: tuple[str, ...] = ()
    allowed_actions: tuple[str, ...] = ()
    approval_ref: str = ""
    rate_per_minute: int = 0
    rate_burst: int = 0
    command_ttl_ms: int = 0
    queue_path: str = ""
    result_path: str = ""
    ledger_path: str = ""
    audit_path: str = ""
    hmac_secret: bytes = field(default=b"", repr=False, compare=False)
    callback_hmac_secret: bytes = field(default=b"", repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class BridgeAuthorizationDecision:
    allowed: bool
    reason_code: str
    command_id_digest: str
    nonce_digest: str
    body_sha256: str


def _env_switch(raw: Any, *, default: bool) -> tuple[bool, bool]:
    if raw is None:
        return default, True
    value = str(raw or "").strip().lower()
    if value in {"1", "true", "yes", "on", "enabled"}:
        return True, True
    if value in {"0", "false", "no", "off", "disabled", ""}:
        return False, True
    return False, False


def _positive_int(raw: Any, default: int) -> int:
    try:
        value = int(str(raw if raw is not None else default).strip())
    except (TypeError, ValueError):
        return -1
    return value


def _csv_tokens(raw: Any) -> tuple[str, ...]:
    values = {
        str(item).strip()
        for item in str(raw or "").split(",")
        if str(item).strip()
    }
    return tuple(sorted(values))


def _edge_url_state(edge_url: str) -> tuple[bool, bool]:
    try:
        parsed = urllib.parse.urlsplit(str(edge_url or "").strip())
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return False, False
        _ = parsed.port
    except (TypeError, ValueError):
        return False, False
    host = str(parsed.hostname).strip().lower()
    if host == "localhost":
        return True, True
    try:
        return True, ipaddress.ip_address(host).is_loopback
    except ValueError:
        return True, False


def _bridge_storage_paths_are_distinct(
    *,
    queue_path: str,
    result_path: str,
    ledger_path: str,
    audit_path: str,
) -> bool:
    paths = [
        Path(item).resolve()
        for item in (queue_path, result_path, ledger_path, audit_path)
        if str(item or "").strip()
    ]
    if ledger_path:
        paths.extend(
            Path(f"{ledger_path}{suffix}").resolve()
            for suffix in ("-wal", "-shm", "-journal")
        )
    if audit_path:
        paths.extend(
            Path(f"{audit_path}.{index}").resolve()
            for index in range(1, 9)
        )
    identities = [
        os.path.normcase(os.path.normpath(str(path)))
        for path in paths
    ]
    if len(set(identities)) != len(identities):
        return False
    existing = [path for path in paths if path.exists()]
    for index, left in enumerate(existing):
        for right in existing[index + 1 :]:
            try:
                if os.path.samefile(left, right):
                    return False
            except OSError:
                return False
    return True


def load_bridge_exposure_config(
    *,
    edge_url: str = DEFAULT_EDGE_URL,
    command_queue_file: str = "",
    command_result_file: str = "",
    environ: Mapping[str, str] | None = None,
) -> BridgeExposureConfig:
    """Parse the complete bridge authority without importing Edge or wx code."""

    source: Mapping[str, str] = os.environ if environ is None else environ
    configured_mode = str(source.get(BRIDGE_EXPOSURE_MODE_ENV, "") or "").strip().upper()
    if not configured_mode:
        return BridgeExposureConfig(
            mode=BRIDGE_MODE_DISABLED,
            enabled=False,
            reason_code=BRIDGE_REASON_DISABLED,
            edge_url=str(edge_url or "").strip(),
        )
    if configured_mode not in {
        BRIDGE_MODE_DISABLED,
        BRIDGE_MODE_LOOPBACK,
        BRIDGE_MODE_REMOTE_APPROVED,
    }:
        return BridgeExposureConfig(
            mode=BRIDGE_MODE_DISABLED,
            enabled=False,
            reason_code=BRIDGE_REASON_MODE_INVALID,
            edge_url=str(edge_url or "").strip(),
        )
    if configured_mode == BRIDGE_MODE_DISABLED:
        return BridgeExposureConfig(
            mode=BRIDGE_MODE_DISABLED,
            enabled=False,
            reason_code=BRIDGE_REASON_DISABLED,
            edge_url=str(edge_url or "").strip(),
        )

    legacy_enabled, legacy_valid = _env_switch(
        source.get(LEGACY_BRIDGE_ENABLED_ENV),
        default=False,
    )
    if not legacy_valid or not legacy_enabled:
        return BridgeExposureConfig(
            mode=BRIDGE_MODE_DISABLED,
            enabled=False,
            reason_code=BRIDGE_REASON_LEGACY_DISABLED,
            edge_url=str(edge_url or "").strip(),
        )

    normalized_url = str(edge_url or "").strip().rstrip("/")
    url_valid, url_loopback = _edge_url_state(normalized_url)
    if not url_valid:
        return BridgeExposureConfig(
            mode=BRIDGE_MODE_DISABLED,
            enabled=False,
            reason_code=BRIDGE_REASON_CONFIG_INVALID,
            edge_url=normalized_url,
        )
    if configured_mode == BRIDGE_MODE_LOOPBACK and not url_loopback:
        return BridgeExposureConfig(
            mode=BRIDGE_MODE_DISABLED,
            enabled=False,
            reason_code=BRIDGE_REASON_EDGE_URL_NOT_LOOPBACK,
            edge_url=normalized_url,
        )

    secret_text = str(source.get(BRIDGE_HMAC_SECRET_ENV, "") or "")
    callback_secret_text = str(source.get(BRIDGE_CALLBACK_HMAC_SECRET_ENV, "") or "")
    key_id = str(
        source.get(BRIDGE_HMAC_KEY_ID_ENV, "bridge.wxautox4.hmac.v1") or ""
    ).strip()
    principal_id = str(
        source.get(BRIDGE_PRINCIPAL_ENV, "edge.gateway.wechat.bridge") or ""
    ).strip()
    explicit_scopes = _csv_tokens(source.get(BRIDGE_ALLOWED_SCOPES_ENV))
    allowed_scopes = explicit_scopes or (
        (BRIDGE_REQUIRED_SCOPE,) if configured_mode == BRIDGE_MODE_LOOPBACK else ()
    )
    allowed_sources = _csv_tokens(source.get(BRIDGE_ALLOWED_SOURCES_ENV))
    allowed_actions = _csv_tokens(source.get(BRIDGE_ALLOWED_ACTIONS_ENV))
    approval_ref = str(source.get(BRIDGE_REMOTE_APPROVAL_REF_ENV, "") or "").strip()
    rate_per_minute = _positive_int(
        source.get(BRIDGE_RATE_PER_MINUTE_ENV), BRIDGE_DEFAULT_RATE_PER_MINUTE
    )
    rate_burst = _positive_int(
        source.get(BRIDGE_RATE_BURST_ENV), BRIDGE_DEFAULT_RATE_BURST
    )
    ttl_ms = _positive_int(
        source.get(BRIDGE_COMMAND_TTL_MS_ENV), BRIDGE_DEFAULT_COMMAND_TTL_MS
    )
    queue_path = _normalize_optional_path(command_queue_file)
    result_path = _normalize_optional_path(command_result_file)
    default_ledger = f"{queue_path}.security.sqlite3" if queue_path else ""
    default_audit = f"{queue_path}.security-audit.jsonl" if queue_path else ""
    ledger_path = _normalize_optional_path(
        source.get(BRIDGE_LEDGER_PATH_ENV) or default_ledger
    )
    audit_path = _normalize_optional_path(
        source.get(BRIDGE_AUDIT_PATH_ENV) or default_audit
    )

    remote_fields_missing = bool(
        configured_mode == BRIDGE_MODE_REMOTE_APPROVED
        and (
            not str(source.get(BRIDGE_HMAC_SECRET_ENV, "") or "")
            or not str(source.get(BRIDGE_CALLBACK_HMAC_SECRET_ENV, "") or "")
            or not str(source.get(BRIDGE_PRINCIPAL_ENV, "") or "").strip()
            or not explicit_scopes
            or not allowed_sources
            or not allowed_actions
            or not approval_ref
            or not str(source.get(BRIDGE_HMAC_KEY_ID_ENV, "") or "").strip()
            or not str(source.get(BRIDGE_RATE_PER_MINUTE_ENV, "") or "").strip()
            or not str(source.get(BRIDGE_RATE_BURST_ENV, "") or "").strip()
            or not str(source.get(BRIDGE_LEDGER_PATH_ENV, "") or "").strip()
            or not str(source.get(BRIDGE_AUDIT_PATH_ENV, "") or "").strip()
        )
    )
    resolved_queue = os.path.normcase(str(Path(queue_path).resolve())) if queue_path else ""
    resolved_result = os.path.normcase(str(Path(result_path).resolve())) if result_path else ""
    resolved_ledger = os.path.normcase(str(Path(ledger_path).resolve())) if ledger_path else ""
    resolved_audit = os.path.normcase(str(Path(audit_path).resolve())) if audit_path else ""
    base_invalid = bool(
        len(secret_text.encode("utf-8")) < 32
        or len(callback_secret_text.encode("utf-8")) < 32
        or hmac.compare_digest(
            secret_text.encode("utf-8"),
            callback_secret_text.encode("utf-8"),
        )
        or _SAFE_ID_RE.fullmatch(key_id) is None
        or _SAFE_ID_RE.fullmatch(principal_id) is None
        or BRIDGE_REQUIRED_SCOPE not in allowed_scopes
        or any(_SAFE_ID_RE.fullmatch(item) is None for item in allowed_scopes)
        or any(_SAFE_ID_RE.fullmatch(item) is None for item in allowed_sources)
        or any(_SAFE_ID_RE.fullmatch(item) is None for item in allowed_actions)
        or (
            configured_mode == BRIDGE_MODE_REMOTE_APPROVED
            and _SAFE_ID_RE.fullmatch(approval_ref) is None
        )
        or rate_per_minute <= 0
        or rate_burst <= 0
        or not (1_000 <= ttl_ms <= 3_600_000)
        or not queue_path
        or not result_path
        or not ledger_path
        or not audit_path
        or not _bridge_storage_paths_are_distinct(
            queue_path=resolved_queue,
            result_path=resolved_result,
            ledger_path=resolved_ledger,
            audit_path=resolved_audit,
        )
    )
    if remote_fields_missing or base_invalid:
        return BridgeExposureConfig(
            mode=BRIDGE_MODE_DISABLED,
            enabled=False,
            reason_code=(
                BRIDGE_REASON_REMOTE_NOT_APPROVED
                if remote_fields_missing
                else BRIDGE_REASON_CONFIG_INVALID
            ),
            edge_url=normalized_url,
        )

    return BridgeExposureConfig(
        mode=configured_mode,
        enabled=True,
        reason_code=BRIDGE_REASON_ALLOWED,
        edge_url=normalized_url,
        key_id=key_id,
        principal_id=principal_id,
        allowed_scopes=allowed_scopes,
        allowed_sources=(allowed_sources if configured_mode == BRIDGE_MODE_REMOTE_APPROVED else ("loopback",)),
        allowed_actions=(allowed_actions if configured_mode == BRIDGE_MODE_REMOTE_APPROVED else ("*",)),
        approval_ref=approval_ref,
        rate_per_minute=rate_per_minute,
        rate_burst=rate_burst,
        command_ttl_ms=ttl_ms,
        queue_path=queue_path,
        result_path=result_path,
        ledger_path=ledger_path,
        audit_path=audit_path,
        hmac_secret=secret_text.encode("utf-8"),
        callback_hmac_secret=callback_secret_text.encode("utf-8"),
    )


def _active_config_is_valid(config: BridgeExposureConfig) -> bool:
    if type(config) is not BridgeExposureConfig or not config.enabled:
        return False
    url_valid, url_loopback = _edge_url_state(config.edge_url)
    if not url_valid or (config.mode == BRIDGE_MODE_LOOPBACK and not url_loopback):
        return False
    if (
        config.mode not in {BRIDGE_MODE_LOOPBACK, BRIDGE_MODE_REMOTE_APPROVED}
        or config.reason_code != BRIDGE_REASON_ALLOWED
        or type(config.hmac_secret) is not bytes
        or type(config.callback_hmac_secret) is not bytes
        or not isinstance(config.allowed_scopes, tuple)
        or not isinstance(config.allowed_sources, tuple)
        or not isinstance(config.allowed_actions, tuple)
        or type(config.rate_per_minute) is not int
        or type(config.rate_burst) is not int
        or type(config.command_ttl_ms) is not int
        or not isinstance(config.key_id, str)
        or not isinstance(config.principal_id, str)
        or not isinstance(config.approval_ref, str)
        or not isinstance(config.queue_path, str)
        or not isinstance(config.result_path, str)
        or not isinstance(config.ledger_path, str)
        or not isinstance(config.audit_path, str)
        or _SAFE_ID_RE.fullmatch(config.key_id) is None
        or _SAFE_ID_RE.fullmatch(config.principal_id) is None
        or len(config.hmac_secret) < 32
        or len(config.callback_hmac_secret) < 32
        or hmac.compare_digest(config.hmac_secret, config.callback_hmac_secret)
        or BRIDGE_REQUIRED_SCOPE not in config.allowed_scopes
        or any(
            not isinstance(item, str) or _SAFE_ID_RE.fullmatch(item) is None
            for item in config.allowed_scopes
        )
        or config.rate_per_minute <= 0
        or config.rate_burst <= 0
        or not (1_000 <= config.command_ttl_ms <= 3_600_000)
        or not config.queue_path
        or not config.result_path
        or not config.ledger_path
        or not config.audit_path
    ):
        return False
    if not _bridge_storage_paths_are_distinct(
        queue_path=config.queue_path,
        result_path=config.result_path,
        ledger_path=config.ledger_path,
        audit_path=config.audit_path,
    ):
        return False
    if config.mode == BRIDGE_MODE_LOOPBACK:
        return config.allowed_sources == ("loopback",) and not config.approval_ref
    return bool(
        config.allowed_sources
        and config.allowed_actions
        and config.approval_ref
        and all(
            isinstance(item, str) and _SAFE_ID_RE.fullmatch(item)
            for item in config.allowed_sources
        )
        and all(
            isinstance(item, str) and _SAFE_ID_RE.fullmatch(item)
            for item in config.allowed_actions
        )
        and _SAFE_ID_RE.fullmatch(config.approval_ref)
    )


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _command_body(row: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in row.items() if str(key) not in _SECURITY_FIELDS}


def _bridge_command_hmac(
    *,
    secret: bytes,
    version: str,
    key_id: str,
    transport: str,
    method: str,
    path: str,
    principal: str,
    scope: str,
    timestamp_ms: int,
    nonce: str,
    body_sha256: str,
) -> str:
    return build_hmac_digest(
        secret,
        version=version,
        key_id=key_id,
        transport=transport,
        method=method,
        path=path,
        timestamp_ms=timestamp_ms,
        nonce=nonce,
        principal=principal,
        scope=scope,
        body_digest=body_sha256,
    )


def sign_bridge_command_row(
    body: Mapping[str, Any],
    *,
    secret: str | bytes,
    key_id: str = "bridge.wxautox4.hmac.v1",
    principal: str = "edge.gateway.wechat.bridge",
    scope: str = BRIDGE_REQUIRED_SCOPE,
    timestamp_ms: int | None = None,
    nonce: str | None = None,
    source: str = "loopback",
    approval_ref: str = "",
) -> dict[str, Any]:
    """Build one content-bound row for trusted queue producers and tests."""

    row = {str(key): value for key, value in dict(body).items()}
    if any(key in row for key in _SECURITY_FIELDS):
        raise ValueError("business body contains reserved bridge security fields")
    row["source"] = str(source).strip()
    row["approval_ref"] = str(approval_ref).strip()
    body_bytes = _canonical_json_bytes(row)
    if len(body_bytes) > BRIDGE_MAX_COMMAND_BODY_BYTES:
        raise ValueError(BRIDGE_REASON_BODY_TOO_LARGE)
    key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    if len(key) < 32:
        raise ValueError("bridge HMAC secret must contain at least 32 bytes")
    issued_at = int(time.time() * 1000) if timestamp_ms is None else int(timestamp_ms)
    nonce_value = str(nonce or secrets.token_urlsafe(24)).strip()
    digest = hashlib.sha256(body_bytes).hexdigest()
    row.update({
        "version": BRIDGE_COMMAND_VERSION,
        "key_id": str(key_id).strip(),
        "transport": BRIDGE_COMMAND_TRANSPORT,
        "method": BRIDGE_COMMAND_METHOD,
        "path": BRIDGE_COMMAND_PATH,
        "principal": str(principal).strip(),
        "scope": str(scope).strip(),
        "timestamp_ms": issued_at,
        "nonce": nonce_value,
        "body_sha256": digest,
    })
    row["hmac_sha256"] = _bridge_command_hmac(
        secret=key,
        version=BRIDGE_COMMAND_VERSION,
        key_id=row["key_id"],
        transport=BRIDGE_COMMAND_TRANSPORT,
        method=BRIDGE_COMMAND_METHOD,
        path=BRIDGE_COMMAND_PATH,
        principal=row["principal"],
        scope=row["scope"],
        timestamp_ms=issued_at,
        nonce=nonce_value,
        body_sha256=digest,
    )
    if len(_canonical_json_bytes(row)) > BRIDGE_MAX_COMMAND_ROW_BYTES:
        raise ValueError(BRIDGE_REASON_BODY_TOO_LARGE)
    return row


class _BridgeSecurityLedger:
    """Durable rate and one-shot claims; no command payload is persisted."""

    def __init__(self, path: str) -> None:
        self._path = str(Path(path).resolve())
        _ensure_parent_dir(self._path)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS bridge_command_claim (
                    nonce_digest TEXT PRIMARY KEY,
                    command_id_digest TEXT NOT NULL UNIQUE,
                    body_sha256 TEXT NOT NULL,
                    principal_digest TEXT NOT NULL,
                    claimed_at_ms INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bridge_rate_window (
                    authority_digest TEXT NOT NULL,
                    window_start_ms INTEGER NOT NULL,
                    request_count INTEGER NOT NULL,
                    PRIMARY KEY (authority_digest, window_start_ms)
                );
                CREATE TABLE IF NOT EXISTS bridge_rate_bucket (
                    authority_digest TEXT PRIMARY KEY,
                    tokens REAL NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=5.0, isolation_level=None)
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def claim_with_rate(
        self,
        *,
        authority_digest: str,
        nonce_digest: str,
        command_id_digest: str,
        body_sha256: str,
        principal_digest: str,
        now_ms: int,
        rate_per_minute: int,
        burst: int,
    ) -> str:
        """Atomically check replay, consume one global token and claim once."""
        current_ms = int(now_ms)
        capacity = float(max(1, int(burst)))
        refill_per_ms = float(rate_per_minute) / 60_000.0
        try:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                replay = conn.execute(
                    """
                    SELECT 1 FROM bridge_command_claim
                    WHERE nonce_digest=? OR command_id_digest=?
                    LIMIT 1
                    """,
                    (nonce_digest, command_id_digest),
                ).fetchone()
                if replay is not None:
                    conn.commit()
                    return BRIDGE_REASON_REPLAYED

                rate_row = conn.execute(
                    """
                    SELECT tokens, updated_at_ms FROM bridge_rate_bucket
                    WHERE authority_digest=?
                    """,
                    (authority_digest,),
                ).fetchone()
                if rate_row is None:
                    tokens = capacity
                    updated_at_ms = current_ms
                else:
                    previous_tokens = max(0.0, float(rate_row[0]))
                    previous_updated = int(rate_row[1])
                    updated_at_ms = max(current_ms, previous_updated)
                    tokens = min(
                        capacity,
                        previous_tokens
                        + (updated_at_ms - previous_updated) * refill_per_ms,
                    )
                if tokens < 1.0:
                    conn.execute(
                        """
                        INSERT INTO bridge_rate_bucket(
                            authority_digest, tokens, updated_at_ms
                        ) VALUES (?, ?, ?)
                        ON CONFLICT(authority_digest)
                        DO UPDATE SET
                            tokens=excluded.tokens,
                            updated_at_ms=excluded.updated_at_ms
                        """,
                        (authority_digest, tokens, updated_at_ms),
                    )
                    conn.commit()
                    return BRIDGE_REASON_RATE_LIMITED

                conn.execute(
                    """
                    INSERT INTO bridge_command_claim(
                        nonce_digest, command_id_digest, body_sha256,
                        principal_digest, claimed_at_ms, status, updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?, 'CLAIMED', ?)
                    """,
                    (
                        nonce_digest,
                        command_id_digest,
                        body_sha256,
                        principal_digest,
                        int(now_ms),
                        int(now_ms),
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO bridge_rate_bucket(
                        authority_digest, tokens, updated_at_ms
                    ) VALUES (?, ?, ?)
                    ON CONFLICT(authority_digest)
                    DO UPDATE SET
                        tokens=excluded.tokens,
                        updated_at_ms=excluded.updated_at_ms
                    """,
                    (authority_digest, tokens - 1.0, updated_at_ms),
                )
                conn.commit()
                return BRIDGE_REASON_ALLOWED
        except sqlite3.IntegrityError:
            return BRIDGE_REASON_REPLAYED

    def mark_terminal(
        self,
        *,
        nonce_digest: str,
        succeeded: bool,
        now_ms: int,
    ) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE bridge_command_claim SET status=?, updated_at_ms=? WHERE nonce_digest=?",
                ("EXECUTED" if succeeded else "FAILED", int(now_ms), nonce_digest),
            )
            conn.commit()

    def cleanup(self, *, now_ms: int, retention_ms: int) -> None:
        claim_cutoff = int(now_ms) - max(60_000, int(retention_ms))
        rate_cutoff = int(now_ms) - 24 * 60 * 60 * 1000
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM bridge_command_claim WHERE claimed_at_ms < ?",
                (claim_cutoff,),
            )
            conn.execute(
                "DELETE FROM bridge_rate_bucket WHERE updated_at_ms < ?",
                (rate_cutoff,),
            )
            conn.execute(
                "DELETE FROM bridge_rate_window WHERE window_start_ms < ?",
                (rate_cutoff,),
            )
            conn.commit()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


class _BridgeJsonlAudit:
    """Fsync audit containing identifiers/digests only, never raw commands."""

    def __init__(self, path: str) -> None:
        self._path = str(Path(path).resolve())
        self._lock = threading.Lock()
        self._poisoned = False
        _ensure_parent_dir(self._path)
        if os.path.islink(self._path):
            raise OSError("bridge audit path must not be a symlink")
        self._validate_existing()
        with open(self._path, "ab", buffering=0) as handle:
            handle.flush()
            os.fsync(handle.fileno())

    def append(self, row: Mapping[str, Any]) -> None:
        safe = {
            "schema_version": "natsume.bridge-security-audit.v1",
            "ts_ms": int(row.get("ts_ms") or 0),
            "decision": str(row.get("decision") or "DENY"),
            "reason_code": str(row.get("reason_code") or BRIDGE_REASON_AUTHORITY_UNAVAILABLE),
            "principal_digest": str(row.get("principal_digest") or ""),
            "scope_digest": str(row.get("scope_digest") or ""),
            "source_digest": str(row.get("source_digest") or ""),
            "command_id_digest": str(row.get("command_id_digest") or ""),
            "nonce_digest": str(row.get("nonce_digest") or ""),
            "body_sha256": str(row.get("body_sha256") or ""),
        }
        encoded = _canonical_json_bytes(safe) + b"\n"
        with self._lock:
            if self._poisoned:
                raise OSError("bridge audit writer is poisoned")
            try:
                self._rotate_if_needed(len(encoded))
                with open(self._path, "ab", buffering=0) as handle:
                    written = handle.write(encoded)
                    if written != len(encoded):
                        raise OSError("bridge audit short write")
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError:
                self._poisoned = True
                raise

    def _validate_existing(self) -> None:
        for candidate in (
            self._path,
            *(f"{self._path}.{index}" for index in range(1, 9)),
        ):
            if not os.path.exists(candidate):
                continue
            with open(candidate, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                if handle.tell() > 0:
                    handle.seek(-1, os.SEEK_END)
                    if handle.read(1) != b"\n":
                        raise ValueError("bridge audit contains a torn terminal row")
                handle.seek(0)
                while True:
                    raw = handle.readline(BRIDGE_MAX_AUDIT_ROW_BYTES + 2)
                    if not raw:
                        break
                    if len(raw) > BRIDGE_MAX_AUDIT_ROW_BYTES:
                        raise ValueError("bridge audit row exceeds its limit")
                    try:
                        row = json.loads(
                            raw.decode("utf-8"),
                            object_pairs_hook=_strict_json_object,
                            parse_constant=_reject_nonfinite_json,
                        )
                    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
                        raise ValueError("bridge audit contains malformed JSON") from exc
                    if (
                        not isinstance(row, dict)
                        or row.get("schema_version") != "natsume.bridge-security-audit.v1"
                        or not isinstance(row.get("ts_ms"), int)
                        or row.get("decision") not in {"ALLOW", "DENY"}
                        or not str(row.get("reason_code") or "")
                    ):
                        raise ValueError("bridge audit contains an invalid row")

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        maximum = 64 * 1024 * 1024
        current = os.path.getsize(self._path) if os.path.exists(self._path) else 0
        if current + int(incoming_bytes) <= maximum:
            return
        archives = 8
        oldest = f"{self._path}.{archives}"
        if os.path.exists(oldest):
            os.unlink(oldest)
        for index in range(archives - 1, 0, -1):
            source = f"{self._path}.{index}"
            if os.path.exists(source):
                os.replace(source, f"{self._path}.{index + 1}")
        if os.path.exists(self._path):
            os.replace(self._path, f"{self._path}.1")


def _digest_text(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


class BridgeCommandAuthority:
    """Verify, rate-limit, audit and durably claim before any wx API call."""

    def __init__(
        self,
        config: BridgeExposureConfig,
        *,
        clock_ms: Any | None = None,
    ) -> None:
        if not _active_config_is_valid(config):
            raise ValueError(BRIDGE_REASON_CONFIG_INVALID)
        if clock_ms is not None and not callable(clock_ms):
            raise TypeError("clock_ms must be callable")
        self._config = config
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._ledger = _BridgeSecurityLedger(config.ledger_path)
        self._audit = _BridgeJsonlAudit(config.audit_path)
        self._ledger.cleanup(
            now_ms=int(self._clock_ms()),
            retention_ms=config.command_ttl_ms + BRIDGE_MAX_FUTURE_SKEW_MS,
        )
        self._last_cleanup_ms = int(self._clock_ms())

    @property
    def config(self) -> BridgeExposureConfig:
        return self._config

    def authorize(self, row: Mapping[str, Any]) -> BridgeAuthorizationDecision:
        now_ms = int(self._clock_ms())
        if now_ms - self._last_cleanup_ms >= 60 * 60 * 1000:
            self._ledger.cleanup(
                now_ms=now_ms,
                retention_ms=self._config.command_ttl_ms + BRIDGE_MAX_FUTURE_SKEW_MS,
            )
            self._last_cleanup_ms = now_ms
        decision = self._verify(row, now_ms=now_ms)
        if not decision.allowed:
            try:
                self._audit_decision(row, decision, now_ms=now_ms)
                return decision
            except Exception:
                return BridgeAuthorizationDecision(
                    False,
                    BRIDGE_REASON_AUDIT_UNAVAILABLE,
                    decision.command_id_digest,
                    decision.nonce_digest,
                    decision.body_sha256,
                )
        principal = str(row.get("principal") or "")
        authority_digest = _digest_text(f"{self._config.key_id}|{principal}")
        try:
            authority_reason = self._ledger.claim_with_rate(
                authority_digest=authority_digest,
                nonce_digest=decision.nonce_digest,
                command_id_digest=decision.command_id_digest,
                body_sha256=decision.body_sha256,
                principal_digest=_digest_text(principal),
                now_ms=now_ms,
                rate_per_minute=self._config.rate_per_minute,
                burst=self._config.rate_burst,
            )
        except Exception:
            unavailable = BridgeAuthorizationDecision(
                False,
                BRIDGE_REASON_AUTHORITY_UNAVAILABLE,
                decision.command_id_digest,
                decision.nonce_digest,
                decision.body_sha256,
            )
            try:
                self._audit_decision(row, unavailable, now_ms=now_ms)
                return unavailable
            except Exception:
                return BridgeAuthorizationDecision(
                    False,
                    BRIDGE_REASON_AUDIT_UNAVAILABLE,
                    decision.command_id_digest,
                    decision.nonce_digest,
                    decision.body_sha256,
                )
        if authority_reason != BRIDGE_REASON_ALLOWED:
            decision = BridgeAuthorizationDecision(
                False,
                authority_reason,
                decision.command_id_digest,
                decision.nonce_digest,
                decision.body_sha256,
            )
            try:
                self._audit_decision(row, decision, now_ms=now_ms)
                return decision
            except Exception:
                return BridgeAuthorizationDecision(
                    False, BRIDGE_REASON_AUDIT_UNAVAILABLE,
                    decision.command_id_digest, decision.nonce_digest,
                    decision.body_sha256,
                )
        try:
            self._audit_decision(row, decision, now_ms=now_ms)
        except Exception:
            return BridgeAuthorizationDecision(
                False,
                BRIDGE_REASON_AUDIT_UNAVAILABLE,
                decision.command_id_digest,
                decision.nonce_digest,
                decision.body_sha256,
            )
        return decision

    def mark_terminal(self, decision: BridgeAuthorizationDecision, *, succeeded: bool) -> None:
        try:
            self._ledger.mark_terminal(
                nonce_digest=decision.nonce_digest,
                succeeded=succeeded,
                now_ms=int(self._clock_ms()),
            )
        except Exception:
            # The durable one-shot claim already exists.  Never make a failed
            # status write a reason to repeat a potentially completed effect.
            return

    def _verify(
        self,
        row: Mapping[str, Any],
        *,
        now_ms: int,
    ) -> BridgeAuthorizationDecision:
        command_id = str(row.get("command_id") or "").strip()
        nonce = str(row.get("nonce") or "").strip()
        body_sha256 = str(row.get("body_sha256") or "").strip().lower()
        base = BridgeAuthorizationDecision(
            False,
            BRIDGE_REASON_SCHEMA_INVALID,
            _digest_text(command_id),
            _digest_text(nonce),
            body_sha256 if _SHA256_RE.fullmatch(body_sha256) else "",
        )
        parser_rejection = str(row.get("__bridge_rejection_reason") or "")
        if parser_rejection in {BRIDGE_REASON_BODY_TOO_LARGE, BRIDGE_REASON_SCHEMA_INVALID}:
            return BridgeAuthorizationDecision(
                False,
                parser_rejection,
                base.command_id_digest,
                base.nonce_digest,
                base.body_sha256,
            )
        try:
            row_bytes = _canonical_json_bytes(dict(row))
            body = _command_body(row)
            body_bytes = _canonical_json_bytes(body)
        except (TypeError, ValueError):
            return base
        if len(row_bytes) > BRIDGE_MAX_COMMAND_ROW_BYTES or len(body_bytes) > BRIDGE_MAX_COMMAND_BODY_BYTES:
            return BridgeAuthorizationDecision(
                False, BRIDGE_REASON_BODY_TOO_LARGE,
                base.command_id_digest, base.nonce_digest, base.body_sha256,
            )
        version = str(row.get("version") or "").strip()
        key_id = str(row.get("key_id") or "").strip()
        transport = str(row.get("transport") or "").strip()
        method = str(row.get("method") or "").strip()
        path = str(row.get("path") or "").strip()
        principal = str(row.get("principal") or "").strip()
        scope = str(row.get("scope") or "").strip()
        source = str(row.get("source") or "").strip()
        approval_ref = str(row.get("approval_ref") or "").strip()
        signature = str(row.get("hmac_sha256") or "").strip().lower()
        timestamp = row.get("timestamp_ms")
        if (
            version != BRIDGE_COMMAND_VERSION
            or key_id != self._config.key_id
            or transport != BRIDGE_COMMAND_TRANSPORT
            or method != BRIDGE_COMMAND_METHOD
            or path != BRIDGE_COMMAND_PATH
            or _SAFE_ID_RE.fullmatch(command_id) is None
            or _SAFE_ID_RE.fullmatch(principal) is None
            or _SAFE_ID_RE.fullmatch(scope) is None
            or _SAFE_ID_RE.fullmatch(nonce) is None
            or type(timestamp) is not int
            or _SHA256_RE.fullmatch(body_sha256) is None
            or _SHA256_RE.fullmatch(signature) is None
        ):
            return base
        expected_body = hashlib.sha256(body_bytes).hexdigest()
        if not hmac.compare_digest(expected_body, body_sha256):
            return BridgeAuthorizationDecision(
                False, BRIDGE_REASON_BODY_DIGEST_INVALID,
                base.command_id_digest, base.nonce_digest, expected_body,
            )
        if principal != self._config.principal_id:
            return BridgeAuthorizationDecision(
                False, BRIDGE_REASON_PRINCIPAL_DENIED,
                base.command_id_digest, base.nonce_digest, expected_body,
            )
        if scope != BRIDGE_REQUIRED_SCOPE or scope not in self._config.allowed_scopes:
            return BridgeAuthorizationDecision(
                False, BRIDGE_REASON_SCOPE_DENIED,
                base.command_id_digest, base.nonce_digest, expected_body,
            )
        action = str(row.get("action") or "").strip().lower()
        if (
            self._config.mode == BRIDGE_MODE_REMOTE_APPROVED
            and action not in self._config.allowed_actions
        ):
            return BridgeAuthorizationDecision(
                False,
                BRIDGE_REASON_SCOPE_DENIED,
                base.command_id_digest,
                base.nonce_digest,
                expected_body,
            )
        if not (now_ms - self._config.command_ttl_ms <= timestamp <= now_ms + BRIDGE_MAX_FUTURE_SKEW_MS):
            return BridgeAuthorizationDecision(
                False, BRIDGE_REASON_EXPIRED,
                base.command_id_digest, base.nonce_digest, expected_body,
            )
        if self._config.mode == BRIDGE_MODE_LOOPBACK:
            if source != "loopback" or approval_ref:
                return BridgeAuthorizationDecision(
                    False, BRIDGE_REASON_SOURCE_DENIED,
                    base.command_id_digest, base.nonce_digest, expected_body,
                )
        else:
            if source not in self._config.allowed_sources:
                return BridgeAuthorizationDecision(
                    False, BRIDGE_REASON_SOURCE_DENIED,
                    base.command_id_digest, base.nonce_digest, expected_body,
                )
            if approval_ref != self._config.approval_ref:
                return BridgeAuthorizationDecision(
                    False, BRIDGE_REASON_APPROVAL_DENIED,
                    base.command_id_digest, base.nonce_digest, expected_body,
                )
        expected_signature = _bridge_command_hmac(
            secret=self._config.hmac_secret,
            version=version,
            key_id=key_id,
            transport=transport,
            method=method,
            path=path,
            principal=principal,
            scope=scope,
            timestamp_ms=timestamp,
            nonce=nonce,
            body_sha256=body_sha256,
        )
        if not hmac.compare_digest(expected_signature, signature):
            return BridgeAuthorizationDecision(
                False, BRIDGE_REASON_SIGNATURE_INVALID,
                base.command_id_digest, base.nonce_digest, expected_body,
            )
        return BridgeAuthorizationDecision(
            True,
            BRIDGE_REASON_ALLOWED,
            base.command_id_digest,
            base.nonce_digest,
            expected_body,
        )

    def _audit_decision(
        self,
        row: Mapping[str, Any],
        decision: BridgeAuthorizationDecision,
        *,
        now_ms: int,
    ) -> None:
        self._audit.append({
            "ts_ms": now_ms,
            "decision": "ALLOW" if decision.allowed else "DENY",
            "reason_code": decision.reason_code,
            "principal_digest": _digest_text(row.get("principal")),
            "scope_digest": _digest_text(row.get("scope")),
            "source_digest": _digest_text(row.get("source")),
            "command_id_digest": decision.command_id_digest,
            "nonce_digest": decision.nonce_digest,
            "body_sha256": decision.body_sha256,
        })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_optional_path(raw: str) -> str:
    """Return stripped path string, or empty string if empty/None."""
    return str(raw or "").strip()


def _ensure_parent_dir(path_text: str) -> None:
    parent = Path(path_text).parent
    parent.mkdir(parents=True, exist_ok=True)


def _build_msg_id(*, sender: str, content: str, ts_ms: int) -> str:
    digest = hashlib.sha1(
        f"{sender}|{content}|{ts_ms}".encode("utf-8", errors="ignore"),
    ).hexdigest()[:12]
    return f"wxautox4-{ts_ms}-{digest}"


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        text = str(key)
        if text in result:
            raise ValueError("duplicate JSON key")
        result[text] = value
    return result


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _read_jsonl_rows_from_offset(
    path_value: str,
    offset: int,
    discarding_oversized_row: bool = False,
) -> tuple[list[dict[str, Any]], int, bool]:
    path_text = _normalize_optional_path(path_value)
    if not path_text or not os.path.exists(path_text):
        return [], 0, False
    with open(path_text, "rb") as handle:
        handle.seek(max(0, int(offset)))
        rows: list[dict[str, Any]] = []
        batch_bytes = 0
        discarding = bool(discarding_oversized_row)
        while discarding and batch_bytes < BRIDGE_MAX_COMMAND_BATCH_BYTES:
            remaining = BRIDGE_MAX_COMMAND_BATCH_BYTES - batch_bytes
            raw = handle.readline(min(BRIDGE_MAX_COMMAND_ROW_BYTES + 2, remaining))
            if not raw:
                return rows, int(handle.tell()), True
            batch_bytes += len(raw)
            if raw.endswith(b"\n"):
                discarding = False
        if discarding:
            return rows, int(handle.tell()), True
        while (
            len(rows) < BRIDGE_MAX_COMMAND_ROWS_PER_CYCLE
            and batch_bytes < BRIDGE_MAX_COMMAND_BATCH_BYTES
        ):
            raw = handle.readline(BRIDGE_MAX_COMMAND_ROW_BYTES + 2)
            if not raw:
                break
            batch_bytes += len(raw)
            if len(raw) > BRIDGE_MAX_COMMAND_ROW_BYTES:
                rows.append({"__bridge_rejection_reason": BRIDGE_REASON_BODY_TOO_LARGE})
                discarding = not raw.endswith(b"\n")
                while discarding and batch_bytes < BRIDGE_MAX_COMMAND_BATCH_BYTES:
                    remaining = BRIDGE_MAX_COMMAND_BATCH_BYTES - batch_bytes
                    raw = handle.readline(
                        min(BRIDGE_MAX_COMMAND_ROW_BYTES + 2, remaining)
                    )
                    if not raw:
                        break
                    batch_bytes += len(raw)
                    if raw.endswith(b"\n"):
                        discarding = False
                break
            try:
                line = raw.decode("utf-8").strip()
            except UnicodeDecodeError:
                rows.append({"__bridge_rejection_reason": BRIDGE_REASON_SCHEMA_INVALID})
                continue
            if not line:
                continue
            try:
                payload = json.loads(
                    line,
                    object_pairs_hook=_strict_json_object,
                    parse_constant=_reject_nonfinite_json,
                )
            except Exception:
                rows.append({"__bridge_rejection_reason": BRIDGE_REASON_SCHEMA_INVALID})
                continue
            if isinstance(payload, dict):
                rows.append(payload)
            else:
                rows.append({"__bridge_rejection_reason": BRIDGE_REASON_SCHEMA_INVALID})
        return rows, int(handle.tell()), discarding


def _append_jsonl_row(path_value: str, row: dict[str, Any]) -> None:
    path_text = _normalize_optional_path(path_value)
    if not path_text:
        return
    _ensure_parent_dir(path_text)
    with open(path_text, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")


def _post_to_edge_callback(
    edge_url: str,
    body: dict[str, Any],
    *,
    timeout_s: float,
    security: BridgeExposureConfig | None = None,
    now_ms: int | None = None,
    nonce: str | None = None,
) -> bool:
    """POST inbound message to Gateway webhook. Returns True on success."""
    if security is None or not _active_config_is_valid(security):
        return False
    base = str(edge_url or DEFAULT_EDGE_URL).strip().rstrip("/")
    if base != security.edge_url:
        return False
    url = f"{base}{CALLBACK_PATH}"
    data = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    issued_at_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    timestamp = str(issued_at_ms // 1000)
    nonce_value = str(nonce or secrets.token_urlsafe(24)).strip()
    canonical = (
        "natsume-webhook-v1\n"
        f"{BRIDGE_CALLBACK_PLATFORM}\n{BRIDGE_CALLBACK_METHOD}\n{CALLBACK_PATH}\n"
        f"{timestamp}\n{nonce_value}\n"
    ).encode("utf-8") + data
    signature = hmac.new(
        security.callback_hmac_secret,
        canonical,
        hashlib.sha256,
    ).hexdigest()
    headers.update({
        "X-Natsume-Webhook-Timestamp": timestamp,
        "X-Natsume-Webhook-Nonce": nonce_value,
        "X-Natsume-Webhook-Signature": signature,
    })
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=max(0.2, float(timeout_s)),
        ) as response:
            payload = response.read(BRIDGE_MAX_CALLBACK_RESPONSE_BYTES + 1)
            if len(payload) > BRIDGE_MAX_CALLBACK_RESPONSE_BYTES:
                return False
    except urllib.error.HTTPError as exc:
        error_body = exc.read(BRIDGE_MAX_CALLBACK_RESPONSE_BYTES + 1)
        logger.warning(
            "edge callback rejected code=%s body=%s",
            exc.code,
            error_body[:BRIDGE_MAX_CALLBACK_RESPONSE_BYTES],
        )
        return False
    except Exception as exc:
        logger.warning("edge callback failed: %s", exc)
        return False
    try:
        parsed = json.loads(payload.decode("utf-8", errors="ignore") or "{}")
    except Exception:
        return True
    if (
        isinstance(parsed, dict)
        and parsed.get("success") is False
        and str(parsed.get("status") or "").lower() == "rejected"
    ):
        return False
    return True


def _resolve_msg_type_code(msg: Any) -> int:
    """Map a wxautox4 message object to a WeChat raw type code integer."""
    # wxautox4 WxMsg may have a .type attribute (str or int)
    raw_type = getattr(msg, "type", None)
    if isinstance(raw_type, int):
        return raw_type
    if isinstance(raw_type, str):
        return MEDIA_TYPE_CODE_MAP.get(raw_type.lower(), 1)
    # Fallback: check content for media hints
    content = str(getattr(msg, "content", "") or "")
    if content.startswith("[图片]") or content.startswith("[Image]"):
        return 3
    if content.startswith("[语音]") or content.startswith("[Voice]"):
        return 34
    if content.startswith("[视频]") or content.startswith("[Video]"):
        return 43
    return 1  # default: text


def _safe_serialize(obj: Any) -> Any:
    """Recursively convert wxautox4 objects to JSON-serializable types."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(item) for item in obj]
    if isinstance(obj, dict):
        return {str(k): _safe_serialize(v) for k, v in obj.items()}
    # wxautox4 objects: try __dict__ first, then str
    d = getattr(obj, "__dict__", None)
    if d and isinstance(d, dict):
        return {str(k): _safe_serialize(v) for k, v in d.items() if not k.startswith("_")}
    return str(obj)


# ---------------------------------------------------------------------------
# Phase 1: HealthMonitor — periodic IsOnline keepalive
# ---------------------------------------------------------------------------

class HealthMonitor:
    """Runs periodic wx.IsOnline() checks and logs online/offline transitions.

    Can be extended later to push health events to the Gateway webhook.
    """

    def __init__(
        self,
        wx: Any,
        *,
        check_interval_s: float = DEFAULT_HEALTH_CHECK_INTERVAL_S,
    ) -> None:
        self._wx = wx
        self._interval = max(5.0, check_interval_s)
        self._shutdown = threading.Event()
        self._last_online: bool | None = None

    def run(self) -> None:
        logger.info("HealthMonitor started (interval=%.0fs)", self._interval)
        while not self._shutdown.is_set():
            self._check()
            self._shutdown.wait(timeout=self._interval)
        logger.info("HealthMonitor stopped.")

    def stop(self) -> None:
        self._shutdown.set()

    def _check(self) -> None:
        try:
            online = bool(self._wx.IsOnline())
        except Exception as exc:
            logger.warning("HealthMonitor: IsOnline() error: %s", exc)
            return
        if online != self._last_online:
            if online:
                logger.info("WeChat online status: ONLINE ✓")
            else:
                logger.warning("WeChat online status: OFFLINE — WeChat may have been closed or logged out!")
            self._last_online = online


# ---------------------------------------------------------------------------
# Inbound processor — receives messages from wxautox4 and posts to Gateway
# ---------------------------------------------------------------------------

class InboundProcessor:
    """Polls wxautox4 for new messages and forwards them via webhook POST."""

    def __init__(
        self,
        wx: Any,
        *,
        edge_url: str = DEFAULT_EDGE_URL,
        poll_interval_s: float = DEFAULT_INBOUND_POLL_INTERVAL_S,
        dedup_window_s: float = DEFAULT_DEDUP_WINDOW_S,
        callback_timeout_s: float = DEFAULT_CALLBACK_TIMEOUT_S,
        listen_targets: list[str] | None = None,
        media_download_dir: str = DEFAULT_MEDIA_DOWNLOAD_DIR,  # Phase 1
        msg_cache: dict[str, Any] | None = None,              # Phase 3: shared msg object cache
        security: BridgeExposureConfig | None = None,
    ) -> None:
        self._wx = wx
        self._edge_url = edge_url
        self._poll_interval_s = max(0.2, poll_interval_s)
        self._dedup_window_s = max(1.0, dedup_window_s)
        self._callback_timeout_s = callback_timeout_s
        self._listen_targets = list(listen_targets or [])
        self._media_download_dir = _normalize_optional_path(media_download_dir)
        self._msg_cache: dict[str, Any] = msg_cache if msg_cache is not None else {}
        if security is None or not _active_config_is_valid(security):
            raise ValueError(BRIDGE_REASON_CONFIG_INVALID)
        normalized_edge_url = str(edge_url or DEFAULT_EDGE_URL).strip().rstrip("/")
        if normalized_edge_url != security.edge_url:
            raise ValueError(BRIDGE_REASON_CONFIG_INVALID)
        self._edge_url = normalized_edge_url
        self._security = security
        self._msg_cache_ts: dict[str, float] = {}   # Phase 3: msg_id -> insert monotonic time
        self._shutdown = threading.Event()
        self._recent_signatures: dict[str, float] = {}  # signature -> mono timestamp
        self._stats = {"forwarded": 0, "deduped": 0, "errors": 0}

    def setup_listeners(self) -> None:
        """Register chat listeners with wxautox4."""
        for target in self._listen_targets:
            target = str(target or "").strip()
            if not target:
                continue
            try:
                self._wx.AddListenChat(who=target)
                logger.info("wxautox4: registered listener for '%s'", target)
            except Exception as exc:
                logger.warning("wxautox4: failed to register listener for '%s': %s", target, exc)

    def run(self) -> None:
        """Main poll loop — call from a daemon thread."""
        logger.info("InboundProcessor started (poll=%.1fs, dedup=%.1fs)", self._poll_interval_s, self._dedup_window_s)
        while not self._shutdown.is_set():
            try:
                self._poll_cycle()
            except Exception as exc:
                logger.warning("InboundProcessor poll error: %s", exc)
                self._stats["errors"] += 1
            self._shutdown.wait(timeout=self._poll_interval_s)
        logger.info("InboundProcessor stopped. stats=%s", self._stats)

    def stop(self) -> None:
        self._shutdown.set()

    def _poll_cycle(self) -> None:
        """Single poll iteration: get new messages and forward them."""
        try:
            msgs = self._wx.GetNewMessage()
        except Exception as exc:
            logger.debug("GetNewMessage error: %s", exc)
            return

        if not msgs:
            return

        ts_ms = int(time.time() * 1000)

        # wxautox4 GetNewMessage() returns:
        #   dict[str, list[WxMsg]]  — keyed by chat name / conversation_id
        if isinstance(msgs, dict):
            for chat_name, msg_list in msgs.items():
                if not isinstance(msg_list, (list, tuple)):
                    msg_list = [msg_list]
                for msg in msg_list:
                    self._forward_one_message(msg, chat_name=chat_name, ts_ms=ts_ms)
        elif isinstance(msgs, (list, tuple)):
            # Fallback: if wxautox4 returns a flat list
            for msg in msgs:
                chat_name = str(getattr(msg, "roomid", None) or getattr(msg, "sender", "") or "")
                self._forward_one_message(msg, chat_name=chat_name, ts_ms=ts_ms)

        # Phase 3: periodic cache eviction (runs every poll cycle)
        self._evict_expired_msg_cache()

    def _try_download_media(self, msg: Any) -> str:
        """Phase 1: Attempt to download media file from message object.

        Returns local file path on success, or empty string on failure/skip.
        """
        if not self._media_download_dir:
            return ""
        msg_type_code = _resolve_msg_type_code(msg)
        # Only attempt download for image(3), voice(34), video(43)
        if msg_type_code not in (3, 34, 43):
            return ""
        save_dir = Path(self._media_download_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        try:
            local_path = msg.download(savepath=str(save_dir))
            if local_path and os.path.isfile(str(local_path)):
                logger.info("media downloaded: %s", local_path)
                return str(local_path)
        except AttributeError:
            pass  # message type has no download() method
        except Exception as exc:
            logger.debug("media download failed: %s", exc)
        return ""

    def _extract_link_url(self, msg: Any) -> str:
        """Phase 2: Extract URL from a LinkMessage object.

        Returns the URL string, or empty string if not a link message.
        """
        msg_type_code = _resolve_msg_type_code(msg)
        # Link messages have type code 49
        if msg_type_code != 49:
            return ""
        try:
            url = msg.get_url()
            if url:
                return str(url).strip()
        except AttributeError:
            pass  # not a LinkMessage
        except Exception as exc:
            logger.debug("LinkMessage.get_url() failed: %s", exc)
        return ""

    def _forward_one_message(self, msg: Any, *, chat_name: str, ts_ms: int) -> None:
        """Forward a single wxautox4 message to the edge callback."""
        sender = str(
            getattr(msg, "sender_name", None)
            or getattr(msg, "sender", None)
            or chat_name
            or ""
        ).strip()
        content = str(getattr(msg, "content", None) or str(msg) or "").strip()
        if not content:
            return

        # Dedup
        signature = f"{sender}|{content}|{ts_ms // 1000}"
        if self._is_recent_duplicate(signature):
            self._stats["deduped"] += 1
            return

        msg_id = _build_msg_id(sender=sender, content=content, ts_ms=ts_ms)
        room_id = str(getattr(msg, "roomid", None) or "").strip()
        is_group_hint = bool(room_id) or (sender != chat_name and bool(chat_name))
        msg_type_code = _resolve_msg_type_code(msg)

        # Phase 1: try to download media and attach local path
        media_path = str(getattr(msg, "media_path", None) or getattr(msg, "file_path", None) or "").strip()
        if not media_path:
            media_path = self._try_download_media(msg)

        # Phase 2: try to extract link URL from LinkMessage
        link_url = self._extract_link_url(msg)

        # Build the webhook body consumed by the Gateway WeChat webhook.
        body: dict[str, Any] = {
            "id": msg_id,
            "msg_id": msg_id,
            "sender": sender,
            "sender_name": sender,
            "from_wxid": str(getattr(msg, "sender", None) or sender),
            "receiver": "local_wechat",
            "to_wxid": "local_wechat",
            "conversation_id": room_id or chat_name or sender,
            "content": content,
            "text": content,
            "type": msg_type_code,
            "msg_type": str(msg_type_code),
            "ts": int(ts_ms / 1000),
            "bridge_backend": "wxautox4",
            "is_group_hint": is_group_hint,
        }

        if media_path:
            body["media_url"] = media_path

        if link_url:  # Phase 2: link message URL
            body["link_url"] = link_url

        callback_kwargs: dict[str, Any] = {
            "timeout_s": self._callback_timeout_s,
            "security": self._security,
        }
        if _post_to_edge_callback(self._edge_url, body, **callback_kwargs):
            self._stats["forwarded"] += 1
            logger.info("forwarded inbound: sender=%s content=%.80s", sender, content)
            # Phase 3: cache the raw message object for advanced ops (delete/capture/etc.)
            self._msg_cache[msg_id] = msg
            self._msg_cache_ts[msg_id] = time.monotonic()
        else:
            self._stats["errors"] += 1

    def _evict_expired_msg_cache(self) -> None:
        """Phase 3: Remove message objects older than PHASE3_MSG_CACHE_TTL_S."""
        now = time.monotonic()
        expired = [
            k for k, ts in self._msg_cache_ts.items()
            if (now - ts) > PHASE3_MSG_CACHE_TTL_S
        ]
        for k in expired:
            self._msg_cache.pop(k, None)
            self._msg_cache_ts.pop(k, None)
        if expired:
            logger.debug("msg_cache: evicted %d expired entries", len(expired))

    def _is_recent_duplicate(self, signature: str) -> bool:
        now_mono = time.monotonic()
        # Prune stale entries
        stale = [k for k, ts in self._recent_signatures.items() if (now_mono - ts) > self._dedup_window_s]
        for k in stale:
            self._recent_signatures.pop(k, None)
        if signature in self._recent_signatures:
            return True
        self._recent_signatures[signature] = now_mono
        return False


# ---------------------------------------------------------------------------
# Outbound processor — consumes JSONL command queue, executes wxautox4 API
# ---------------------------------------------------------------------------

class OutboundProcessor:
    """Polls the JSONL command queue and dispatches commands via wxautox4.

    Phase 1 additions:
      - get_friends        → wx.GetFriends()
      - get_groups         → wx.GetRecentGroups()  (or GetAllSubWindow groups)
      - get_recent_chats   → wx.GetSession() (recent session list)
      - get_history_messages → wx.GetHistoryMessage(who, savepath)
      - get_my_info        → wx.GetMyInfo()
      - listen_add         → wx.AddListenChat(who)
      - listen_remove      → wx.RemoveListenChat(who)
      - listen_stop        → wx.StopListening()
      - chat_with          → wx.ChatWith(who)
    """

    # All supported actions (Phase 1 additions marked with # P1, Phase 2 with # P2)
    SUPPORTED_ACTIONS = {
        # Original
        "send_text",
        "send_image",
        "send_file",
        "send_voice",
        # Phase 1 — query
        "get_friends",           # P1
        "get_groups",            # P1
        "get_recent_chats",      # P1
        "get_history_messages",  # P1
        "get_my_info",           # P1
        # Phase 1 — listen management
        "listen_add",            # P1
        "listen_remove",         # P1
        "listen_stop",           # P1
        # Phase 1 — navigation
        "chat_with",             # P1
        # Phase 2 — window & session
        "get_sub_window",        # P2
        "get_all_sub_windows",   # P2
        "get_chat_info",         # P2
        "get_dialog",            # P2
        # Phase 2 — messaging
        "send_url_card",         # P2
        "get_all_messages",      # P2
        # Phase 2 — contacts
        "get_new_friends",       # P2
        "get_friend_details",    # P2
        # Phase 3 — moments (朋友圈)
        "publish_moment",        # P3
        "get_moments",           # P3
        "like_moment",           # P3
        "comment_moment",        # P3
        # Phase 3 — group management
        "add_group_members",     # P3
        # Phase 3 — advanced message ops
        "delete_message",        # P3  (HumanMessage.delete — requires msg cache)
        "capture_emotion",       # P3  (EmotionMessage.capture)
        "download_quote_image",  # P3  (QuoteMessage.download_quote_image)
        # Phase 4 — call actions
        "voice_call",            # P4  wx.VoiceCall(who)
        "video_call",            # P4  wx.VideoCall(who)
        "cancel_call",           # P4  wx.CancelCall()
    }

    def __init__(
        self,
        wx: Any,
        *,
        command_queue_file: str,
        command_result_file: str,
        poll_interval_s: float = DEFAULT_COMMAND_POLL_INTERVAL_S,
        media_download_dir: str = DEFAULT_MEDIA_DOWNLOAD_DIR,  # Phase 1
        msg_cache: dict[str, Any] | None = None,              # Phase 3: shared msg object cache
        authority: BridgeCommandAuthority | None = None,
    ) -> None:
        if authority is not None and type(authority) is not BridgeCommandAuthority:
            raise TypeError("authority must be BridgeCommandAuthority")
        if authority is not None:
            configured_queue = os.path.normcase(
                str(Path(authority.config.queue_path).resolve())
            )
            configured_result = os.path.normcase(
                str(Path(authority.config.result_path).resolve())
            )
            actual_queue = os.path.normcase(str(Path(command_queue_file).resolve()))
            actual_result = os.path.normcase(str(Path(command_result_file).resolve()))
            if (
                configured_queue != actual_queue
                or configured_result != actual_result
            ):
                raise ValueError(BRIDGE_REASON_CONFIG_INVALID)
        self._wx = wx
        self._queue_file = command_queue_file
        self._result_file = command_result_file
        self._poll_interval_s = max(0.1, poll_interval_s)
        self._media_download_dir = _normalize_optional_path(media_download_dir)
        self._msg_cache: dict[str, Any] = msg_cache if msg_cache is not None else {}
        self._authority = authority
        self._shutdown = threading.Event()
        self._read_offset: int = 0
        self._discarding_oversized_row = False
        self.__dispatch_token = object()
        self._stats = {"executed": 0, "failed": 0, "unsupported": 0}

    def run(self) -> None:
        """Main command queue consumer loop — call from a daemon thread."""
        logger.info("OutboundProcessor started (poll=%.1fs)", self._poll_interval_s)
        while not self._shutdown.is_set():
            try:
                self._process_cycle()
            except Exception as exc:
                logger.warning("OutboundProcessor cycle error: %s", exc)
            self._shutdown.wait(timeout=self._poll_interval_s)
        logger.info("OutboundProcessor stopped. stats=%s", self._stats)

    def stop(self) -> None:
        self._shutdown.set()

    def _process_cycle(self) -> None:
        rows, new_offset, discarding = _read_jsonl_rows_from_offset(
            self._queue_file,
            self._read_offset,
            self._discarding_oversized_row,
        )
        if new_offset > self._read_offset:
            self._read_offset = new_offset
        self._discarding_oversized_row = discarding
        for cmd in rows:
            self._execute_command(cmd)

    def _execute_command(self, command: dict[str, Any]) -> None:
        now_ms = int(time.time() * 1000)
        if self._authority is None:
            decision = BridgeAuthorizationDecision(
                False,
                BRIDGE_REASON_DISABLED,
                _digest_text(command.get("command_id")),
                _digest_text(command.get("nonce")),
                "",
            )
        else:
            try:
                decision = self._authority.authorize(command)
            except Exception:
                decision = BridgeAuthorizationDecision(
                    False,
                    BRIDGE_REASON_AUTHORITY_UNAVAILABLE,
                    _digest_text(command.get("command_id")),
                    _digest_text(command.get("nonce")),
                    "",
                )
        if not decision.allowed:
            self._stats["failed"] += 1
            _append_jsonl_row(
                self._result_file,
                {
                    "command_id_digest": decision.command_id_digest,
                    "nonce_digest": decision.nonce_digest,
                    "body_sha256": decision.body_sha256,
                    "ts_ms": now_ms,
                    "success": False,
                    "status": "rejected",
                    "reason_code": decision.reason_code,
                    "detail": "bridge command rejected before device dispatch",
                },
            )
            return
        try:
            self.__execute_authorized_command(command, dispatch_token=self.__dispatch_token)
        finally:
            assert self._authority is not None
            self._authority.mark_terminal(decision, succeeded=True)

    def __execute_authorized_command(
        self,
        command: dict[str, Any],
        *,
        dispatch_token: object,
    ) -> None:
        if dispatch_token is not self.__dispatch_token:
            raise PermissionError(BRIDGE_REASON_AUTHORITY_UNAVAILABLE)
        now_ms = int(time.time() * 1000)
        command_id = str(command.get("command_id") or "").strip() or f"wxautox4-{now_ms}"
        request_id = str(command.get("request_id") or "").strip()
        action = str(command.get("action") or "").strip().lower()
        target = str(command.get("target") or "").strip()
        content = str(command.get("content") or "")

        # Resolve target candidates — first non-empty wins
        candidates = []
        for raw in [target, *list(command.get("target_candidates") or [])]:
            text = str(raw or "").strip()
            if text and text not in candidates:
                candidates.append(text)
        chat_target = candidates[0] if candidates else ""

        result: dict[str, Any] = {
            "command_id": command_id,
            "request_id": request_id,
            "action": action,
            "target": target,
            "content": content,
            "ts_ms": now_ms,
            "success": False,
            "status": "error",
            "reason_code": None,
            "detail": "",
        }

        try:
            if action == "send_text":
                if not chat_target:
                    result["reason_code"] = "WECHAT_NO_TARGET"
                    result["detail"] = "no chat target specified"
                    self._stats["failed"] += 1
                    logger.warning("command %s: no target", command_id)
                    _append_jsonl_row(self._result_file, result)
                    return
                self._wx.SendMsg(content, who=chat_target)
                result["success"] = True
                result["status"] = "ok"
                result["resolved_target"] = chat_target
                self._stats["executed"] += 1
                logger.info("command %s: send_text to '%s' OK", command_id, chat_target)

            elif action in ("send_image", "send_file", "send_voice"):
                if not chat_target:
                    result["reason_code"] = "WECHAT_NO_TARGET"
                    result["detail"] = "no chat target specified"
                    self._stats["failed"] += 1
                    _append_jsonl_row(self._result_file, result)
                    return
                media_path = str(command.get("media_path") or content or "").strip()
                if not media_path or not os.path.isfile(media_path):
                    result["reason_code"] = "WECHAT_MEDIA_FILE_NOT_FOUND"
                    result["detail"] = f"media file not found: {media_path}"
                    self._stats["failed"] += 1
                else:
                    self._wx.SendFiles(media_path, who=chat_target)
                    result["success"] = True
                    result["status"] = "ok"
                    result["resolved_target"] = chat_target
                    self._stats["executed"] += 1
                    logger.info("command %s: %s to '%s' OK", command_id, action, chat_target)

            # ── Phase 4: Call actions (VoiceCall / VideoCall / CancelCall) ──
            elif action == "voice_call":
                if not chat_target:
                    result["reason_code"] = "WECHAT_NO_TARGET"
                    result["detail"] = "voice_call requires a target"
                    self._stats["failed"] += 1
                    _append_jsonl_row(self._result_file, result)
                    return
                self._wx.VoiceCall(who=chat_target)
                result["success"] = True
                result["status"] = "ok"
                result["resolved_target"] = chat_target
                self._stats["executed"] += 1
                logger.info("command %s: voice_call to '%s' OK", command_id, chat_target)

            elif action == "video_call":
                if not chat_target:
                    result["reason_code"] = "WECHAT_NO_TARGET"
                    result["detail"] = "video_call requires a target"
                    self._stats["failed"] += 1
                    _append_jsonl_row(self._result_file, result)
                    return
                self._wx.VideoCall(who=chat_target)
                result["success"] = True
                result["status"] = "ok"
                result["resolved_target"] = chat_target
                self._stats["executed"] += 1
                logger.info("command %s: video_call to '%s' OK", command_id, chat_target)

            elif action == "cancel_call":
                self._wx.CancelCall()
                result["success"] = True
                result["status"] = "ok"
                self._stats["executed"] += 1
                logger.info("command %s: cancel_call OK", command_id)

            # ── Phase 1: Query actions ──────────────────────────────────────

            elif action == "get_my_info":
                info = self._wx.GetMyInfo()
                result["success"] = True
                result["status"] = "ok"
                result["data"] = _safe_serialize(info)
                self._stats["executed"] += 1
                logger.info("command %s: get_my_info OK", command_id)

            elif action == "get_friends":
                friends = self._wx.GetFriends()
                result["success"] = True
                result["status"] = "ok"
                result["data"] = _safe_serialize(friends)
                result["count"] = len(friends) if isinstance(friends, (list, tuple)) else None
                self._stats["executed"] += 1
                logger.info("command %s: get_friends OK count=%s", command_id, result["count"])

            elif action == "get_groups":
                # wxautox4 v40.1.x: GetRecentGroups() returns recently active groups
                try:
                    groups = self._wx.GetRecentGroups()
                except AttributeError:
                    # Fallback: some versions use a different method name
                    groups = self._wx.GetAllSubWindow()
                result["success"] = True
                result["status"] = "ok"
                result["data"] = _safe_serialize(groups)
                result["count"] = len(groups) if isinstance(groups, (list, tuple)) else None
                self._stats["executed"] += 1
                logger.info("command %s: get_groups OK count=%s", command_id, result["count"])

            elif action == "get_recent_chats":
                # GetSession() returns the recent chat sessions list
                sessions = self._wx.GetSession()
                result["success"] = True
                result["status"] = "ok"
                result["data"] = _safe_serialize(sessions)
                result["count"] = len(sessions) if isinstance(sessions, (list, tuple)) else None
                self._stats["executed"] += 1
                logger.info("command %s: get_recent_chats OK count=%s", command_id, result["count"])

            elif action == "get_history_messages":
                # Required: target (who to get history from)
                # Optional: command["savepath"] or use media_download_dir
                if not chat_target:
                    result["reason_code"] = "WECHAT_NO_TARGET"
                    result["detail"] = "get_history_messages requires a target"
                    self._stats["failed"] += 1
                    _append_jsonl_row(self._result_file, result)
                    return
                savepath = str(command.get("savepath") or self._media_download_dir or "").strip()
                if not savepath:
                    # Return messages as data without saving to file
                    history = self._wx.GetHistoryMessage(who=chat_target)
                else:
                    Path(savepath).mkdir(parents=True, exist_ok=True)
                    history = self._wx.GetHistoryMessage(who=chat_target, savepath=savepath)
                result["success"] = True
                result["status"] = "ok"
                result["data"] = _safe_serialize(history)
                result["count"] = len(history) if isinstance(history, (list, tuple)) else None
                self._stats["executed"] += 1
                logger.info("command %s: get_history_messages for '%s' OK count=%s", command_id, chat_target, result["count"])

            # ── Phase 1: Listen management ──────────────────────────────────

            elif action == "listen_add":
                if not chat_target:
                    result["reason_code"] = "WECHAT_NO_TARGET"
                    result["detail"] = "listen_add requires a target"
                    self._stats["failed"] += 1
                    _append_jsonl_row(self._result_file, result)
                    return
                self._wx.AddListenChat(who=chat_target)
                result["success"] = True
                result["status"] = "ok"
                result["resolved_target"] = chat_target
                self._stats["executed"] += 1
                logger.info("command %s: listen_add '%s' OK", command_id, chat_target)

            elif action == "listen_remove":
                if not chat_target:
                    result["reason_code"] = "WECHAT_NO_TARGET"
                    result["detail"] = "listen_remove requires a target"
                    self._stats["failed"] += 1
                    _append_jsonl_row(self._result_file, result)
                    return
                self._wx.RemoveListenChat(who=chat_target)
                result["success"] = True
                result["status"] = "ok"
                result["resolved_target"] = chat_target
                self._stats["executed"] += 1
                logger.info("command %s: listen_remove '%s' OK", command_id, chat_target)

            elif action == "listen_stop":
                self._wx.StopListening()
                result["success"] = True
                result["status"] = "ok"
                self._stats["executed"] += 1
                logger.info("command %s: listen_stop OK", command_id)

            # ── Phase 1: Navigation ─────────────────────────────────────────

            elif action == "chat_with":
                if not chat_target:
                    result["reason_code"] = "WECHAT_NO_TARGET"
                    result["detail"] = "chat_with requires a target"
                    self._stats["failed"] += 1
                    _append_jsonl_row(self._result_file, result)
                    return
                self._wx.ChatWith(who=chat_target)
                result["success"] = True
                result["status"] = "ok"
                result["resolved_target"] = chat_target
                self._stats["executed"] += 1
                logger.info("command %s: chat_with '%s' OK", command_id, chat_target)

            # ── Phase 2: Window & Session ───────────────────────────────────

            elif action == "get_sub_window":
                if not chat_target:
                    result["reason_code"] = "WECHAT_NO_TARGET"
                    result["detail"] = "get_sub_window requires a target"
                    self._stats["failed"] += 1
                    _append_jsonl_row(self._result_file, result)
                    return
                window = self._wx.GetSubWindow(chat_target)
                result["success"] = True
                result["status"] = "ok"
                result["data"] = _safe_serialize(window)
                self._stats["executed"] += 1
                logger.info("command %s: get_sub_window '%s' OK", command_id, chat_target)

            elif action == "get_all_sub_windows":
                windows = self._wx.GetAllSubWindow()
                result["success"] = True
                result["status"] = "ok"
                result["data"] = _safe_serialize(windows)
                result["count"] = len(windows) if isinstance(windows, (list, tuple)) else None
                self._stats["executed"] += 1
                logger.info("command %s: get_all_sub_windows OK count=%s", command_id, result["count"])

            elif action == "get_chat_info":
                # ChatInfo() returns info about the currently focused chat
                info = self._wx.ChatInfo()
                result["success"] = True
                result["status"] = "ok"
                result["data"] = _safe_serialize(info)
                self._stats["executed"] += 1
                logger.info("command %s: get_chat_info OK", command_id)

            elif action == "get_dialog":
                # GetDialog() detects and returns any open dialog/popup
                dialog = self._wx.GetDialog()
                result["success"] = True
                result["status"] = "ok"
                result["data"] = _safe_serialize(dialog)
                result["has_dialog"] = dialog is not None
                self._stats["executed"] += 1
                logger.info("command %s: get_dialog OK has_dialog=%s", command_id, result["has_dialog"])

            # ── Phase 2: Messaging ──────────────────────────────────────────

            elif action == "send_url_card":
                # Required fields: url, who (target); optional: title, desc
                if not chat_target:
                    result["reason_code"] = "WECHAT_NO_TARGET"
                    result["detail"] = "send_url_card requires a target"
                    self._stats["failed"] += 1
                    _append_jsonl_row(self._result_file, result)
                    return
                url = str(command.get("url") or content or "").strip()
                if not url:
                    result["reason_code"] = "WECHAT_MISSING_PARAMS"
                    result["detail"] = "send_url_card requires 'url' parameter"
                    self._stats["failed"] += 1
                    _append_jsonl_row(self._result_file, result)
                    return
                title = str(command.get("title") or "").strip()
                desc = str(command.get("desc") or command.get("description") or "").strip()
                self._wx.SendUrlCard(url, title, desc, who=chat_target)
                result["success"] = True
                result["status"] = "ok"
                result["resolved_target"] = chat_target
                self._stats["executed"] += 1
                logger.info("command %s: send_url_card to '%s' url=%s OK", command_id, chat_target, url[:60])

            elif action == "get_all_messages":
                # GetAllMessage() returns all messages in the current chat view
                all_msgs = self._wx.GetAllMessage()
                result["success"] = True
                result["status"] = "ok"
                result["data"] = _safe_serialize(all_msgs)
                result["count"] = len(all_msgs) if isinstance(all_msgs, (list, tuple)) else None
                self._stats["executed"] += 1
                logger.info("command %s: get_all_messages OK count=%s", command_id, result["count"])

            # ── Phase 2: Contacts ───────────────────────────────────────────

            elif action == "get_new_friends":
                # GetNewFriends() returns pending friend requests
                new_friends = self._wx.GetNewFriends()
                result["success"] = True
                result["status"] = "ok"
                result["data"] = _safe_serialize(new_friends)
                result["count"] = len(new_friends) if isinstance(new_friends, (list, tuple)) else None
                self._stats["executed"] += 1
                logger.info("command %s: get_new_friends OK count=%s", command_id, result["count"])

            elif action == "get_friend_details":
                if not chat_target:
                    result["reason_code"] = "WECHAT_NO_TARGET"
                    result["detail"] = "get_friend_details requires a target"
                    self._stats["failed"] += 1
                    _append_jsonl_row(self._result_file, result)
                    return
                details = self._wx.GetFriendDetails(chat_target)
                result["success"] = True
                result["status"] = "ok"
                result["data"] = _safe_serialize(details)
                self._stats["executed"] += 1
                logger.info("command %s: get_friend_details '%s' OK", command_id, chat_target)

            # ── Phase 3: Moments (朋友圈) ────────────────────────────────────

            elif action == "publish_moment":
                # content = moment text; command["images"] = list of file paths (optional)
                moment_text = content
                images = list(command.get("images") or [])
                # Filter to existing files
                valid_images = [p for p in images if p and os.path.isfile(str(p))]
                if images and not valid_images:
                    result["reason_code"] = "WECHAT_MEDIA_FILE_NOT_FOUND"
                    result["detail"] = f"None of the provided image paths exist: {images}"
                    self._stats["failed"] += 1
                    _append_jsonl_row(self._result_file, result)
                    return
                if valid_images:
                    self._wx.PublishMoment(moment_text, images=valid_images)
                else:
                    self._wx.PublishMoment(moment_text)
                result["success"] = True
                result["status"] = "ok"
                self._stats["executed"] += 1
                logger.info("command %s: publish_moment OK text=%.60s images=%d", command_id, moment_text, len(valid_images))

            elif action == "get_moments":
                # target = whose moments to fetch (empty = self timeline)
                who = chat_target or ""
                if who:
                    moments = self._wx.GetMoments(who)
                else:
                    moments = self._wx.GetMoments()
                result["success"] = True
                result["status"] = "ok"
                result["data"] = _safe_serialize(moments)
                result["count"] = len(moments) if isinstance(moments, (list, tuple)) else None
                self._stats["executed"] += 1
                logger.info("command %s: get_moments who='%s' OK count=%s", command_id, who, result["count"])

            elif action == "like_moment":
                moment_id = str(command.get("moment_id") or content or "").strip()
                if not moment_id:
                    result["reason_code"] = "WECHAT_MISSING_PARAMS"
                    result["detail"] = "like_moment requires 'moment_id'"
                    self._stats["failed"] += 1
                    _append_jsonl_row(self._result_file, result)
                    return
                self._wx.Like(moment_id)
                result["success"] = True
                result["status"] = "ok"
                result["moment_id"] = moment_id
                self._stats["executed"] += 1
                logger.info("command %s: like_moment moment_id=%s OK", command_id, moment_id)

            elif action == "comment_moment":
                moment_id = str(command.get("moment_id") or "").strip()
                comment_text = str(command.get("comment_text") or content or "").strip()
                if not moment_id:
                    result["reason_code"] = "WECHAT_MISSING_PARAMS"
                    result["detail"] = "comment_moment requires 'moment_id'"
                    self._stats["failed"] += 1
                    _append_jsonl_row(self._result_file, result)
                    return
                if not comment_text:
                    result["reason_code"] = "WECHAT_MISSING_PARAMS"
                    result["detail"] = "comment_moment requires 'comment_text' or 'content'"
                    self._stats["failed"] += 1
                    _append_jsonl_row(self._result_file, result)
                    return
                self._wx.Comment(moment_id, comment_text)
                result["success"] = True
                result["status"] = "ok"
                result["moment_id"] = moment_id
                self._stats["executed"] += 1
                logger.info("command %s: comment_moment moment_id=%s OK", command_id, moment_id)

            # ── Phase 3: Group management ────────────────────────────────────

            elif action == "add_group_members":
                # target = group name/wxid; command["members"] = list of wxids/names
                if not chat_target:
                    result["reason_code"] = "WECHAT_NO_TARGET"
                    result["detail"] = "add_group_members requires target (group name)"
                    self._stats["failed"] += 1
                    _append_jsonl_row(self._result_file, result)
                    return
                members = list(command.get("members") or [])
                if not members:
                    result["reason_code"] = "WECHAT_MISSING_PARAMS"
                    result["detail"] = "add_group_members requires 'members' list"
                    self._stats["failed"] += 1
                    _append_jsonl_row(self._result_file, result)
                    return
                self._wx.AddGroupMembers(chat_target, members)
                result["success"] = True
                result["status"] = "ok"
                result["resolved_target"] = chat_target
                result["members_added"] = len(members)
                self._stats["executed"] += 1
                logger.info("command %s: add_group_members group='%s' count=%d OK", command_id, chat_target, len(members))

            # ── Phase 3: Advanced message operations ─────────────────────────
            # NOTE: capture_emotion and download_quote_image operate on cached
            # message objects. The bridge maintains a _msg_cache keyed by
            # msg_id. Callers must provide the msg_id of a recently received
            # message. Cache TTL = PHASE3_MSG_CACHE_TTL_S seconds.

            elif action == "capture_emotion":
                msg_id_ref = str(command.get("msg_id_ref") or "").strip()
                cached_msg = self._msg_cache.get(msg_id_ref)
                if not cached_msg:
                    result["reason_code"] = "WECHAT_MSG_NOT_IN_CACHE"
                    result["detail"] = f"msg_id_ref '{msg_id_ref}' not found in message cache (may have expired)"
                    self._stats["failed"] += 1
                    _append_jsonl_row(self._result_file, result)
                    return
                save_dir = Path(self._media_download_dir or "runtime/edge/media")
                save_dir.mkdir(parents=True, exist_ok=True)
                save_path = str(save_dir / f"emotion_{int(time.time()*1000)}.png")
                local_path = cached_msg.capture(savepath=save_path)
                result["success"] = True
                result["status"] = "ok"
                result["local_path"] = str(local_path or save_path)
                self._stats["executed"] += 1
                logger.info("command %s: capture_emotion OK path=%s", command_id, result["local_path"])

            elif action == "download_quote_image":
                msg_id_ref = str(command.get("msg_id_ref") or "").strip()
                cached_msg = self._msg_cache.get(msg_id_ref)
                if not cached_msg:
                    result["reason_code"] = "WECHAT_MSG_NOT_IN_CACHE"
                    result["detail"] = f"msg_id_ref '{msg_id_ref}' not found in message cache"
                    self._stats["failed"] += 1
                    _append_jsonl_row(self._result_file, result)
                    return
                save_dir = Path(self._media_download_dir or "runtime/edge/media")
                save_dir.mkdir(parents=True, exist_ok=True)
                local_path = cached_msg.download_quote_image(savepath=str(save_dir))
                result["success"] = True
                result["status"] = "ok"
                result["local_path"] = str(local_path or "")
                self._stats["executed"] += 1
                logger.info("command %s: download_quote_image OK path=%s", command_id, result["local_path"])

            elif action == "delete_message":
                msg_id_ref = str(command.get("msg_id_ref") or "").strip()
                cached_msg = self._msg_cache.get(msg_id_ref)
                if not cached_msg:
                    result["reason_code"] = "WECHAT_MSG_NOT_IN_CACHE"
                    result["detail"] = f"msg_id_ref '{msg_id_ref}' not found in message cache"
                    self._stats["failed"] += 1
                    _append_jsonl_row(self._result_file, result)
                    return
                cached_msg.delete()
                # Remove from cache after deletion
                self._msg_cache.pop(msg_id_ref, None)
                result["success"] = True
                result["status"] = "ok"
                self._stats["executed"] += 1
                logger.info("command %s: delete_message msg_id_ref=%s OK", command_id, msg_id_ref)

            else:
                result["reason_code"] = "WECHAT_UNKNOWN_ACTION"
                result["detail"] = f"unknown action: {action}"
                self._stats["unsupported"] += 1
                logger.warning("command %s: unknown action '%s'", command_id, action)

        except Exception as exc:
            result["reason_code"] = "WECHAT_WXAUTOX4_ERROR"
            result["detail"] = str(exc)
            self._stats["failed"] += 1
            logger.error("command %s: wxautox4 error: %s", command_id, exc)

        _append_jsonl_row(self._result_file, result)


# ---------------------------------------------------------------------------
# Main Bridge orchestrator
# ---------------------------------------------------------------------------

class WxAutoX4Bridge:
    """Top-level bridge that coordinates inbound + outbound processors.

    Lifecycle:
        bridge = WxAutoX4Bridge(...)
        bridge.start()   # blocks until shutdown

    Phase 1 additions:
        - HealthMonitor thread (IsOnline keepalive)
        - GetMyInfo() log at startup
        - media_download_dir support
        - health_check_interval_s config
    """

    def __init__(
        self,
        *,
        edge_url: str = DEFAULT_EDGE_URL,
        command_queue_file: str,
        command_result_file: str,
        command_poll_interval_s: float = DEFAULT_COMMAND_POLL_INTERVAL_S,
        inbound_poll_interval_s: float = DEFAULT_INBOUND_POLL_INTERVAL_S,
        callback_timeout_s: float = DEFAULT_CALLBACK_TIMEOUT_S,
        dedup_window_s: float = DEFAULT_DEDUP_WINDOW_S,
        listen_targets: list[str] | None = None,
        health_check_interval_s: float = DEFAULT_HEALTH_CHECK_INTERVAL_S,  # Phase 1
        media_download_dir: str = DEFAULT_MEDIA_DOWNLOAD_DIR,              # Phase 1
        exposure_config: BridgeExposureConfig | None = None,
    ) -> None:
        self._edge_url = edge_url
        self._queue_file = command_queue_file
        self._result_file = command_result_file
        self._command_poll_interval_s = command_poll_interval_s
        self._inbound_poll_interval_s = inbound_poll_interval_s
        self._callback_timeout_s = callback_timeout_s
        self._dedup_window_s = dedup_window_s
        self._listen_targets = list(listen_targets or [])
        self._health_check_interval_s = health_check_interval_s
        self._media_download_dir = _normalize_optional_path(media_download_dir)
        self._shutdown = threading.Event()
        self._exposure = exposure_config or load_bridge_exposure_config(
            edge_url=edge_url,
            command_queue_file=command_queue_file,
            command_result_file=command_result_file,
        )

    def start(self) -> int:
        """Initialize wxautox4 and run the bridge. Blocks until interrupted.

        Returns exit code (0 = clean, 1 = error).
        """
        if not self._exposure.enabled:
            logger.error("wxautox4 bridge blocked: %s", self._exposure.reason_code)
            return 1
        if self._exposure.edge_url != str(self._edge_url or "").strip().rstrip("/"):
            logger.error("wxautox4 bridge blocked: %s", BRIDGE_REASON_CONFIG_INVALID)
            return 1
        if (
            os.path.normcase(str(Path(self._exposure.queue_path).resolve()))
            != os.path.normcase(str(Path(self._queue_file).resolve()))
            or os.path.normcase(str(Path(self._exposure.result_path).resolve()))
            != os.path.normcase(str(Path(self._result_file).resolve()))
        ):
            logger.error("wxautox4 bridge blocked: %s", BRIDGE_REASON_CONFIG_INVALID)
            return 1
        try:
            authority = BridgeCommandAuthority(self._exposure)
        except Exception as exc:
            logger.error(
                "wxautox4 bridge authority unavailable before device binding: %s",
                type(exc).__name__,
            )
            return 1

        logger.info("=" * 60)
        logger.info("wxautox4 Bridge for Natsume_Ai  [Phase 1]")
        logger.info("=" * 60)

        # ── Step 1: import and initialize wxautox4 ──
        WeChat = None
        try:
            from wxautox4 import WeChat  # type: ignore[import-untyped]
        except ImportError:
            try:
                from wxautox4_wechatbot import WeChat  # type: ignore[import-untyped]
            except ImportError:
                WeChat = None
        if WeChat is None:
            logger.error(
                "wxautox4 not installed! Install the official package or vendor wheel, then activate it"
            )
            return 1

        logger.info("Initializing wxautox4.WeChat() — binding to WeChat window...")
        try:
            wx = WeChat()
        except Exception as exc:
            logger.error("Failed to initialize wxautox4.WeChat(): %s", exc)
            logger.error("Make sure WeChat 4.1+ is running and wxautox4 is activated.")
            return 1
        logger.info("wxautox4 bound to WeChat window successfully!")

        # ── Step 2: Phase 1 — log self info at startup ──
        try:
            my_info = wx.GetMyInfo()
            logger.info("WeChat self info: %s", _safe_serialize(my_info))
        except Exception as exc:
            logger.warning("GetMyInfo() failed (non-fatal): %s", exc)

        # ── Step 3: start HealthMonitor ──
        health = HealthMonitor(wx, check_interval_s=self._health_check_interval_s)
        health_thread = threading.Thread(
            target=health.run,
            name="wxautox4-health",
            daemon=True,
        )
        health_thread.start()

        # ── Step 4: start inbound processor ──
        # Phase 3: create a shared message-object cache between inbound & outbound
        shared_msg_cache: dict[str, Any] = {}

        inbound = InboundProcessor(
            wx,
            edge_url=self._edge_url,
            poll_interval_s=self._inbound_poll_interval_s,
            dedup_window_s=self._dedup_window_s,
            callback_timeout_s=self._callback_timeout_s,
            listen_targets=self._listen_targets,
            media_download_dir=self._media_download_dir,
            msg_cache=shared_msg_cache,
            security=self._exposure,
        )
        inbound.setup_listeners()

        inbound_thread = threading.Thread(
            target=inbound.run,
            name="wxautox4-inbound",
            daemon=True,
        )
        inbound_thread.start()

        # ── Step 5: start outbound processor ──
        outbound = OutboundProcessor(
            wx,
            command_queue_file=self._queue_file,
            command_result_file=self._result_file,
            poll_interval_s=self._command_poll_interval_s,
            media_download_dir=self._media_download_dir,
            msg_cache=shared_msg_cache,
            authority=authority,
        )

        outbound_thread = threading.Thread(
            target=outbound.run,
            name="wxautox4-outbound",
            daemon=True,
        )
        outbound_thread.start()

        # ── Step 6: keep alive ──
        logger.info("wxautox4 Bridge [Phase 1] is running. Press Ctrl+C to stop.")
        try:
            while not self._shutdown.is_set():
                self._shutdown.wait(timeout=1.0)
        except KeyboardInterrupt:
            logger.info("Received interrupt signal, shutting down...")

        health.stop()
        inbound.stop()
        outbound.stop()
        logger.info("wxautox4 Bridge stopped.")
        return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="wxautox4 Bridge for Natsume_Ai [Phase 1]",
    )
    parser.add_argument(
        "--edge-url",
        default=(os.environ.get("EDGE_URL") or DEFAULT_EDGE_URL).strip(),
        help="Gateway base URL (default: http://127.0.0.1:19000)",
    )
    parser.add_argument(
        "--command-poll-interval",
        type=float,
        default=float(
            os.environ.get("EDGE_WECHAT_COMMAND_POLL_INTERVAL_S") or DEFAULT_COMMAND_POLL_INTERVAL_S,
        ),
        help="Command queue poll interval in seconds",
    )
    parser.add_argument(
        "--command-queue-file",
        default=_normalize_optional_path(os.environ.get("EDGE_WECHAT_COMMAND_QUEUE_FILE") or ""),
        help="Path to JSONL command queue file",
    )
    parser.add_argument(
        "--command-result-file",
        default=_normalize_optional_path(os.environ.get("EDGE_WECHAT_COMMAND_RESULT_FILE") or ""),
        help="Path to JSONL command result file",
    )
    parser.add_argument(
        "--callback-timeout",
        type=float,
        default=float(
            os.environ.get("EDGE_WECHAT_CALLBACK_TIMEOUT_S") or DEFAULT_CALLBACK_TIMEOUT_S,
        ),
        help="Webhook callback timeout in seconds",
    )
    parser.add_argument(
        "--inbound-poll-interval",
        type=float,
        default=float(
            os.environ.get("EDGE_WECHAT_INBOUND_POLL_INTERVAL_S") or DEFAULT_INBOUND_POLL_INTERVAL_S,
        ),
        help="Inbound message poll interval in seconds",
    )
    parser.add_argument(
        "--dedup-window",
        type=float,
        default=float(
            os.environ.get("EDGE_WECHAT_LISTENER_DEDUP_WINDOW_S") or DEFAULT_DEDUP_WINDOW_S,
        ),
        help="Deduplication window in seconds",
    )
    parser.add_argument(
        "--listen-targets",
        nargs="*",
        default=[
            t.strip()
            for t in str(os.environ.get("EDGE_WECHAT_WXAUTOX4_LISTEN_TARGETS") or "").split(",")
            if t.strip()
        ],
        help="Chat targets to listen to (space-separated); empty = rely on GetNewMessage()",
    )
    # Phase 1 new args
    parser.add_argument(
        "--health-check-interval",
        type=float,
        default=float(
            os.environ.get("EDGE_WECHAT_WXAUTOX4_HEALTH_INTERVAL_S") or DEFAULT_HEALTH_CHECK_INTERVAL_S,
        ),
        help="IsOnline health check interval in seconds (default: 30)",
    )
    parser.add_argument(
        "--media-download-dir",
        default=_normalize_optional_path(os.environ.get("EDGE_WECHAT_WXAUTOX4_MEDIA_DOWNLOAD_DIR") or ""),
        help="Directory to auto-download inbound media files (empty = disabled)",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    # Resolve command queue/result file paths (same logic as bootstrap.py)
    repo_root = Path(__file__).resolve().parents[1]
    runtime_edge = repo_root / "runtime" / "edge"
    runtime_edge.mkdir(parents=True, exist_ok=True)

    queue_file = args.command_queue_file or str(runtime_edge / "wechat_command_queue.jsonl")
    result_file = args.command_result_file or str(runtime_edge / "wechat_command_result.jsonl")

    bridge = WxAutoX4Bridge(
        edge_url=args.edge_url,
        command_queue_file=queue_file,
        command_result_file=result_file,
        command_poll_interval_s=args.command_poll_interval,
        inbound_poll_interval_s=args.inbound_poll_interval,
        callback_timeout_s=args.callback_timeout,
        dedup_window_s=args.dedup_window,
        listen_targets=args.listen_targets,
        health_check_interval_s=args.health_check_interval,
        media_download_dir=args.media_download_dir,
    )
    return bridge.start()


if __name__ == "__main__":
    sys.exit(main())
