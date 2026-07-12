from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
import secrets
import socket
import sqlite3
import sys
import threading
import time
import ctypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from natsume_contracts.bridge_auth import (
    BRIDGE_AUTH_VERSION as AUTH_VERSION,
    HTTP_AUTH_HEADERS,
    body_sha256,
    build_hmac_signature,
    canonical_hmac_payload,
)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


EXPOSURE_MODE_DISABLED = "DISABLED"
EXPOSURE_MODE_LOOPBACK = "LOOPBACK"
EXPOSURE_MODE_REMOTE_APPROVED = "REMOTE_APPROVED"
EXPOSURE_MODES = frozenset(
    {
        EXPOSURE_MODE_DISABLED,
        EXPOSURE_MODE_LOOPBACK,
        EXPOSURE_MODE_REMOTE_APPROVED,
    }
)
PROTOCOL_VERSION = "1"
BRIDGE_CONSTRUCTION_FAILED = "BRIDGE_CONSTRUCTION_FAILED"
SCOPE_READ = "bridge:mateengine:read"
SCOPE_SAY = "bridge:mateengine:say"
SCOPE_EXPRESSION = "bridge:mateengine:expression"
SCOPE_MOTION = "bridge:mateengine:motion"
SCOPE_TRANSFORM = "bridge:mateengine:transform"
SCOPE_PLUGIN = "bridge:mateengine:plugin"
SCOPE_ADMIN = "bridge:mateengine:admin"
KNOWN_SCOPES = frozenset(
    {
        SCOPE_READ,
        SCOPE_SAY,
        SCOPE_EXPRESSION,
        SCOPE_MOTION,
        SCOPE_TRANSFORM,
        SCOPE_PLUGIN,
        SCOPE_ADMIN,
    }
)
SAFE_CANONICAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")


class BridgeConstructionError(RuntimeError):
    """Stable startup rejection that never exposes filesystem/provider detail."""

    reason_code = BRIDGE_CONSTRUCTION_FAILED

    def __init__(self) -> None:
        super().__init__(self.reason_code)

HTTP_HOST = os.getenv("MATEENGINE_BRIDGE_HTTP_HOST", "127.0.0.1").strip() or "127.0.0.1"
HTTP_PORT = _env_int("MATEENGINE_BRIDGE_HTTP_PORT", 18770)
UDP_HOST = os.getenv("MATEENGINE_BRIDGE_UDP_HOST", "127.0.0.1").strip() or "127.0.0.1"
UDP_PORT = _env_int("MATEENGINE_BRIDGE_UDP_PORT", 18771)
MATEENGINE_HOST = os.getenv("MATEENGINE_SIGNAL_HOST", "127.0.0.1").strip() or "127.0.0.1"
MATEENGINE_PORT = _env_int("MATEENGINE_SIGNAL_PORT", 32145)
MATEENGINE_PLUGIN_HOST = os.getenv("MATEENGINE_PLUGIN_HOST", "127.0.0.1").strip() or "127.0.0.1"
MATEENGINE_PLUGIN_PORT = _env_int("MATEENGINE_PLUGIN_PORT", 32146)
WINDOW_MOTION_ENABLED = _env_bool("MATEENGINE_WINDOW_MOTION_ENABLED", False)
WINDOW_TITLE = os.getenv("MATEENGINE_WINDOW_TITLE", "MateEngineX").strip() or "MateEngineX"
WINDOW_MOTION_SCALE_X = _env_float("MATEENGINE_WINDOW_MOTION_SCALE_X", 700.0)
WINDOW_MOTION_SCALE_Y = _env_float("MATEENGINE_WINDOW_MOTION_SCALE_Y", 520.0)
WINDOW_MOTION_MAX_OFFSET_X = _env_int("MATEENGINE_WINDOW_MOTION_MAX_OFFSET_X", 260)
WINDOW_MOTION_MAX_OFFSET_Y = _env_int("MATEENGINE_WINDOW_MOTION_MAX_OFFSET_Y", 180)
WINDOW_HOME_X = os.getenv("MATEENGINE_WINDOW_HOME_X")
WINDOW_HOME_Y = os.getenv("MATEENGINE_WINDOW_HOME_Y")


def _mapping_text(source: Mapping[str, str], name: str, default: str = "") -> str:
    value = source.get(name)
    return str(default if value is None else value).strip()


def _mapping_bool(source: Mapping[str, str], name: str, default: bool) -> bool:
    raw = source.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off", ""}


def _mapping_int(source: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(_mapping_text(source, name, str(default)))
    except ValueError:
        return 0


def _mapping_float(source: Mapping[str, str], name: str, default: float) -> float:
    try:
        return float(_mapping_text(source, name, str(default)))
    except ValueError:
        return 0.0


def _is_loopback_host(value: str) -> bool:
    normalized = str(value or "").strip().lower().rstrip(".")
    if normalized == "localhost":
        return True
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    try:
        return bool(ipaddress.ip_address(normalized).is_loopback)
    except ValueError:
        return False


def _filesystem_paths_collide(paths: Sequence[Path]) -> bool:
    normalized = [os.path.normcase(os.path.normpath(str(path.resolve()))) for path in paths]
    if len(set(normalized)) != len(normalized):
        return True
    existing = [path for path in paths if path.exists()]
    for index, left in enumerate(existing):
        for right in existing[index + 1 :]:
            try:
                if os.path.samefile(left, right):
                    return True
            except OSError:
                return True
    return False


def _split_scopes(value: str) -> tuple[str, ...]:
    normalized = sorted(
        {
            item.strip()
            for item in str(value or "").replace(" ", ",").split(",")
            if item.strip()
        }
    )
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class BridgeSecurityConfig:
    exposure_mode: str
    mateengine_enabled: bool
    http_host: str
    http_port: int
    udp_host: str
    udp_port: int
    udp_enabled: bool
    signal_host: str
    signal_port: int
    plugin_host: str
    plugin_port: int
    approval_ref: str
    key_id: str
    hmac_secret: bytes
    plugin_key_id: str
    plugin_hmac_secret: bytes
    allowed_principals: frozenset[str]
    allowed_scopes: frozenset[str]
    rate_per_second: float
    rate_burst: int
    rate_per_second_explicit: bool
    rate_burst_explicit: bool
    preauth_rate_per_second: float
    preauth_rate_burst: int
    nonce_ttl_seconds: int
    max_body_bytes: int
    audit_path: Path | None
    replay_ledger_path: Path | None
    audit_max_bytes: int
    audit_archive_count: int
    service_principal: str


def build_security_config(
    environ: Mapping[str, str] | None = None,
) -> BridgeSecurityConfig:
    source = os.environ if environ is None else environ
    mode = _mapping_text(
        source,
        "NATSUME_BRIDGE_EXPOSURE_MODE",
        EXPOSURE_MODE_DISABLED,
    ).upper()
    raw_audit_path = _mapping_text(source, "NATSUME_MATEENGINE_BRIDGE_AUDIT_JSONL_PATH")
    raw_replay_path = _mapping_text(source, "NATSUME_MATEENGINE_BRIDGE_REPLAY_LEDGER_PATH")
    return BridgeSecurityConfig(
        exposure_mode=mode,
        mateengine_enabled=_mapping_bool(
            source,
            "HOST_MATEENGINE_BRIDGE_ENABLED",
            False,
        ),
        http_host=_mapping_text(
            source,
            "MATEENGINE_BRIDGE_HTTP_HOST",
            "127.0.0.1",
        )
        or "127.0.0.1",
        http_port=_mapping_int(source, "MATEENGINE_BRIDGE_HTTP_PORT", 18770),
        udp_host=_mapping_text(
            source,
            "MATEENGINE_BRIDGE_UDP_HOST",
            "127.0.0.1",
        )
        or "127.0.0.1",
        udp_port=_mapping_int(source, "MATEENGINE_BRIDGE_UDP_PORT", 18771),
        udp_enabled=_mapping_bool(
            source,
            "MATEENGINE_BRIDGE_UDP_ENABLED",
            False,
        ),
        signal_host=_mapping_text(
            source,
            "MATEENGINE_SIGNAL_HOST",
            "127.0.0.1",
        )
        or "127.0.0.1",
        signal_port=_mapping_int(source, "MATEENGINE_SIGNAL_PORT", 32145),
        plugin_host=_mapping_text(
            source,
            "MATEENGINE_PLUGIN_HOST",
            "127.0.0.1",
        )
        or "127.0.0.1",
        plugin_port=_mapping_int(source, "MATEENGINE_PLUGIN_PORT", 32146),
        approval_ref=_mapping_text(source, "NATSUME_MATEENGINE_BRIDGE_REMOTE_APPROVAL_REF"),
        key_id=_mapping_text(source, "NATSUME_MATEENGINE_BRIDGE_HMAC_KEY_ID"),
        hmac_secret=_mapping_text(source, "NATSUME_MATEENGINE_BRIDGE_HMAC_SECRET").encode(
            "utf-8"
        ),
        plugin_key_id=_mapping_text(
            source,
            "NATSUME_MATEENGINE_PLUGIN_HMAC_KEY_ID",
        ),
        plugin_hmac_secret=_mapping_text(
            source,
            "NATSUME_MATEENGINE_PLUGIN_HMAC_SECRET",
        ).encode("utf-8"),
        allowed_principals=frozenset(
            _split_scopes(_mapping_text(source, "NATSUME_MATEENGINE_BRIDGE_ALLOWED_PRINCIPALS"))
        ),
        allowed_scopes=frozenset(
            _split_scopes(_mapping_text(source, "NATSUME_MATEENGINE_BRIDGE_ALLOWED_SCOPES"))
        ),
        rate_per_second=_mapping_float(
            source,
            "NATSUME_BRIDGE_RATE_PER_SECOND",
            10.0,
        ),
        rate_burst=_mapping_int(source, "NATSUME_BRIDGE_RATE_BURST", 20),
        rate_per_second_explicit=bool(
            _mapping_text(source, "NATSUME_BRIDGE_RATE_PER_SECOND")
        ),
        rate_burst_explicit=bool(
            _mapping_text(source, "NATSUME_BRIDGE_RATE_BURST")
        ),
        preauth_rate_per_second=_mapping_float(
            source,
            "NATSUME_BRIDGE_PREAUTH_RATE_PER_SECOND",
            50.0,
        ),
        preauth_rate_burst=_mapping_int(
            source,
            "NATSUME_BRIDGE_PREAUTH_RATE_BURST",
            100,
        ),
        nonce_ttl_seconds=_mapping_int(
            source,
            "NATSUME_BRIDGE_NONCE_TTL_SECONDS",
            30,
        ),
        max_body_bytes=_mapping_int(
            source,
            "NATSUME_BRIDGE_MAX_BODY_BYTES",
            65_536,
        ),
        audit_path=(Path(raw_audit_path).expanduser().resolve() if raw_audit_path else None),
        replay_ledger_path=(
            Path(raw_replay_path).expanduser().resolve()
            if raw_replay_path
            else None
        ),
        audit_max_bytes=_mapping_int(
            source,
            "NATSUME_MATEENGINE_BRIDGE_AUDIT_MAX_BYTES",
            64 * 1024 * 1024,
        ),
        audit_archive_count=_mapping_int(
            source,
            "NATSUME_MATEENGINE_BRIDGE_AUDIT_ARCHIVES",
            8,
        ),
        service_principal=_mapping_text(
            source,
            "NATSUME_MATEENGINE_PLUGIN_PRINCIPAL",
            "host.mateengine.bridge",
        )
        or "host.mateengine.bridge",
    )


def validate_security_config(config: BridgeSecurityConfig) -> tuple[str, ...]:
    errors: list[str] = []
    if config.exposure_mode not in EXPOSURE_MODES:
        return ("NATSUME_BRIDGE_EXPOSURE_MODE must be DISABLED, LOOPBACK or REMOTE_APPROVED",)
    if config.exposure_mode == EXPOSURE_MODE_DISABLED:
        return ()
    if not config.mateengine_enabled:
        errors.append("HOST_MATEENGINE_BRIDGE_ENABLED must be explicitly true")
    for label, port in (
        ("HTTP", config.http_port),
        ("UDP", config.udp_port),
        ("MateEngine signal", config.signal_port),
        ("MateEngine plugin", config.plugin_port),
    ):
        if int(port) <= 0 or int(port) > 65_535:
            errors.append(f"{label} port must be between 1 and 65535")
    if config.exposure_mode == EXPOSURE_MODE_LOOPBACK:
        if not _is_loopback_host(config.http_host):
            errors.append("LOOPBACK mode requires a loopback HTTP bind")
        if config.udp_enabled and not _is_loopback_host(config.udp_host):
            errors.append("LOOPBACK mode requires a loopback UDP bind")
    if not _is_loopback_host(config.signal_host):
        errors.append("MateEngine signal downstream must be loopback")
    if not _is_loopback_host(config.plugin_host):
        errors.append("MateEngine plugin downstream must be loopback")
    if len(config.hmac_secret) < 32:
        errors.append("NATSUME_MATEENGINE_BRIDGE_HMAC_SECRET must contain at least 32 UTF-8 bytes")
    if not SAFE_CANONICAL_ID.fullmatch(config.key_id):
        errors.append("NATSUME_MATEENGINE_BRIDGE_HMAC_KEY_ID is required and must be at most 128 characters")
    if len(config.plugin_hmac_secret) < 32:
        errors.append("NATSUME_MATEENGINE_PLUGIN_HMAC_SECRET must contain at least 32 UTF-8 bytes")
    if not SAFE_CANONICAL_ID.fullmatch(config.plugin_key_id):
        errors.append("NATSUME_MATEENGINE_PLUGIN_HMAC_KEY_ID is required")
    if hmac.compare_digest(config.hmac_secret, config.plugin_hmac_secret):
        errors.append("MateEngine ingress and plugin HMAC secrets must be distinct")
    if config.key_id == config.plugin_key_id:
        errors.append("MateEngine ingress and plugin HMAC key IDs must be distinct")
    if not config.allowed_principals:
        errors.append("NATSUME_MATEENGINE_BRIDGE_ALLOWED_PRINCIPALS must be explicit and non-empty")
    elif any(not SAFE_CANONICAL_ID.fullmatch(item) for item in config.allowed_principals):
        errors.append("NATSUME_MATEENGINE_BRIDGE_ALLOWED_PRINCIPALS contains an invalid principal")
    if not config.allowed_scopes:
        errors.append("NATSUME_MATEENGINE_BRIDGE_ALLOWED_SCOPES must be explicit and non-empty")
    unknown_scopes = sorted(config.allowed_scopes - KNOWN_SCOPES)
    if unknown_scopes:
        errors.append("unknown bridge scopes: " + ",".join(unknown_scopes))
    if config.rate_per_second <= 0:
        errors.append("NATSUME_BRIDGE_RATE_PER_SECOND must be positive")
    if config.rate_burst <= 0:
        errors.append("NATSUME_BRIDGE_RATE_BURST must be positive")
    if config.preauth_rate_per_second <= 0:
        errors.append("NATSUME_BRIDGE_PREAUTH_RATE_PER_SECOND must be positive")
    if config.preauth_rate_burst <= 0:
        errors.append("NATSUME_BRIDGE_PREAUTH_RATE_BURST must be positive")
    if config.preauth_rate_per_second > 10_000 or config.preauth_rate_burst > 10_000:
        errors.append("MateEngine pre-auth rate controls exceed safe bounds")
    if config.nonce_ttl_seconds <= 0 or config.nonce_ttl_seconds > 3600:
        errors.append("NATSUME_BRIDGE_NONCE_TTL_SECONDS must be between 1 and 3600")
    if config.max_body_bytes <= 0 or config.max_body_bytes > 1_048_576:
        errors.append("NATSUME_BRIDGE_MAX_BODY_BYTES must be between 1 and 1048576")
    if config.audit_path is None:
        errors.append("NATSUME_MATEENGINE_BRIDGE_AUDIT_JSONL_PATH is required")
    if config.replay_ledger_path is None:
        errors.append("NATSUME_MATEENGINE_BRIDGE_REPLAY_LEDGER_PATH is required")
    if config.audit_path is not None and config.replay_ledger_path is not None:
        reserved_paths = [
            config.audit_path,
            *[
                Path(f"{config.audit_path}.{index}")
                for index in range(
                    1,
                    min(32, max(1, config.audit_archive_count)) + 1,
                )
            ],
            config.replay_ledger_path,
            Path(f"{config.replay_ledger_path}-wal"),
            Path(f"{config.replay_ledger_path}-shm"),
            Path(f"{config.replay_ledger_path}-journal"),
        ]
        if _filesystem_paths_collide(reserved_paths):
            errors.append("MateEngine audit and replay ledger path families must be distinct")
    if not (1024 * 1024 <= config.audit_max_bytes <= 1024 * 1024 * 1024):
        errors.append("NATSUME_MATEENGINE_BRIDGE_AUDIT_MAX_BYTES is invalid")
    if not (1 <= config.audit_archive_count <= 32):
        errors.append("NATSUME_MATEENGINE_BRIDGE_AUDIT_ARCHIVES is invalid")
    if not SAFE_CANONICAL_ID.fullmatch(config.service_principal):
        errors.append("NATSUME_MATEENGINE_PLUGIN_PRINCIPAL is invalid")
    if (
        config.exposure_mode == EXPOSURE_MODE_REMOTE_APPROVED
        and not config.approval_ref
    ):
        errors.append("REMOTE_APPROVED requires NATSUME_MATEENGINE_BRIDGE_REMOTE_APPROVAL_REF")
    if config.exposure_mode == EXPOSURE_MODE_REMOTE_APPROVED:
        if not config.rate_per_second_explicit:
            errors.append("REMOTE_APPROVED requires explicit NATSUME_BRIDGE_RATE_PER_SECOND")
        if not config.rate_burst_explicit:
            errors.append("REMOTE_APPROVED requires explicit NATSUME_BRIDGE_RATE_BURST")
    return tuple(errors)


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    status: int
    reason: str
    principal: str = ""
    scopes: tuple[str, ...] = ()
    nonce: str = ""
    body_digest: str = ""
    signed_timestamp_ms: int = 0


class DurableJsonlAudit:
    def __init__(self, path: Path, *, max_bytes: int, archive_count: int) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise OSError("bridge audit path must not be a symlink")
        self.max_bytes = int(max_bytes)
        self.archive_count = int(archive_count)
        self._lock = threading.Lock()
        self._poisoned = False
        self._validate_existing()
        self.append(
            {
                "event": "audit_ready",
                "ts_ms": int(time.time() * 1000),
                "protocol_version": PROTOCOL_VERSION,
            }
        )

    def append(self, row: Mapping[str, Any]) -> None:
        if self._poisoned:
            raise OSError("bridge audit writer is poisoned")
        encoded = json.dumps(
            dict(row),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._lock:
            try:
                self._rotate_if_needed(len((encoded + "\n").encode("utf-8")))
                with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(encoded + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            except OSError:
                self._poisoned = True
                raise

    def _validate_existing(self) -> None:
        for audit_path in [
            self.path,
            *[Path(f"{self.path}.{index}") for index in range(1, self.archive_count + 1)],
        ]:
            if not audit_path.exists():
                continue
            if audit_path.stat().st_size:
                with audit_path.open("rb") as binary_stream:
                    binary_stream.seek(-1, os.SEEK_END)
                    if binary_stream.read(1) != b"\n":
                        raise ValueError(
                            "bridge audit contains a torn terminal row"
                        )
            with audit_path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                    except (json.JSONDecodeError, UnicodeError) as exc:
                        raise ValueError("bridge audit contains malformed JSON") from exc
                    if not isinstance(row, dict):
                        raise ValueError("bridge audit row must be an object")

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        current_size = self.path.stat().st_size if self.path.exists() else 0
        if current_size + int(incoming_bytes) <= self.max_bytes:
            return
        oldest = Path(f"{self.path}.{self.archive_count}")
        oldest.unlink(missing_ok=True)
        for index in range(self.archive_count - 1, 0, -1):
            source = Path(f"{self.path}.{index}")
            if source.exists():
                source.replace(Path(f"{self.path}.{index + 1}"))
        if self.path.exists():
            self.path.replace(Path(f"{self.path}.1"))

    def recent_nonce_claims(
        self,
        *,
        ttl_seconds: int,
        now_ms: int,
    ) -> dict[tuple[str, str], int]:
        claims: dict[tuple[str, str], int] = {}
        cutoff = int(now_ms) - int(ttl_seconds * 1000)
        try:
            paths = [self.path, *[Path(f"{self.path}.{index}") for index in range(1, self.archive_count + 1)]]
            for audit_path in paths:
                if not audit_path.exists():
                    continue
                with audit_path.open("r", encoding="utf-8") as stream:
                    for line in stream:
                        try:
                            row = json.loads(line)
                        except (json.JSONDecodeError, UnicodeError) as exc:
                            raise ValueError("bridge audit contains malformed JSON") from exc
                        if not isinstance(row, dict) or row.get("event") != "command_accepted":
                            continue
                        claimed_at = int(row.get("ts_ms") or 0)
                        principal = str(row.get("principal") or "")
                        nonce_digest = str(row.get("nonce_digest") or "")
                        if claimed_at < cutoff:
                            continue
                        if (
                            not SAFE_CANONICAL_ID.fullmatch(principal)
                            or not re.fullmatch(r"[0-9a-f]{64}", nonce_digest)
                        ):
                            raise ValueError("bridge audit contains an invalid live nonce claim")
                        signed_timestamp_ms = int(
                            row.get("auth_timestamp_ms")
                            or claimed_at + int(ttl_seconds * 1000)
                        )
                        claims[(principal, nonce_digest)] = max(
                            claims.get((principal, nonce_digest), 0),
                            signed_timestamp_ms + int(ttl_seconds * 1000),
                        )
        except OSError as exc:
            raise OSError("bridge audit is unavailable for durable replay recovery") from exc
        return claims


class DurableNonceLedger:
    """SQLite one-shot nonce authority independent from rotating audit files."""

    def __init__(self, path: Path, *, now_ms: int) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise OSError("bridge replay ledger path must not be a symlink")
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mateengine_nonce_claim (
                    principal_digest TEXT NOT NULL,
                    nonce_digest TEXT NOT NULL,
                    expires_at_ms INTEGER NOT NULL,
                    PRIMARY KEY (principal_digest, nonce_digest)
                )
                """
            )
            connection.execute(
                "DELETE FROM mateengine_nonce_claim WHERE expires_at_ms < ?",
                (int(now_ms),),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @staticmethod
    def _principal_digest(principal: str) -> str:
        return hashlib.sha256(str(principal).encode("utf-8")).hexdigest()

    def is_claimed(self, *, principal: str, nonce_digest: str, now_ms: int) -> bool:
        principal_digest = self._principal_digest(principal)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT expires_at_ms FROM mateengine_nonce_claim
                WHERE principal_digest=? AND nonce_digest=?
                """,
                (principal_digest, nonce_digest),
            ).fetchone()
            if row is None:
                return False
            if int(row[0]) < int(now_ms):
                connection.execute(
                    "DELETE FROM mateengine_nonce_claim WHERE principal_digest=? AND nonce_digest=?",
                    (principal_digest, nonce_digest),
                )
                return False
            return True

    def claim(
        self,
        *,
        principal: str,
        nonce_digest: str,
        expires_at_ms: int,
        now_ms: int,
    ) -> bool:
        principal_digest = self._principal_digest(principal)
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "DELETE FROM mateengine_nonce_claim WHERE expires_at_ms < ?",
                    (int(now_ms),),
                )
                connection.execute(
                    """
                    INSERT INTO mateengine_nonce_claim(
                        principal_digest, nonce_digest, expires_at_ms
                    ) VALUES (?, ?, ?)
                    """,
                    (principal_digest, nonce_digest, int(expires_at_ms)),
                )
                connection.commit()
                return True
        except sqlite3.IntegrityError:
            return False


class BridgeSecurityRuntime:
    def __init__(self, config: BridgeSecurityConfig) -> None:
        errors = validate_security_config(config)
        if errors:
            raise ValueError("; ".join(errors))
        self.config = config
        self.audit_log = (
            DurableJsonlAudit(
                config.audit_path,
                max_bytes=config.audit_max_bytes,
                archive_count=config.audit_archive_count,
            )
            if config.exposure_mode != EXPOSURE_MODE_DISABLED
            and config.audit_path is not None
            else None
        )
        self.nonce_ledger = (
            DurableNonceLedger(
                config.replay_ledger_path,
                now_ms=int(time.time() * 1000),
            )
            if config.exposure_mode != EXPOSURE_MODE_DISABLED
            and config.replay_ledger_path is not None
            else None
        )
        self._lock = threading.Lock()
        self._nonces: dict[tuple[str, str], int] = {}
        self._rate_bucket: tuple[float, float] = (
            float(config.rate_burst),
            time.monotonic(),
        )
        self._preauth_buckets: dict[tuple[str, str], tuple[float, float]] = {}
        self._preauth_last_audit: dict[tuple[str, str], float] = {}
        self._preauth_global: tuple[float, float] = (
            float(config.preauth_rate_burst),
            time.monotonic(),
        )

    def admit_source(
        self,
        *,
        remote_host: str,
        transport: str,
        now_monotonic: float | None = None,
    ) -> bool:
        now = time.monotonic() if now_monotonic is None else float(now_monotonic)
        key = (str(transport).lower(), str(remote_host))
        should_audit = False
        with self._lock:
            global_tokens, global_updated = self._preauth_global
            global_tokens = min(
                float(self.config.preauth_rate_burst),
                global_tokens
                + max(0.0, now - global_updated)
                * float(self.config.preauth_rate_per_second),
            )
            if global_tokens < 1.0:
                key = ("global", "global")
                self._preauth_global = (global_tokens, now)
                last_audit = self._preauth_last_audit.get(key, -1_000_000.0)
                if now - last_audit >= 1.0:
                    self._preauth_last_audit[key] = now
                    should_audit = True
            else:
                self._preauth_global = (global_tokens - 1.0, now)
            if key == ("global", "global"):
                pass
            else:
                if key not in self._preauth_buckets and len(self._preauth_buckets) >= 4096:
                    idle_seconds = max(
                        60.0,
                        4.0
                        * float(self.config.preauth_rate_burst)
                        / float(self.config.preauth_rate_per_second),
                    )
                    for existing, (_tokens, updated) in tuple(self._preauth_buckets.items()):
                        if now - updated >= idle_seconds:
                            self._preauth_buckets.pop(existing, None)
                            self._preauth_last_audit.pop(existing, None)
                if key not in self._preauth_buckets and len(self._preauth_buckets) >= 4096:
                    oldest = min(
                        self._preauth_buckets,
                        key=lambda item: self._preauth_buckets[item][1],
                    )
                    self._preauth_buckets.pop(oldest, None)
                    self._preauth_last_audit.pop(oldest, None)
                tokens, updated_at = self._preauth_buckets.get(
                    key,
                    (float(self.config.preauth_rate_burst), now),
                )
                elapsed = max(0.0, now - updated_at)
                tokens = min(
                    float(self.config.preauth_rate_burst),
                    tokens + elapsed * float(self.config.preauth_rate_per_second),
                )
                if tokens >= 1.0:
                    self._preauth_buckets[key] = (tokens - 1.0, now)
                    return True
                self._preauth_buckets[key] = (tokens, now)
                last_audit = self._preauth_last_audit.get(key, -1_000_000.0)
                if now - last_audit >= 1.0:
                    self._preauth_last_audit[key] = now
                    should_audit = True
        if should_audit:
            self.audit(
                {
                    "event": "preauth_rate_limited",
                    "ts_ms": int(time.time() * 1000),
                    "transport": key[0],
                    "remote_host": key[1],
                }
            )
        return False

    def _consume_rate(self, now: float) -> bool:
        """Consume the configured ingress credential's global budget.

        Principal and scope are signed claims under one key; neither may
        multiply that credential's advertised rate allowance.
        """
        with self._lock:
            tokens, updated_at = self._rate_bucket
            elapsed = max(0.0, now - updated_at)
            tokens = min(
                float(self.config.rate_burst),
                tokens + elapsed * float(self.config.rate_per_second),
            )
            if tokens < 1.0:
                self._rate_bucket = (tokens, now)
                return False
            self._rate_bucket = (tokens - 1.0, now)
            return True

    def _claim_nonce(
        self,
        principal: str,
        nonce: str,
        now_ms: int,
        expires_at_ms: int,
    ) -> bool:
        nonce_digest = _nonce_digest(nonce)
        key = (principal, nonce_digest)
        if self.nonce_ledger is None or not self.nonce_ledger.claim(
            principal=principal,
            nonce_digest=nonce_digest,
            expires_at_ms=expires_at_ms,
            now_ms=now_ms,
        ):
            return False
        with self._lock:
            for existing, expiry in tuple(self._nonces.items()):
                if expiry < now_ms:
                    self._nonces.pop(existing, None)
            if key in self._nonces:
                return False
            self._nonces[key] = int(expires_at_ms)
            return True

    def _nonce_is_claimed(self, principal: str, nonce: str, now_ms: int) -> bool:
        nonce_digest = _nonce_digest(nonce)
        key = (principal, nonce_digest)
        with self._lock:
            expiry = self._nonces.get(key)
            if expiry is None:
                pass
            elif expiry < now_ms:
                self._nonces.pop(key, None)
            else:
                return True
        return bool(
            self.nonce_ledger is not None
            and self.nonce_ledger.is_claimed(
                principal=principal,
                nonce_digest=nonce_digest,
                now_ms=now_ms,
            )
        )

    def authorize(
        self,
        *,
        transport: str,
        method: str,
        path: str,
        remote_host: str,
        raw_body: bytes,
        metadata: Mapping[str, Any],
        required_scopes: Sequence[str],
        now_ms: int | None = None,
        now_monotonic: float | None = None,
    ) -> AuthorizationDecision:
        digest = body_sha256(raw_body)
        if self.config.exposure_mode == EXPOSURE_MODE_DISABLED:
            return AuthorizationDecision(False, 503, "bridge_disabled", body_digest=digest)
        if (
            self.config.exposure_mode == EXPOSURE_MODE_LOOPBACK
            and not _is_loopback_host(remote_host)
        ):
            return AuthorizationDecision(False, 403, "non_loopback_client", body_digest=digest)
        if len(raw_body) > self.config.max_body_bytes:
            return AuthorizationDecision(False, 413, "body_too_large", body_digest=digest)

        version = str(metadata.get("version") or "").strip()
        key_id = str(metadata.get("key_id") or "").strip()
        timestamp_text = str(metadata.get("timestamp_ms") or "").strip()
        nonce = str(metadata.get("nonce") or "").strip()
        principal = str(metadata.get("principal") or "").strip()
        scope_text = str(metadata.get("scope") or "").strip()
        signature = str(metadata.get("signature") or "").strip().lower()
        scopes = _split_scopes(scope_text)
        canonical_scope = ",".join(scopes)
        if version != AUTH_VERSION:
            return AuthorizationDecision(False, 401, "invalid_version", principal, scopes, nonce, digest)
        if key_id != self.config.key_id:
            return AuthorizationDecision(False, 401, "invalid_key_id", principal, scopes, nonce, digest)
        if not timestamp_text or not timestamp_text.lstrip("-").isdigit():
            return AuthorizationDecision(False, 401, "invalid_timestamp", principal, scopes, nonce, digest)
        timestamp_ms = int(timestamp_text)
        current_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        ttl_ms = int(self.config.nonce_ttl_seconds * 1000)
        if abs(current_ms - timestamp_ms) > ttl_ms:
            return AuthorizationDecision(False, 401, "expired_timestamp", principal, scopes, nonce, digest)
        if not nonce or len(nonce) > 256 or any(character.isspace() for character in nonce):
            return AuthorizationDecision(False, 401, "invalid_nonce", principal, scopes, nonce, digest)
        if not SAFE_CANONICAL_ID.fullmatch(principal):
            return AuthorizationDecision(False, 401, "invalid_principal", principal, scopes, nonce, digest)
        if principal not in self.config.allowed_principals:
            return AuthorizationDecision(False, 403, "principal_not_allowed", principal, scopes, nonce, digest)
        if not scopes or canonical_scope != scope_text:
            return AuthorizationDecision(False, 403, "invalid_scope_encoding", principal, scopes, nonce, digest)
        if not set(scopes).issubset(self.config.allowed_scopes):
            return AuthorizationDecision(False, 403, "scope_not_allowed", principal, scopes, nonce, digest)
        required = set(required_scopes)
        if SCOPE_ADMIN not in scopes and not required.issubset(scopes):
            return AuthorizationDecision(False, 403, "required_scope_missing", principal, scopes, nonce, digest)
        expected = build_hmac_signature(
            self.config.hmac_secret,
            version=version,
            key_id=key_id,
            transport=transport,
            method=method,
            path=path,
            timestamp_ms=timestamp_text,
            nonce=nonce,
            principal=principal,
            scope=canonical_scope,
            body_digest=digest,
        ).lower()
        if not signature or not hmac.compare_digest(signature, expected):
            return AuthorizationDecision(False, 401, "invalid_signature", principal, scopes, nonce, digest)
        try:
            if self._nonce_is_claimed(principal, nonce, current_ms):
                return AuthorizationDecision(False, 409, "replay_detected", principal, scopes, nonce, digest)
        except (OSError, sqlite3.Error):
            return AuthorizationDecision(False, 503, "replay_ledger_unavailable", principal, scopes, nonce, digest)
        current_monotonic = time.monotonic() if now_monotonic is None else float(now_monotonic)
        if not self._consume_rate(current_monotonic):
            return AuthorizationDecision(False, 429, "rate_limited", principal, scopes, nonce, digest)
        try:
            claimed = self._claim_nonce(
                principal,
                nonce,
                current_ms,
                timestamp_ms + ttl_ms,
            )
        except (OSError, sqlite3.Error):
            return AuthorizationDecision(False, 503, "replay_ledger_unavailable", principal, scopes, nonce, digest)
        if not claimed:
            return AuthorizationDecision(False, 409, "replay_detected", principal, scopes, nonce, digest)
        return AuthorizationDecision(
            True,
            200,
            "authorized",
            principal,
            scopes,
            nonce,
            digest,
            timestamp_ms,
        )

    def authorize_health(
        self,
        *,
        remote_host: str,
        metadata: Mapping[str, Any],
        now_ms: int | None = None,
        now_monotonic: float | None = None,
    ) -> AuthorizationDecision:
        if self.config.exposure_mode == EXPOSURE_MODE_DISABLED:
            return AuthorizationDecision(False, 503, "bridge_disabled")
        has_auth = any(str(metadata.get(key) or "").strip() for key in HTTP_AUTH_HEADERS)
        if (
            self.config.exposure_mode == EXPOSURE_MODE_LOOPBACK
            and _is_loopback_host(remote_host)
            and not has_auth
        ):
            return AuthorizationDecision(True, 200, "anonymous_loopback_health")
        return self.authorize(
            transport="http",
            method="GET",
            path="/health",
            remote_host=remote_host,
            raw_body=b"",
            metadata=metadata,
            required_scopes=(SCOPE_READ,),
            now_ms=now_ms,
            now_monotonic=now_monotonic,
        )

    def audit(self, row: Mapping[str, Any]) -> bool:
        if self.audit_log is None:
            return False
        try:
            self.audit_log.append(row)
            return True
        except OSError:
            return False


_SECURITY_RUNTIME: BridgeSecurityRuntime | None = None


def _install_security_runtime(runtime: BridgeSecurityRuntime | None) -> None:
    global _SECURITY_RUNTIME
    global HTTP_HOST, HTTP_PORT, UDP_HOST, UDP_PORT
    global MATEENGINE_HOST, MATEENGINE_PORT
    global MATEENGINE_PLUGIN_HOST, MATEENGINE_PLUGIN_PORT
    _SECURITY_RUNTIME = runtime
    if runtime is None:
        return
    config = runtime.config
    HTTP_HOST, HTTP_PORT = config.http_host, config.http_port
    UDP_HOST, UDP_PORT = config.udp_host, config.udp_port
    MATEENGINE_HOST, MATEENGINE_PORT = config.signal_host, config.signal_port
    MATEENGINE_PLUGIN_HOST, MATEENGINE_PLUGIN_PORT = (
        config.plugin_host,
        config.plugin_port,
    )
MATEENGINE_KNOWN_FORM_BLENDSHAPES = [
    "Face:Joy1",
    "Face:Joy2",
    "Face:MouthNeko",
    "Face:FaceStar",
    "Face:FaceHeart",
    "Face:FaceRed",
    "Face:O",
    "Face:EyeWideLeft",
    "Face:EyeWideRight",
    "Face:FaceCry",
    "Face:Sorrow1",
    "Face:Sorrow2",
    "Face:Angry",
    "Face:BrowDownLeft",
    "Face:BrowDownRight",
    "Face:FaceSweat1",
    "Face:Face_XX",
    "Face:LookUp",
    "Face:LookDown",
    "Face:LookRight",
    "Face:FaceDark",
    "Face:MouthSmile",
    "Face:MouthTrouble",
    "Face:MouthStraight",
]

state: dict[str, Any] = {
    "started_at": time.time(),
    "command_count": 0,
    "command_history": [],
    "udp_input_count": 0,
    "udp_count": 0,
    "error_count": 0,
    "last_command": None,
    "last_action_sequence": None,
    "last_motion_sequence": None,
    "last_udp_input": None,
    "last_udp": None,
    "last_error": None,
    "window_motion_count": 0,
    "window_home": None,
    "last_window_motion": None,
    "plugin_count": 0,
    "last_plugin": None,
}
_STATE_LOCK = threading.Lock()


def _command_type(payload: Mapping[str, Any]) -> str:
    return str(payload.get("type") or payload.get("action") or "").strip().lower()


def _required_scopes_for_payload(payload: Mapping[str, Any]) -> tuple[str, ...]:
    command_type = _command_type(payload)
    if command_type in {"action_sequence", "choreography", "sequence"}:
        required: set[str] = set()
        steps = payload.get("steps")
        if isinstance(steps, list):
            for raw_step in steps:
                if not isinstance(raw_step, dict):
                    continue
                command = raw_step.get("command")
                if not isinstance(command, dict):
                    command = {
                        key: value
                        for key, value in raw_step.items()
                        if key not in {"at_ms", "hold_ms", "label"}
                    }
                required.update(_required_scopes_for_payload(command))
        return tuple(sorted(required or {SCOPE_ADMIN}))
    if command_type in {
        "motion_sequence",
        "freeze",
        "stabilize",
        "stabilise",
        "stable",
        "motion_freeze",
        "native_action",
        "body_action",
        "gesture",
        "animation",
        "animate",
        "move_model",
    }:
        return (SCOPE_MOTION,)
    if command_type in {"say", "text"}:
        return (SCOPE_SAY,)
    if command_type in {"accessory", "chibi"}:
        return (SCOPE_TRANSFORM,)
    if command_type in {
        "mouth",
        "talk",
        "reset",
        "expression",
        "expression_transition",
        "form",
        "shape",
        "blendshape",
        "morph",
        "reset_form",
    } or (not command_type and "value" in payload):
        return (SCOPE_EXPRESSION,)
    return (SCOPE_ADMIN,)


def _http_auth_metadata(headers: Mapping[str, Any]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    get_all = getattr(headers, "get_all", None)
    for field, header_name in HTTP_AUTH_HEADERS.items():
        if callable(get_all):
            values = [str(item).strip() for item in (get_all(header_name) or ())]
            metadata[field] = values[0] if len(values) == 1 and values[0] else ""
        else:
            metadata[field] = str(headers.get(header_name) or "").strip()
    return metadata


def _nonce_digest(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _audit_row(
    *,
    event: str,
    transport: str,
    method: str,
    path: str,
    remote_host: str,
    decision: AuthorizationDecision,
    command_type: str = "",
    request_id: str = "",
    success: bool | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "event": event,
        "ts_ms": int(time.time() * 1000),
        "transport": transport,
        "method": method,
        "path": path,
        "remote_host": remote_host,
        "decision_reason": decision.reason,
        "principal": decision.principal or None,
        "scopes": list(decision.scopes),
        "nonce_digest": _nonce_digest(decision.nonce) if decision.nonce else None,
        "body_sha256": decision.body_digest or None,
        "auth_timestamp_ms": decision.signed_timestamp_ms or None,
        "command_type": command_type or None,
        "request_id": request_id or None,
    }
    if success is not None:
        row["success"] = bool(success)
    return row


@dataclass(frozen=True, slots=True)
class CommandProcessResult:
    status: int
    payload: dict[str, Any]
    authorization: AuthorizationDecision


def process_command_request(
    *,
    raw_body: bytes,
    metadata: Mapping[str, Any],
    transport: str,
    method: str,
    path: str,
    remote_host: str,
    runtime: BridgeSecurityRuntime,
    command_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    now_ms: int | None = None,
    now_monotonic: float | None = None,
    _source_admitted: bool = False,
) -> CommandProcessResult:
    digest = body_sha256(raw_body)
    if not _source_admitted and not runtime.admit_source(
        remote_host=remote_host,
        transport=transport,
        now_monotonic=now_monotonic,
    ):
        decision = AuthorizationDecision(False, 429, "preauth_rate_limited", body_digest=digest)
        return CommandProcessResult(429, {"success": False, "detail": decision.reason}, decision)
    if len(raw_body) > runtime.config.max_body_bytes:
        decision = AuthorizationDecision(
            False,
            413,
            "body_too_large",
            body_digest=digest,
        )
        runtime.audit(
            _audit_row(
                event="command_rejected",
                transport=transport,
                method=method,
                path=path,
                remote_host=remote_host,
                decision=decision,
            )
        )
        return CommandProcessResult(413, {"success": False, "detail": decision.reason}, decision)
    try:
        decoded = raw_body.decode("utf-8")
        payload = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
        decision = AuthorizationDecision(False, 400, "invalid_json", body_digest=digest)
        runtime.audit(
            _audit_row(
                event="command_rejected",
                transport=transport,
                method=method,
                path=path,
                remote_host=remote_host,
                decision=decision,
            )
        )
        return CommandProcessResult(400, {"success": False, "detail": decision.reason}, decision)
    if not isinstance(payload, dict):
        decision = AuthorizationDecision(False, 400, "invalid_payload_type", body_digest=digest)
        runtime.audit(
            _audit_row(
                event="command_rejected",
                transport=transport,
                method=method,
                path=path,
                remote_host=remote_host,
                decision=decision,
            )
        )
        return CommandProcessResult(400, {"success": False, "detail": decision.reason}, decision)

    required_scopes = _required_scopes_for_payload(payload)
    decision = runtime.authorize(
        transport=transport,
        method=method,
        path=path,
        remote_host=remote_host,
        raw_body=raw_body,
        metadata=metadata,
        required_scopes=required_scopes,
        now_ms=now_ms,
        now_monotonic=now_monotonic,
    )
    command_type = _command_type(payload)
    request_id = str(payload.get("request_id") or "").strip()
    if not decision.allowed:
        runtime.audit(
            _audit_row(
                event="command_rejected",
                transport=transport,
                method=method,
                path=path,
                remote_host=remote_host,
                decision=decision,
                command_type=command_type,
                request_id=request_id,
            )
        )
        return CommandProcessResult(
            decision.status,
            {"success": False, "detail": decision.reason},
            decision,
        )

    accepted = runtime.audit(
        _audit_row(
            event="command_accepted",
            transport=transport,
            method=method,
            path=path,
            remote_host=remote_host,
            decision=decision,
            command_type=command_type,
            request_id=request_id,
        )
    )
    if not accepted:
        unavailable = AuthorizationDecision(
            False,
            503,
            "audit_unavailable",
            decision.principal,
            decision.scopes,
            decision.nonce,
            decision.body_digest,
        )
        return CommandProcessResult(
            503,
            {"success": False, "detail": unavailable.reason},
            unavailable,
        )

    with _STATE_LOCK:
        state["command_count"] = int(state["command_count"]) + 1
        received_metadata = {
            "command_type": command_type,
            "request_id": request_id or None,
            "body_sha256": decision.body_digest,
            "received_at": time.time(),
        }
        state["last_command"] = received_metadata
        history = state["command_history"]
        history.append(received_metadata)
        del history[:-120]

    handler = command_handler or handle_command
    try:
        result = handler(dict(payload))
    except Exception as exc:
        with _STATE_LOCK:
            state["error_count"] = int(state["error_count"]) + 1
            state["last_error"] = type(exc).__name__
        result = {
            "success": False,
            "detail": "command execution failed",
            "forwarded": False,
        }
    runtime.audit(
        _audit_row(
            event="command_completed",
            transport=transport,
            method=method,
            path=path,
            remote_host=remote_host,
            decision=decision,
            command_type=command_type,
            request_id=request_id,
            success=bool(result.get("success")),
        )
    )
    return CommandProcessResult(
        200 if result.get("success") else 502,
        result,
        decision,
    )


def build_health_payload(runtime: BridgeSecurityRuntime) -> dict[str, Any]:
    """Return a minimal owner/readiness payload without command bodies."""

    return {
        "status": "ok",
        "bridge": "mateengine",
        "owner_id": "host.mateengine.bridge",
        "protocol_version": PROTOCOL_VERSION,
        "exposure_mode": runtime.config.exposure_mode,
        "started_at": state["started_at"],
        "command_count": int(state["command_count"]),
        "error_count": int(state["error_count"]),
        "http": {
            "host": runtime.config.http_host,
            "port": runtime.config.http_port,
        },
        "udp_ingress": {
            "enabled": runtime.config.udp_enabled,
            "host": runtime.config.udp_host if runtime.config.udp_enabled else None,
            "port": runtime.config.udp_port if runtime.config.udp_enabled else None,
        },
    }


class MateEngineBridgeHandler(BaseHTTPRequestHandler):
    server_version = "NatsumeMateEngineBridge/0.1"

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path != "/health":
            self._send_json({"success": False, "detail": "not found"}, status=404)
            return
        runtime = getattr(self.server, "bridge_security_runtime", _SECURITY_RUNTIME)
        if runtime is None:
            self._send_json({"success": False, "detail": "bridge_disabled"}, status=503)
            return
        metadata = _http_auth_metadata(self.headers)
        remote_host = str(self.client_address[0])
        if not runtime.admit_source(remote_host=remote_host, transport="http"):
            self._send_json({"success": False, "detail": "preauth_rate_limited"}, status=429)
            return
        decision = runtime.authorize_health(
            remote_host=remote_host,
            metadata=metadata,
        )
        if not decision.allowed:
            runtime.audit(
                _audit_row(
                    event="health_rejected",
                    transport="http",
                    method="GET",
                    path=path,
                    remote_host=remote_host,
                    decision=decision,
                )
            )
            self._send_json(
                {"success": False, "detail": decision.reason},
                status=decision.status,
            )
            return
        audited = runtime.audit(
            _audit_row(
                event="health_access",
                transport="http",
                method="GET",
                path=path,
                remote_host=remote_host,
                decision=decision,
            )
        )
        if not audited:
            self._send_json(
                {"success": False, "detail": "audit_unavailable"},
                status=503,
            )
            return
        self._send_json(build_health_payload(runtime))

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path != "/command":
            self._send_json({"success": False, "detail": "not found"}, status=404)
            return
        runtime = getattr(self.server, "bridge_security_runtime", _SECURITY_RUNTIME)
        if runtime is None:
            self._send_json({"success": False, "detail": "bridge_disabled"}, status=503)
            return
        remote_host = str(self.client_address[0])
        if not runtime.admit_source(remote_host=remote_host, transport="http"):
            self._send_json({"success": False, "detail": "preauth_rate_limited"}, status=429)
            return
        raw_body, error_status, error = self._read_raw_body(runtime.config.max_body_bytes)
        if error is not None:
            self._send_json({"success": False, "detail": error}, status=error_status)
            return
        processed = process_command_request(
            raw_body=raw_body,
            metadata=_http_auth_metadata(self.headers),
            transport="http",
            method="POST",
            path=path,
            remote_host=remote_host,
            runtime=runtime,
            _source_admitted=True,
        )
        self._send_json(processed.payload, status=processed.status)

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _read_raw_body(self, maximum: int) -> tuple[bytes, int, str | None]:
        get_all = getattr(self.headers, "get_all", None)
        if callable(get_all):
            lengths = [str(item).strip() for item in (get_all("Content-Length") or ())]
            if len(lengths) != 1:
                return b"", 400, "content_length_required_or_ambiguous"
            if get_all("Transfer-Encoding"):
                return b"", 400, "transfer_encoding_not_supported"
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            return b"", 400, "invalid_content_length"
        if length <= 0:
            return b"", 400, "body_required"
        if length > maximum:
            self.close_connection = True
            return b"", 413, "body_too_large"
        body = self.rfile.read(length)
        if len(body) != length:
            self.close_connection = True
            return b"", 400, "incomplete_body"
        return body, 200, None

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP server with a hard request-thread and slow-client bound."""

    daemon_threads = True
    block_on_close = False
    request_queue_size = 32

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        maximum = max(
            1,
            min(256, _env_int("MATEENGINE_BRIDGE_HTTP_MAX_CONCURRENT", 32)),
        )
        self._request_slots = threading.BoundedSemaphore(maximum)
        self._request_timeout_seconds = max(
            1.0,
            min(30.0, _env_float("MATEENGINE_BRIDGE_HTTP_REQUEST_TIMEOUT_SECONDS", 10.0)),
        )
        super().__init__(*args, **kwargs)

    def get_request(self) -> tuple[socket.socket, Any]:
        request, address = super().get_request()
        request.settimeout(self._request_timeout_seconds)
        return request, address

    def process_request(self, request: socket.socket, client_address: Any) -> None:
        if not self._request_slots.acquire(blocking=False):
            with _STATE_LOCK:
                state["error_count"] = int(state["error_count"]) + 1
                state["last_error"] = "http_concurrency_limit_reached"
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


def handle_command(payload: dict[str, Any]) -> dict[str, Any]:
    command_type = str(payload.get("type") or payload.get("action") or "").strip().lower()
    if command_type in {"action_sequence", "choreography", "sequence"}:
        return schedule_action_sequence(payload)
    if command_type == "motion_sequence":
        return schedule_motion_sequence(payload)
    if command_type == "mouth" or (not command_type and "value" in payload):
        return send_mouth(value=payload.get("value"))
    if command_type == "talk":
        talking = bool(payload.get("talking"))
        return send_mouth(value=0.16 if talking else 0.0, detail=("talk_start" if talking else "talk_end"))
    if command_type == "reset":
        return reset_mateengine_forms()
    if command_type in {"freeze", "stabilize", "stabilise", "stable", "motion_freeze"}:
        enabled = payload.get("enabled")
        if enabled is None:
            enabled = payload.get("value", True)
        return send_freeze(enabled=enabled)
    if command_type in {"native_action", "body_action", "gesture", "animation", "animate"}:
        return send_native_action(payload)
    if command_type == "move_model":
        return move_mateengine_window(payload)
    if command_type in {"expression", "expression_transition"}:
        return send_form(payload, detail=command_type)
    if command_type in {"form", "shape", "blendshape", "morph", "reset_form"}:
        return send_form(payload, detail=command_type)
    if command_type in {"accessory", "chibi"}:
        return send_plugin_command(dict(payload), detail=command_type)
    if command_type in {"hotkey"}:
        return {
            "success": True,
            "detail": f"MateEngine bridge accepted no-op command: {command_type}",
            "forwarded": False,
            "command_type": command_type,
        }
    if command_type in {"say", "text"}:
        text = str(payload.get("text") or payload.get("message") or "").strip()
        if not text:
            return {"success": False, "detail": "text command requires text"}
        return send_say(text)
    return {
        "success": True,
        "detail": f"MateEngine bridge ignored unsupported command: {command_type or '(empty)'}",
        "forwarded": False,
        "command_type": command_type,
    }


def send_mouth(*, value: Any, detail: str = "mouth") -> dict[str, Any]:
    mouth_value = _clamp01(value)
    payload = {
        "type": "natsume_mouth",
        "distance": mouth_value,
        "ts": int(time.time() * 1000),
    }
    return send_udp(payload, detail=detail)


def send_say(text: str) -> dict[str, Any]:
    payload = {
        "type": "mob_proximity",
        "phase": "enter",
        "name": "NATSUME_TEXT::" + text,
        "id": "natsume_bridge_say",
        "distance": 1.0,
        "ts": int(time.time() * 1000),
    }
    return send_udp(payload, detail="say")


def send_freeze(*, enabled: Any) -> dict[str, Any]:
    enabled_value = _truthy(enabled)
    payload = {
        "type": "natsume_freeze",
        "phase": "set",
        "name": "NATSUME_FREEZE",
        "id": "natsume_bridge_freeze",
        "distance": 1.0 if enabled_value else 0.0,
        "ts": int(time.time() * 1000),
    }
    return send_udp(payload, detail="freeze")


def send_native_action(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(
        payload.get("native_action")
        or payload.get("gesture")
        or payload.get("preset")
        or payload.get("name")
        or payload.get("target")
        or payload.get("action")
        or ""
    ).strip().lower()
    if not action or action in {"native_action", "body_action", "gesture", "animation", "animate"}:
        action = str(payload.get("state") or payload.get("emotion") or payload.get("emotion_tag") or "").strip().lower()
    if not action:
        return {
            "success": False,
            "detail": "native_action requires action, gesture, preset, name, target, state, or emotion",
            "forwarded": False,
            "command_type": "native_action",
        }
    if not _env_bool("MATEENGINE_ENABLE_NATIVE_ACTION_CONTROL", False):
        return {
            "success": True,
            "detail": "MateEngine native_action control disabled; random/native model motion is left to MateEngine",
            "forwarded": False,
            "command_type": "native_action",
            "action": action,
        }
    raw_value = payload.get("value")
    if raw_value is None:
        raw_value = payload.get("intensity", 1.0)
    action_payload = {
        "type": "natsume_action",
        "phase": "set",
        "name": action,
        "id": f"natsume_action_{action}",
        "distance": _clamp01(raw_value),
        "ts": int(time.time() * 1000),
    }
    return send_udp(action_payload, detail="native_action")


def send_form(payload: dict[str, Any], *, detail: str = "form") -> dict[str, Any]:
    emotion = str(
        payload.get("emotion_tag")
        or payload.get("emotion")
        or payload.get("preset")
        or payload.get("target")
        or "NEUTRAL"
    ).strip().upper() or "NEUTRAL"
    raw_value = payload.get("value")
    if raw_value is None:
        raw_value = 0.0 if emotion in {"", "NEUTRAL", "NONE", "RESET"} else 1.0
    explicit_blendshape = str(payload.get("blendshape") or payload.get("name") or "").strip()
    if explicit_blendshape:
        return send_form_blendshape(
            blendshape=explicit_blendshape,
            value=raw_value,
            detail=detail,
            emotion=emotion,
        )
    if emotion in {"", "NEUTRAL", "NONE", "RESET"}:
        return reset_mateengine_forms()
    preset_shapes = _mateengine_form_blendshapes_for_emotion(emotion)
    active_names = {name for name, _scale in preset_shapes if name}
    clear_results = [
        send_form_blendshape(
            blendshape=name,
            value=0.0,
            detail=f"{detail}:clear:{name}",
            emotion=emotion,
        )
        for name in MATEENGINE_KNOWN_FORM_BLENDSHAPES
        if name and name not in active_names
    ]
    if len(preset_shapes) > 1:
        base_value = _clamp01(raw_value)
        return combine_results(
            detail,
            *clear_results,
            *[
                send_form_blendshape(
                    blendshape=name,
                    value=max(0.0, min(1.0, base_value * scale)),
                    detail=f"{detail}:{name}",
                    emotion=emotion,
                )
                for name, scale in preset_shapes
                if name
            ],
        )
    blendshape = preset_shapes[0][0] if preset_shapes else ""
    return combine_results(
        detail,
        *clear_results,
        send_form_blendshape(
            blendshape=blendshape,
            value=raw_value,
            detail=detail,
            emotion=emotion,
        ),
    )


def send_form_blendshape(*, blendshape: str, value: Any, detail: str, emotion: str) -> dict[str, Any]:
    form_payload = {
        "type": "natsume_form",
        "phase": "set",
        "name": blendshape,
        "id": f"natsume_form_{emotion.lower()}",
        "distance": _clamp01(value),
        "ts": int(time.time() * 1000),
    }
    return send_udp(form_payload, detail=detail)


def reset_mateengine_forms() -> dict[str, Any]:
    results = [send_mouth(value=0.0, detail="reset_mouth")]
    for blendshape in MATEENGINE_KNOWN_FORM_BLENDSHAPES:
        results.append(send_form({
            "type": "form",
            "name": blendshape,
            "value": 0.0,
        }, detail="reset_form"))
    return combine_results("reset", *results)


def _mateengine_form_blendshape_for_emotion(emotion: str) -> str:
    shapes = _mateengine_form_blendshapes_for_emotion(emotion)
    return shapes[0][0] if shapes else ""


def _mateengine_form_blendshapes_for_emotion(emotion: str) -> list[tuple[str, float]]:
    normalized = str(emotion or "").strip().upper()
    mapping = {
        "HAPPY": [("Face:Joy1", 1.0), ("Face:Joy2", 0.55), ("Face:MouthSmile", 0.75), ("Face:FaceRed", 0.22)],
        "HAPPINESS": [("Face:Joy1", 1.0), ("Face:Joy2", 0.55), ("Face:MouthSmile", 0.75), ("Face:FaceRed", 0.22)],
        "JOY": [("Face:Joy1", 1.0), ("Face:Joy2", 0.55), ("Face:MouthSmile", 0.75), ("Face:FaceRed", 0.22)],
        "PLAYFUL": [("Face:MouthNeko", 1.0), ("Face:Joy1", 0.5), ("Face:FaceRed", 0.18)],
        "EXCITED": [("Face:Joy1", 0.95), ("Face:FaceStar", 0.9), ("Face:MouthSmile", 0.7)],
        "EXCITEMENT": [("Face:Joy1", 0.95), ("Face:FaceStar", 0.9), ("Face:MouthSmile", 0.7)],
        "PROUD": [("Face:Joy1", 0.9), ("Face:FaceStar", 0.75), ("Face:MouthSmile", 0.65)],
        "LOVE": [("Face:FaceHeart", 1.0), ("Face:FaceRed", 0.7), ("Face:MouthSmile", 0.65)],
        "LOVING": [("Face:FaceHeart", 1.0), ("Face:FaceRed", 0.7), ("Face:MouthSmile", 0.65)],
        "AFFECTION": [("Face:FaceHeart", 1.0), ("Face:FaceRed", 0.7), ("Face:MouthSmile", 0.65)],
        "COQUETTISH": [("Face:FaceHeart", 1.0), ("Face:FaceRed", 0.7), ("Face:MouthSmile", 0.65)],
        "EMBARRASSED": [("Face:FaceRed", 1.0), ("Face:LookDown", 0.55), ("Face:MouthTrouble", 0.45)],
        "SHY": [("Face:FaceRed", 1.0), ("Face:LookDown", 0.55), ("Face:MouthTrouble", 0.45)],
        "SURPRISED": [("Face:EyeWideLeft", 1.0), ("Face:EyeWideRight", 1.0), ("Face:O", 1.0), ("Face:FaceStar", 0.55)],
        "SURPRISE": [("Face:EyeWideLeft", 1.0), ("Face:EyeWideRight", 1.0), ("Face:O", 1.0), ("Face:FaceStar", 0.55)],
        "SAD": [("Face:Sorrow1", 1.0), ("Face:Sorrow2", 0.55), ("Face:FaceCry", 0.65), ("Face:MouthTrouble", 0.45)],
        "SADNESS": [("Face:Sorrow1", 1.0), ("Face:Sorrow2", 0.55), ("Face:FaceCry", 0.65), ("Face:MouthTrouble", 0.45)],
        "NOSTALGIC": [("Face:Sorrow1", 0.85), ("Face:Sorrow2", 0.45), ("Face:MouthTrouble", 0.32)],
        "ANGRY": [("Face:Angry", 1.0), ("Face:BrowDownLeft", 0.65), ("Face:BrowDownRight", 0.65), ("Face:MouthStraight", 0.5)],
        "FRUSTRATED": [("Face:Angry", 1.0), ("Face:BrowDownLeft", 0.65), ("Face:BrowDownRight", 0.65), ("Face:MouthStraight", 0.5)],
        "ANXIOUS": [("Face:FaceSweat1", 0.9), ("Face:FaceRed", 0.35), ("Face:MouthTrouble", 0.55)],
        "CONFUSED": [("Face:Face_XX", 0.9), ("Face:LookUp", 0.65), ("Face:MouthStraight", 0.45)],
        "THINKING": [("Face:LookUp", 1.0), ("Face:LookRight", 0.55), ("Face:MouthStraight", 0.42)],
        "DARK": [("Face:FaceDark", 1.0), ("Face:MouthStraight", 0.45)],
        "LOW": [("Face:FaceDark", 1.0), ("Face:MouthStraight", 0.45)],
        "NEKO": [("Face:MouthNeko", 1.0), ("Face:Joy1", 0.55)],
        "CAT": [("Face:MouthNeko", 1.0), ("Face:Joy1", 0.55)],
        "NEUTRAL": [("", 0.0)],
        "RESET": [("", 0.0)],
        "NONE": [("", 0.0)],
    }
    if normalized in mapping:
        return mapping[normalized]
    if normalized.startswith("FACE:"):
        return [(normalized, 1.0)]
    return []


def build_signed_udp_envelope(
    *,
    raw_body: bytes,
    runtime: BridgeSecurityRuntime,
    path: str,
    principal: str,
    scope: str,
    timestamp_ms: int | None = None,
    nonce: str | None = None,
    key_id: str | None = None,
    hmac_secret: bytes | None = None,
) -> dict[str, Any]:
    timestamp_value = int(time.time() * 1000) if timestamp_ms is None else int(timestamp_ms)
    nonce_value = str(nonce or secrets.token_urlsafe(24))
    signing_key_id = runtime.config.key_id if key_id is None else str(key_id)
    signing_secret = runtime.config.hmac_secret if hmac_secret is None else bytes(hmac_secret)
    digest = body_sha256(raw_body)
    signature = build_hmac_signature(
        signing_secret,
        version=AUTH_VERSION,
        key_id=signing_key_id,
        transport="udp",
        method="DATAGRAM",
        path=path,
        timestamp_ms=timestamp_value,
        nonce=nonce_value,
        principal=principal,
        scope=scope,
        body_digest=digest,
    )
    return {
        "version": AUTH_VERSION,
        "key_id": signing_key_id,
        "transport": "udp",
        "method": "DATAGRAM",
        "path": path,
        "timestamp_ms": timestamp_value,
        "nonce": nonce_value,
        "principal": principal,
        "scope": scope,
        "payload_json": raw_body.decode("utf-8"),
        "body_sha256": digest,
        "signature": signature,
    }


def send_plugin_command(payload: dict[str, Any], *, detail: str) -> dict[str, Any]:
    runtime = _SECURITY_RUNTIME
    if runtime is None or runtime.config.exposure_mode == EXPOSURE_MODE_DISABLED:
        return {
            "success": False,
            "detail": "MateEngine plugin forward blocked: bridge security runtime unavailable",
            "forwarded": False,
        }
    if not _is_loopback_host(MATEENGINE_PLUGIN_HOST):
        return {
            "success": False,
            "detail": "MateEngine plugin forward blocked: downstream is not loopback",
            "forwarded": False,
        }
    command = dict(payload)
    command["ts"] = int(time.time() * 1000)
    raw_body = json.dumps(command, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    envelope = build_signed_udp_envelope(
        raw_body=raw_body,
        runtime=runtime,
        path="/plugin-command",
        principal=runtime.config.service_principal,
        scope=SCOPE_PLUGIN,
        key_id=runtime.config.plugin_key_id,
        hmac_secret=runtime.config.plugin_hmac_secret,
    )
    data = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(data) > 65_000:
        return {
            "success": False,
            "detail": "MateEngine plugin forward blocked: signed datagram exceeds UDP limit",
            "forwarded": False,
        }
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sent = sock.sendto(data, (MATEENGINE_PLUGIN_HOST, MATEENGINE_PLUGIN_PORT))
    except OSError as exc:
        state["error_count"] = int(state["error_count"]) + 1
        state["last_error"] = str(exc)
        return {
            "success": False,
            "detail": f"MateEngine plugin forward failed: {exc}",
            "forwarded": False,
            "payload": command,
        }
    state["plugin_count"] = int(state["plugin_count"]) + 1
    state["last_plugin"] = {
        "command_type": _command_type(command),
        "body_sha256": envelope["body_sha256"],
        "bytes_sent": sent,
        "sent_at": time.time(),
    }
    state["last_error"] = None
    return {
        "success": True,
        "detail": f"MateEngine plugin {detail} forwarded",
        "forwarded": True,
        "bytes_sent": sent,
        "payload": command,
    }


def combine_results(command_type: str, *results: dict[str, Any]) -> dict[str, Any]:
    rows = [dict(row or {}) for row in results]
    success = all(bool(row.get("success")) for row in rows)
    return {
        "success": success,
        "detail": f"MateEngine {command_type} {'forwarded' if success else 'partially failed'}",
        "forwarded": any(bool(row.get("forwarded")) for row in rows),
        "command_type": command_type,
        "results": rows,
    }


class _Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def move_mateengine_window(payload: dict[str, Any]) -> dict[str, Any]:
    if not WINDOW_MOTION_ENABLED:
        return {
            "success": True,
            "detail": "MateEngine window motion disabled",
            "forwarded": False,
            "command_type": "move_model",
        }
    if os.name != "nt":
        return {
            "success": True,
            "detail": "MateEngine window motion is only available on Windows",
            "forwarded": False,
            "command_type": "move_model",
        }
    hwnd = ctypes.windll.user32.FindWindowW(None, WINDOW_TITLE)
    if not hwnd:
        state["error_count"] = int(state["error_count"]) + 1
        state["last_error"] = f"MateEngine window not found: {WINDOW_TITLE}"
        return {
            "success": False,
            "detail": state["last_error"],
            "forwarded": False,
            "command_type": "move_model",
        }

    rect = _Rect()
    if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        state["error_count"] = int(state["error_count"]) + 1
        state["last_error"] = "GetWindowRect failed"
        return {
            "success": False,
            "detail": state["last_error"],
            "forwarded": False,
            "command_type": "move_model",
        }

    width = max(320, int(rect.right - rect.left))
    height = max(240, int(rect.bottom - rect.top))
    home = _window_home_from_state_or_rect(rect)
    position_x = _clamp_float(payload.get("position_x"), -1.0, 1.0)
    position_y = _clamp_float(payload.get("position_y"), -1.0, 1.0)
    dx = int(round(_clamp_float(position_x * WINDOW_MOTION_SCALE_X, -WINDOW_MOTION_MAX_OFFSET_X, WINDOW_MOTION_MAX_OFFSET_X)))
    dy = int(round(_clamp_float(-position_y * WINDOW_MOTION_SCALE_Y, -WINDOW_MOTION_MAX_OFFSET_Y, WINDOW_MOTION_MAX_OFFSET_Y)))

    bounds = _virtual_screen_bounds()
    target_x = _clamp_int(int(home["left"]) + dx, bounds["left"], bounds["right"] - width)
    target_y = _clamp_int(int(home["top"]) + dy, bounds["top"], bounds["bottom"] - height)

    ctypes.windll.user32.ShowWindow(hwnd, 9)
    moved = bool(ctypes.windll.user32.SetWindowPos(hwnd, 0, target_x, target_y, width, height, 0x0014))
    motion = {
        "payload": {
            "position_x": round(position_x, 5),
            "position_y": round(position_y, 5),
            "rotation": _safe_float(payload.get("rotation"), 0.0),
            "relative": bool(payload.get("relative", True)),
        },
        "home": dict(home),
        "offset": {"x": dx, "y": dy},
        "target": {"left": target_x, "top": target_y, "width": width, "height": height},
        "bounds": bounds,
        "moved_at": time.time(),
    }
    state["window_motion_count"] = int(state["window_motion_count"]) + 1
    state["last_window_motion"] = motion
    state["last_error"] = None if moved else "SetWindowPos failed"
    return {
        "success": moved,
        "detail": "MateEngine window moved" if moved else "SetWindowPos failed",
        "forwarded": moved,
        "command_type": "move_model",
        "motion": motion,
    }


_SEQUENCE_LOCK = threading.Lock()
_SEQUENCE_ACTIVE = 0


def _sequence_limit() -> int:
    return max(1, min(16, _env_int("MATEENGINE_SEQUENCE_MAX_CONCURRENT", 2)))


def _acquire_sequence_slot() -> bool:
    global _SEQUENCE_ACTIVE
    with _SEQUENCE_LOCK:
        if _SEQUENCE_ACTIVE >= _sequence_limit():
            return False
        _SEQUENCE_ACTIVE += 1
        return True


def _release_sequence_slot() -> None:
    global _SEQUENCE_ACTIVE
    with _SEQUENCE_LOCK:
        _SEQUENCE_ACTIVE = max(0, _SEQUENCE_ACTIVE - 1)


def _validate_sequence_payload(payload: Mapping[str, Any], kind: str) -> str | None:
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return f"{kind} requires non-empty steps"
    if kind == "motion_sequence":
        maximum = max(1, min(64, _env_int("MATEENGINE_MOTION_SEQUENCE_MAX_STEPS", 16)))
        max_hold_ms = max(0, min(10_000, _env_int("MATEENGINE_MOTION_SEQUENCE_MAX_HOLD_MS", 650)))
        if len(raw_steps) > maximum:
            return f"{kind} exceeds step limit"
        for raw_step in raw_steps:
            if not isinstance(raw_step, dict):
                return f"{kind} contains a non-object step"
            try:
                hold_ms = int(float(raw_step.get("hold_ms", 0)))
            except (TypeError, ValueError):
                return f"{kind} contains invalid hold_ms"
            if hold_ms < 0 or hold_ms > max_hold_ms:
                return f"{kind} hold_ms exceeds limit"
        return None
    maximum = max(1, min(64, _env_int("MATEENGINE_ACTION_SEQUENCE_MAX_STEPS", 24)))
    max_timeline_ms = max(
        1,
        min(60_000, _env_int("MATEENGINE_ACTION_SEQUENCE_MAX_TIMELINE_MS", 10_000)),
    )
    if len(raw_steps) > maximum:
        return f"{kind} exceeds step limit"
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            return f"{kind} contains a non-object step"
        try:
            at_ms = int(float(raw_step.get("at_ms", 0)))
        except (TypeError, ValueError):
            return f"{kind} contains invalid at_ms"
        if at_ms < 0 or at_ms > max_timeline_ms:
            return f"{kind} timeline exceeds limit"
        command = raw_step.get("command")
        if command is not None and not isinstance(command, dict):
            return f"{kind} command must be an object"
    return None


def _run_sequence_with_slot(
    runner: Callable[[dict[str, Any]], dict[str, Any]],
    payload: dict[str, Any],
) -> None:
    try:
        runner(payload)
    finally:
        _release_sequence_slot()


def schedule_motion_sequence(payload: dict[str, Any]) -> dict[str, Any]:
    raw_steps = payload.get("steps")
    validation_error = _validate_sequence_payload(payload, "motion_sequence")
    if validation_error is not None:
        return {
            "success": False,
            "detail": validation_error,
            "forwarded": False,
            "command_type": "motion_sequence",
        }
    if not _acquire_sequence_slot():
        return {
            "success": False,
            "detail": "sequence concurrency limit reached",
            "forwarded": False,
            "command_type": "motion_sequence",
        }
    step_count = len(raw_steps)
    thread = threading.Thread(
        target=_run_sequence_with_slot,
        args=(run_motion_sequence, dict(payload)),
        daemon=True,
    )
    try:
        thread.start()
    except Exception:
        _release_sequence_slot()
        raise
    return {
        "success": True,
        "detail": "MateEngine motion sequence scheduled",
        "forwarded": True,
        "async": True,
        "command_type": "motion_sequence",
        "gesture": str(payload.get("gesture") or "").strip() or None,
        "step_count": step_count,
    }


def run_motion_sequence(payload: dict[str, Any]) -> dict[str, Any]:
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return {
            "success": False,
            "detail": "motion_sequence requires non-empty steps",
            "forwarded": False,
            "command_type": "motion_sequence",
        }

    native_action_result = None
    gesture = str(payload.get("gesture") or payload.get("preset") or payload.get("name") or "").strip()
    if gesture:
        native_action_result = send_native_action({
            "type": "native_action",
            "gesture": gesture,
            "value": payload.get("intensity", 1.0),
        })
        if (
            native_action_result.get("success")
            and _env_bool("MATEENGINE_NATIVE_ACTION_SUPPRESS_WINDOW_MOTION_FOR_GESTURES", True)
            and not _truthy(payload.get("force_window_motion"))
            and not _truthy(payload.get("window_motion"))
        ):
            result = {
                "success": True,
                "detail": "MateEngine motion sequence handled by native action; window motion suppressed",
                "forwarded": True,
                "command_type": "motion_sequence",
                "gesture": gesture,
                "native_action": native_action_result,
                "step_count": 0,
                "results": [],
            }
            state["last_motion_sequence"] = {**result, "completed_at": time.time()}
            return result

    results: list[dict[str, Any]] = []
    max_steps = _env_int("MATEENGINE_MOTION_SEQUENCE_MAX_STEPS", 16)
    max_hold_ms = _env_int("MATEENGINE_MOTION_SEQUENCE_MAX_HOLD_MS", 650)
    default_hold_ms = max(
        0,
        min(10_000, _env_int("MATEENGINE_MOTION_SEQUENCE_DEFAULT_HOLD_MS", 110)),
    )
    for index, raw_step in enumerate(raw_steps[:max_steps]):
        if not isinstance(raw_step, dict):
            results.append({
                "success": False,
                "detail": f"invalid motion step at index {index}",
                "forwarded": False,
                "command_type": "move_model",
                "index": index,
            })
            continue
        step = dict(raw_step)
        step.setdefault("relative", True)
        step.setdefault("pose", payload.get("pose") or payload.get("gesture") or "motion")
        result = move_mateengine_window(step)
        result["index"] = index
        results.append(result)
        try:
            hold_ms = int(float(step.get("hold_ms", default_hold_ms)))
        except (TypeError, ValueError):
            hold_ms = default_hold_ms
        time.sleep(max(0, min(max_hold_ms, hold_ms)) / 1000.0)

    result = {
        "success": all(bool(item.get("success")) for item in results),
        "detail": "MateEngine motion sequence completed",
        "forwarded": True,
        "command_type": "motion_sequence",
        "gesture": str(payload.get("gesture") or "").strip() or None,
        "native_action": native_action_result,
        "step_count": len(results),
        "results": results,
    }
    state["last_motion_sequence"] = {**result, "completed_at": time.time()}
    return result


def schedule_action_sequence(payload: dict[str, Any]) -> dict[str, Any]:
    raw_steps = payload.get("steps")
    validation_error = _validate_sequence_payload(payload, "action_sequence")
    if validation_error is not None:
        return {
            "success": False,
            "detail": validation_error,
            "forwarded": False,
            "command_type": "action_sequence",
        }
    if not _acquire_sequence_slot():
        return {
            "success": False,
            "detail": "sequence concurrency limit reached",
            "forwarded": False,
            "command_type": "action_sequence",
        }
    step_count = len(raw_steps)
    thread = threading.Thread(
        target=_run_sequence_with_slot,
        args=(run_action_sequence, dict(payload)),
        daemon=True,
    )
    try:
        thread.start()
    except Exception:
        _release_sequence_slot()
        raise
    return {
        "success": True,
        "detail": "MateEngine action sequence scheduled",
        "forwarded": True,
        "async": True,
        "command_type": "action_sequence",
        "name": str(payload.get("name") or payload.get("gesture") or "").strip() or None,
        "step_count": step_count,
    }


def run_action_sequence(payload: dict[str, Any]) -> dict[str, Any]:
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return {
            "success": False,
            "detail": "action_sequence requires non-empty steps",
            "forwarded": False,
            "command_type": "action_sequence",
        }

    max_steps = _env_int("MATEENGINE_ACTION_SEQUENCE_MAX_STEPS", 24)
    normalized_steps: list[dict[str, Any]] = []
    for index, raw_step in enumerate(raw_steps[:max_steps]):
        if not isinstance(raw_step, dict):
            normalized_steps.append({
                "index": index,
                "at_ms": 0,
                "command": {},
                "invalid": True,
            })
            continue
        command = raw_step.get("command")
        if not isinstance(command, dict):
            command = {
                key: value
                for key, value in raw_step.items()
                if key not in {"at_ms", "hold_ms", "label"}
            }
        try:
            at_ms = int(float(raw_step.get("at_ms", 0)))
        except (TypeError, ValueError):
            at_ms = 0
        normalized_steps.append({
            "index": index,
            "label": str(raw_step.get("label") or "").strip() or None,
            "at_ms": max(0, at_ms),
            "command": dict(command),
            "invalid": not bool(command),
        })

    normalized_steps.sort(key=lambda item: (int(item.get("at_ms") or 0), int(item.get("index") or 0)))
    started_at = time.time()
    results: list[dict[str, Any]] = []
    for item in normalized_steps:
        target_at = started_at + (int(item.get("at_ms") or 0) / 1000.0)
        delay_s = target_at - time.time()
        if delay_s > 0:
            time.sleep(delay_s)
        command = dict(item.get("command") or {})
        if item.get("invalid"):
            result = {
                "success": False,
                "detail": f"invalid action sequence step at index {item.get('index')}",
                "forwarded": False,
                "command_type": "action_sequence_step",
            }
        else:
            result = execute_action_sequence_command(command)
        results.append({
            "index": item.get("index"),
            "label": item.get("label"),
            "at_ms": item.get("at_ms"),
            "command_type": str(command.get("type") or command.get("action") or "").strip().lower(),
            "result": result,
        })

    result = {
        "success": all(bool(item.get("result", {}).get("success")) for item in results),
        "detail": "MateEngine action sequence completed",
        "forwarded": True,
        "command_type": "action_sequence",
        "name": str(payload.get("name") or payload.get("gesture") or "").strip() or None,
        "step_count": len(results),
        "results": results,
        "completed_at": time.time(),
    }
    state["last_action_sequence"] = result
    return result


def execute_action_sequence_command(command: dict[str, Any]) -> dict[str, Any]:
    command_type = str(command.get("type") or command.get("action") or "").strip().lower()
    if command_type in {"action_sequence", "choreography", "sequence"}:
        return {
            "success": False,
            "detail": "nested action_sequence is not supported",
            "forwarded": False,
            "command_type": command_type,
        }
    if command_type == "motion_sequence":
        return run_motion_sequence(dict(command))
    return handle_command(dict(command))


def _window_home_from_state_or_rect(rect: _Rect) -> dict[str, int]:
    existing = state.get("window_home")
    if isinstance(existing, dict) and "left" in existing and "top" in existing:
        return {
            "left": int(existing["left"]),
            "top": int(existing["top"]),
        }
    left = _safe_int(WINDOW_HOME_X, int(rect.left))
    top = _safe_int(WINDOW_HOME_Y, int(rect.top))
    state["window_home"] = {"left": left, "top": top}
    return dict(state["window_home"])


def _virtual_screen_bounds() -> dict[str, int]:
    user32 = ctypes.windll.user32
    left = int(user32.GetSystemMetrics(76))
    top = int(user32.GetSystemMetrics(77))
    width = int(user32.GetSystemMetrics(78))
    height = int(user32.GetSystemMetrics(79))
    if width <= 0 or height <= 0:
        width = int(user32.GetSystemMetrics(0))
        height = int(user32.GetSystemMetrics(1))
        left = 0
        top = 0
    return {
        "left": left,
        "top": top,
        "right": left + width,
        "bottom": top + height,
    }


def send_udp(payload: dict[str, Any], *, detail: str) -> dict[str, Any]:
    runtime = _SECURITY_RUNTIME
    if runtime is None or runtime.config.exposure_mode == EXPOSURE_MODE_DISABLED:
        return {
            "success": False,
            "detail": "MateEngine signal forward blocked: bridge security runtime unavailable",
            "forwarded": False,
            "payload": payload,
        }
    if not _is_loopback_host(MATEENGINE_HOST):
        return {
            "success": False,
            "detail": "MateEngine signal forward blocked: downstream is not loopback",
            "forwarded": False,
            "payload": payload,
        }
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sent = sock.sendto(data, (MATEENGINE_HOST, MATEENGINE_PORT))
    except OSError as exc:
        state["error_count"] = int(state["error_count"]) + 1
        state["last_error"] = str(exc)
        return {
            "success": False,
            "detail": f"UDP forward failed: {exc}",
            "forwarded": False,
            "payload": payload,
        }
    state["udp_count"] = int(state["udp_count"]) + 1
    state["last_udp"] = {
        "command_type": _command_type(payload),
        "body_sha256": body_sha256(data),
        "bytes_sent": sent,
        "sent_at": time.time(),
    }
    state["last_error"] = None
    return {
        "success": True,
        "detail": f"MateEngine {detail} forwarded",
        "forwarded": True,
        "bytes_sent": sent,
        "payload": payload,
    }


def process_udp_datagram(
    data: bytes,
    addr: tuple[str, int],
    *,
    runtime: BridgeSecurityRuntime,
    command_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    now_ms: int | None = None,
    now_monotonic: float | None = None,
) -> CommandProcessResult:
    digest = body_sha256(data)
    if not runtime.config.udp_enabled:
        decision = AuthorizationDecision(False, 503, "udp_ingress_disabled", body_digest=digest)
        return CommandProcessResult(503, {"success": False, "detail": decision.reason}, decision)
    if not runtime.admit_source(
        remote_host=str(addr[0]),
        transport="udp",
        now_monotonic=now_monotonic,
    ):
        decision = AuthorizationDecision(False, 429, "preauth_rate_limited", body_digest=digest)
        return CommandProcessResult(429, {"success": False, "detail": decision.reason}, decision)
    if len(data) > runtime.config.max_body_bytes + 8192:
        decision = AuthorizationDecision(False, 413, "udp_envelope_too_large", body_digest=digest)
        runtime.audit(
            _audit_row(
                event="command_rejected",
                transport="udp",
                method="DATAGRAM",
                path="/command",
                remote_host=str(addr[0]),
                decision=decision,
            )
        )
        return CommandProcessResult(413, {"success": False, "detail": decision.reason}, decision)
    try:
        envelope = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        decision = AuthorizationDecision(False, 400, "invalid_udp_envelope", body_digest=digest)
        runtime.audit(
            _audit_row(
                event="command_rejected",
                transport="udp",
                method="DATAGRAM",
                path="/command",
                remote_host=str(addr[0]),
                decision=decision,
            )
        )
        return CommandProcessResult(400, {"success": False, "detail": decision.reason}, decision)
    if not isinstance(envelope, dict) or not isinstance(envelope.get("payload_json"), str):
        decision = AuthorizationDecision(False, 400, "invalid_udp_envelope", body_digest=digest)
        runtime.audit(
            _audit_row(
                event="command_rejected",
                transport="udp",
                method="DATAGRAM",
                path="/command",
                remote_host=str(addr[0]),
                decision=decision,
            )
        )
        return CommandProcessResult(400, {"success": False, "detail": decision.reason}, decision)
    if (
        str(envelope.get("transport") or "").lower() != "udp"
        or str(envelope.get("method") or "").upper() != "DATAGRAM"
        or str(envelope.get("path") or "") != "/command"
    ):
        decision = AuthorizationDecision(False, 401, "invalid_udp_binding", body_digest=digest)
        runtime.audit(
            _audit_row(
                event="command_rejected",
                transport="udp",
                method="DATAGRAM",
                path="/command",
                remote_host=str(addr[0]),
                decision=decision,
            )
        )
        return CommandProcessResult(401, {"success": False, "detail": decision.reason}, decision)
    raw_body = str(envelope["payload_json"]).encode("utf-8")
    supplied_digest = str(envelope.get("body_sha256") or "").strip().lower()
    if supplied_digest != body_sha256(raw_body):
        decision = AuthorizationDecision(False, 401, "body_digest_mismatch", body_digest=body_sha256(raw_body))
        runtime.audit(
            _audit_row(
                event="command_rejected",
                transport="udp",
                method="DATAGRAM",
                path="/command",
                remote_host=str(addr[0]),
                decision=decision,
            )
        )
        return CommandProcessResult(401, {"success": False, "detail": decision.reason}, decision)
    metadata = {
        key: envelope.get(key)
        for key in (
            "version",
            "key_id",
            "timestamp_ms",
            "nonce",
            "principal",
            "scope",
            "signature",
        )
    }
    return process_command_request(
        raw_body=raw_body,
        metadata=metadata,
        transport="udp",
        method="DATAGRAM",
        path="/command",
        remote_host=str(addr[0]),
        runtime=runtime,
        command_handler=command_handler,
        now_ms=now_ms,
        now_monotonic=now_monotonic,
        _source_admitted=True,
    )


def serve_udp_bridge(runtime: BridgeSecurityRuntime) -> None:
    if not runtime.config.udp_enabled:
        return
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind((runtime.config.udp_host, runtime.config.udp_port))
            print(
                f"[mateengine-bridge] udp://{runtime.config.udp_host}:{runtime.config.udp_port} -> udp://{MATEENGINE_HOST}:{MATEENGINE_PORT}",
                flush=True,
            )
            while True:
                data, addr = sock.recvfrom(min(65_535, runtime.config.max_body_bytes + 8192))
                handle_udp_bridge_datagram(data, addr, runtime=runtime)
    except OSError as exc:
        state["error_count"] = int(state["error_count"]) + 1
        state["last_error"] = f"UDP bridge failed: {exc}"
        print(f"[mateengine-bridge] UDP bridge failed: {exc}", flush=True)


def handle_udp_bridge_datagram(
    data: bytes,
    addr: tuple[str, int],
    *,
    runtime: BridgeSecurityRuntime | None = None,
) -> None:
    active_runtime = runtime or _SECURITY_RUNTIME
    if active_runtime is None:
        return
    processed = process_udp_datagram(data, addr, runtime=active_runtime)
    if processed.authorization.allowed:
        state["udp_input_count"] = int(state["udp_input_count"]) + 1
        state["last_udp_input"] = {
            "body_sha256": processed.authorization.body_digest,
            "principal": processed.authorization.principal,
            "remote": f"{addr[0]}:{addr[1]}",
            "received_at": time.time(),
        }
    if not processed.payload.get("success"):
        state["error_count"] = int(state["error_count"]) + 1
        state["last_error"] = str(
            processed.payload.get("detail") or "udp bridge command failed"
        )


def start_udp_bridge_thread(runtime: BridgeSecurityRuntime) -> None:
    if not runtime.config.udp_enabled:
        return
    thread = threading.Thread(
        target=serve_udp_bridge,
        args=(runtime,),
        name="mateengine-bridge-udp",
        daemon=True,
    )
    thread.start()


def _clamp01(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    if number < 0.0:
        return 0.0
    if number > 1.0:
        return 1.0
    return round(number, 4)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value) > 0.0
    normalized = str(value or "").strip().lower()
    return normalized not in {"0", "false", "no", "off", "disable", "disabled", ""}


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp_float(value: Any, minimum: float, maximum: float) -> float:
    number = _safe_float(value, 0.0)
    if number < minimum:
        return minimum
    if number > maximum:
        return maximum
    return number


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    if maximum < minimum:
        return minimum
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


def build_bridge_security_runtime(
    config: BridgeSecurityConfig,
) -> BridgeSecurityRuntime:
    """Construct the owned security runtime behind a sanitized boundary."""

    try:
        return BridgeSecurityRuntime(config)
    except (OSError, sqlite3.Error, ValueError):
        raise BridgeConstructionError() from None


def main() -> None:
    try:
        config = build_security_config()
        runtime = build_bridge_security_runtime(config)
    except BridgeConstructionError as exc:
        raise SystemExit(
            f"[mateengine-bridge] secure startup rejected: {exc.reason_code}"
        ) from None
    except Exception:
        raise SystemExit(
            f"[mateengine-bridge] secure startup rejected: {BRIDGE_CONSTRUCTION_FAILED}"
        ) from None
    if config.exposure_mode == EXPOSURE_MODE_DISABLED:
        print("[mateengine-bridge] disabled by NATSUME_BRIDGE_EXPOSURE_MODE", flush=True)
        return
    _install_security_runtime(runtime)
    started_audited = runtime.audit(
        {
            "event": "bridge_started",
            "ts_ms": int(time.time() * 1000),
            "owner_id": "host.mateengine.bridge",
            "exposure_mode": config.exposure_mode,
            "approval_ref_digest": (
                hashlib.sha256(config.approval_ref.encode("utf-8")).hexdigest()
                if config.approval_ref
                else None
            ),
            "http_host": config.http_host,
            "http_port": config.http_port,
            "udp_enabled": config.udp_enabled,
        }
    )
    if not started_audited:
        raise SystemExit("[mateengine-bridge] secure startup rejected: audit unavailable")
    start_udp_bridge_thread(runtime)
    server = BoundedThreadingHTTPServer(
        (config.http_host, config.http_port),
        MateEngineBridgeHandler,
    )
    server.bridge_security_runtime = runtime  # type: ignore[attr-defined]
    print(
        f"[mateengine-bridge] http://{config.http_host}:{config.http_port} -> udp://{MATEENGINE_HOST}:{MATEENGINE_PORT}",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
