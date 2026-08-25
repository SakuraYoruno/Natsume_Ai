"""耐久运行时事件时间线与重放原语。

内存 Tribe EventBus 仅承担低延迟通知；本模块才是恢复事实来源。事件在写入
SQLite 前必须通过可重放的版本、单调游标、幂等去重键和 fail-closed 脱敏闸门，
避免临时消息、秘密字段或重复投递成为重放状态的一部分。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import threading
from typing import Any, Callable, Mapping, Sequence
import uuid

from host_brain.core.ids import now_ms
from host_brain.core.runtime_helpers import _clean_text as _text
from host_brain.persistence.runtime_event_repository import (
    RUNTIME_EVENT_DELIVERY_CLAIM_LOST,
    RUNTIME_EVENT_DELIVERY_CONFLICT,
    RUNTIME_EVENT_DELIVERY_MIGRATION_ASSET_KEY,
    RUNTIME_EVENT_DELIVERY_MIGRATION_SHA256,
    RUNTIME_EVENT_DELIVERY_NOT_FOUND,
    RUNTIME_EVENT_DURABLE_DATA_INVALID,
    RuntimeEventRepository,
    RuntimeEventRepositoryError,
    TASK_RUNTIME_PROJECTION,
)


RUNTIME_EVENT_SCHEMA_VERSION = "Natsume.runtime.event.v1"

RUNTIME_EVENT_SCHEMA_NOT_READY = "RUNTIME_EVENT_SCHEMA_NOT_READY"
RUNTIME_EVENT_INVALID = "RUNTIME_EVENT_INVALID"
RUNTIME_EVENT_FORBIDDEN_FIELD = "RUNTIME_EVENT_FORBIDDEN_FIELD"
RUNTIME_EVENT_OUTBOX_PUBLISH_FAILED = "RUNTIME_EVENT_OUTBOX_PUBLISH_FAILED"

RUNTIME_EVENT_TABLES = frozenset(
    {
        "runtime_event_timeline",
        "runtime_event_projection_outbox",
        "runtime_event_projection_inbox",
        "runtime_event_task_projection",
    }
)
RUNTIME_EVENT_SOURCE_KINDS = frozenset(
    {
        "TASK_STORE_OUTBOX",
        "TRIBE_EVENT",
        "GRAPH_EVIDENCE",
        "PENTEST",
        "BIO_OPS",
        "REVIEW",
        "VERIFICATION",
        "TASK",
        "SYSTEM",
    }
)

RUNTIME_EVENT_REDACTION_LEVELS = frozenset({"PUBLIC", "INTERNAL_REDACTED"})

_DURABLE_REFERENCE_PATTERN = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,511}$"
)
_SHA256_REF_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_FORBIDDEN_KEY_PATTERN = re.compile(
    r"("
    r"raw[_-]?thought|chain[_-]?of[_-]?thought|reasoning[_-]?trace|"
    r"private[_-]?body|raw[_-]?body|password|secret|cookie|"
    r"api[_-]?key|access[_-]?token|refresh[_-]?token|resume[_-]?token|"
    r"authorization|bearer"
    r")",
    re.IGNORECASE,
)
_FORBIDDEN_VALUE_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/\-=]+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"review_resume::[A-Za-z0-9._~+/\-=]+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bSet-Cookie\s*:", re.IGNORECASE),
)
_MAX_PAYLOAD_JSON_BYTES = 16_384
_MAX_EVIDENCE_REFS = 64


class RuntimeEventError(RuntimeError):
    """带稳定原因码的 fail-closed 事件错误，供调用方降级而非解析异常文本。"""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or RUNTIME_EVENT_INVALID)
        self.detail = str(detail or "runtime event operation failed")
        super().__init__(f"{self.reason_code}: {self.detail}")

    def to_dict(self) -> dict[str, str]:
        return {"reason_code": self.reason_code, "detail": self.detail}


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads_object(value: Any, *, field_name: str) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError as exc:
        raise RuntimeEventError(RUNTIME_EVENT_INVALID, f"{field_name} is invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise RuntimeEventError(RUNTIME_EVENT_INVALID, f"{field_name} must be a JSON object")
    return parsed


def _loads_list(value: Any, *, field_name: str) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError as exc:
        raise RuntimeEventError(RUNTIME_EVENT_INVALID, f"{field_name} is invalid JSON") from exc
    if not isinstance(parsed, list):
        raise RuntimeEventError(RUNTIME_EVENT_INVALID, f"{field_name} must be a JSON list")
    return parsed


def _require_text(value: Any, *, field_name: str, max_length: int = 512) -> str:
    text = _text(value)
    if not text:
        raise RuntimeEventError(RUNTIME_EVENT_INVALID, f"{field_name} is required")
    if len(text) > max_length:
        raise RuntimeEventError(RUNTIME_EVENT_INVALID, f"{field_name} exceeds {max_length} chars")
    return text


def _require_durable_ref(value: Any, *, field_name: str) -> str:
    # 可重放记录只能指向持久引用；临时文件路径或任意文本不能承担跨进程证据身份。
    text = _require_text(value, field_name=field_name)
    if not _DURABLE_REFERENCE_PATTERN.fullmatch(text):
        raise RuntimeEventError(RUNTIME_EVENT_INVALID, f"{field_name} must be a durable reference")
    return text


def _normalize_source_kind(value: Any) -> str:
    source_kind = _require_text(value, field_name="source_kind", max_length=64).upper()
    if source_kind not in RUNTIME_EVENT_SOURCE_KINDS:
        raise RuntimeEventError(RUNTIME_EVENT_INVALID, f"unsupported source_kind: {source_kind}")
    return source_kind


def _normalize_redaction_level(value: Any) -> str:
    redaction_level = _text(value).upper() or "PUBLIC"
    if redaction_level not in RUNTIME_EVENT_REDACTION_LEVELS:
        raise RuntimeEventError(
            RUNTIME_EVENT_INVALID,
            f"unsupported redaction_level: {redaction_level}",
        )
    return redaction_level


def _normalize_evidence_refs(value: Any) -> tuple[str, ...]:
    # 证据引用数量受限，防止单个事件把不受控的大型关联图写入耐久时间线。
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise RuntimeEventError(RUNTIME_EVENT_INVALID, "evidence_refs must be a list")
    refs = tuple(_require_durable_ref(item, field_name="evidence_refs") for item in value)
    if len(refs) > _MAX_EVIDENCE_REFS:
        raise RuntimeEventError(
            RUNTIME_EVENT_INVALID,
            f"evidence_refs exceed {_MAX_EVIDENCE_REFS}",
        )
    return refs


def _scan_forbidden(value: Any, *, path: str) -> None:
    # 递归扫描键名和值；一旦发现秘密、原始推理或会话令牌就拒绝整个事件而非静默删改。
    if isinstance(value, Mapping):
        for raw_key, raw_value in value.items():
            key = str(raw_key or "").strip()
            if not key:
                raise RuntimeEventError(RUNTIME_EVENT_INVALID, f"{path} contains an empty key")
            if _FORBIDDEN_KEY_PATTERN.search(key):
                raise RuntimeEventError(
                    RUNTIME_EVENT_FORBIDDEN_FIELD,
                    f"runtime event payload contains forbidden field: {path}.{key}",
                )
            _scan_forbidden(raw_value, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _scan_forbidden(item, path=f"{path}[{index}]")
        return
    if isinstance(value, bytes):
        raise RuntimeEventError(
            RUNTIME_EVENT_FORBIDDEN_FIELD,
            f"runtime event payload contains raw bytes: {path}",
        )
    if isinstance(value, str):
        for pattern in _FORBIDDEN_VALUE_PATTERNS:
            if pattern.search(value):
                raise RuntimeEventError(
                    RUNTIME_EVENT_FORBIDDEN_FIELD,
                    f"runtime event payload contains forbidden value pattern: {path}",
                )


def _json_safe(value: Any, *, path: str = "payload") -> Any:
    # 只允许 JSON 原生值，避免 callable、连接或其他进程内对象被伪装成可恢复状态。
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item, path=f"{path}.{key}") for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_safe(item, path=f"{path}[]") for item in value]
    if isinstance(value, list):
        return [_json_safe(item, path=f"{path}[]") for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise RuntimeEventError(
        RUNTIME_EVENT_INVALID,
        f"runtime event payload contains non-JSON value at {path}: {type(value).__name__}",
    )


def sanitize_runtime_event_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """将事件载荷规范为有大小上限的 JSON 安全副本，并拒绝敏感字段。"""

    if not isinstance(payload, Mapping):
        raise RuntimeEventError(RUNTIME_EVENT_INVALID, "runtime event payload must be a mapping")
    safe_payload = _json_safe(dict(payload))
    _scan_forbidden(safe_payload, path="payload")
    payload_json = _dumps(safe_payload)
    if len(payload_json.encode("utf-8")) > _MAX_PAYLOAD_JSON_BYTES:
        # 长度在序列化后检查，确保限制覆盖真实写入 SQLite 的字节数而非 Python 对象大小。
        raise RuntimeEventError(
            RUNTIME_EVENT_INVALID,
            f"runtime event payload exceeds {_MAX_PAYLOAD_JSON_BYTES} bytes",
        )
    return safe_payload


def runtime_event_payload_digest(payload: Mapping[str, Any]) -> str:
    """对已净化的规范 JSON 生成摘要，用于发现存储或重放期间的载荷漂移。"""
    payload_json = _dumps(sanitize_runtime_event_payload(payload))
    return f"sha256:{hashlib.sha256(payload_json.encode('utf-8')).hexdigest()}"


def build_runtime_event_dedupe_key(
    *,
    source_kind: str,
    source_ref: str,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
) -> str:
    """从不可变来源、聚合版本和事件身份构造幂等键；同一事实重投不会生成新事件。"""
    normalized_version = int(aggregate_version)
    if normalized_version < 0:
        raise RuntimeEventError(RUNTIME_EVENT_INVALID, "aggregate_version must be non-negative")
    canonical = _dumps(
        {
            "aggregate_id": _require_text(aggregate_id, field_name="aggregate_id"),
            "aggregate_type": _require_text(aggregate_type, field_name="aggregate_type"),
            "aggregate_version": normalized_version,
            "event_type": _require_text(event_type, field_name="event_type"),
            "source_kind": _normalize_source_kind(source_kind),
            "source_ref": _require_durable_ref(source_ref, field_name="source_ref"),
        }
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _event_id_from_dedupe_key(dedupe_key: str) -> str:
    """由去重摘要派生稳定事件引用，避免发布方自由生成难以关联的重放身份。"""
    digest = _require_text(dedupe_key, field_name="dedupe_key").removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        digest = hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()
    return f"runtimeevent://sha256/{digest}"


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """可持久化/可重放的领域事件；构造必须经 ``create`` 完成所有边界规范化。"""
    event_type: str
    source_kind: str
    source_ref: str
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    payload_ref: str
    payload: Mapping[str, Any]
    evidence_refs: tuple[str, ...] = ()
    dedupe_key: str | None = None
    event_id: str | None = None
    redaction_level: str = "PUBLIC"
    schema_version: str = RUNTIME_EVENT_SCHEMA_VERSION
    ts_ms: int = 0

    @classmethod
    def create(
        cls,
        *,
        event_type: str,
        source_kind: str,
        source_ref: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int = 0,
        payload_ref: str,
        payload: Mapping[str, Any],
        evidence_refs: tuple[str, ...] = (),
        dedupe_key: str | None = None,
        event_id: str | None = None,
        redaction_level: str = "PUBLIC",
        ts_ms: int = 0,
    ) -> "RuntimeEvent":
        # 先规范每个 authority-bearing 字段，再计算派生身份，避免未验证输入参与去重。
        normalized_source_kind = _normalize_source_kind(source_kind)
        normalized_source_ref = _require_durable_ref(source_ref, field_name="source_ref")
        normalized_event_type = _require_text(event_type, field_name="event_type", max_length=128)
        normalized_aggregate_type = _require_text(
            aggregate_type,
            field_name="aggregate_type",
            max_length=128,
        )
        normalized_aggregate_id = _require_text(
            aggregate_id,
            field_name="aggregate_id",
            max_length=512,
        )
        normalized_version = int(aggregate_version)
        if normalized_version < 0:
            raise RuntimeEventError(RUNTIME_EVENT_INVALID, "aggregate_version must be non-negative")
        normalized_payload_ref = _require_durable_ref(payload_ref, field_name="payload_ref")
        normalized_payload = sanitize_runtime_event_payload(payload)
        normalized_refs = _normalize_evidence_refs(evidence_refs)
        normalized_dedupe_key = _text(dedupe_key) or build_runtime_event_dedupe_key(
            source_kind=normalized_source_kind,
            source_ref=normalized_source_ref,
            event_type=normalized_event_type,
            aggregate_type=normalized_aggregate_type,
            aggregate_id=normalized_aggregate_id,
            aggregate_version=normalized_version,
        )
        if not _SHA256_REF_PATTERN.fullmatch(normalized_dedupe_key):
            raise RuntimeEventError(RUNTIME_EVENT_INVALID, "dedupe_key must be sha256:<64 hex>")
        normalized_event_id = _text(event_id) or _event_id_from_dedupe_key(normalized_dedupe_key)
        _require_durable_ref(normalized_event_id, field_name="event_id")
        normalized_ts_ms = int(ts_ms or 0)
        if normalized_ts_ms < 0:
            raise RuntimeEventError(RUNTIME_EVENT_INVALID, "ts_ms must be non-negative")
        return cls(
            event_type=normalized_event_type,
            source_kind=normalized_source_kind,
            source_ref=normalized_source_ref,
            aggregate_type=normalized_aggregate_type,
            aggregate_id=normalized_aggregate_id,
            aggregate_version=normalized_version,
            payload_ref=normalized_payload_ref,
            payload=normalized_payload,
            evidence_refs=normalized_refs,
            dedupe_key=normalized_dedupe_key,
            event_id=normalized_event_id,
            redaction_level=_normalize_redaction_level(redaction_level),
            ts_ms=normalized_ts_ms,
        )

    def validate(self) -> None:
        # 通过重新构造验证不可变实例，确保反序列化或外部适配器不能绕过创建期规则。
        if self.schema_version != RUNTIME_EVENT_SCHEMA_VERSION:
            raise RuntimeEventError(RUNTIME_EVENT_INVALID, "unsupported runtime event schema_version")
        RuntimeEvent.create(
            event_type=self.event_type,
            source_kind=self.source_kind,
            source_ref=self.source_ref,
            aggregate_type=self.aggregate_type,
            aggregate_id=self.aggregate_id,
            aggregate_version=self.aggregate_version,
            payload_ref=self.payload_ref,
            payload=self.payload,
            evidence_refs=tuple(self.evidence_refs),
            dedupe_key=self.dedupe_key,
            event_id=self.event_id,
            redaction_level=self.redaction_level,
            ts_ms=self.ts_ms,
        )


def _runtime_event_record(row: Mapping[str, Any]) -> dict[str, Any]:
    # 从 SQLite 读取也要再次净化和比对摘要：持久化并不自动等于可信重放数据。
    record = dict(row)
    payload = _loads_object(record.pop("payload_json"), field_name="runtime_event_timeline.payload_json")
    evidence_refs = _loads_list(
        record.pop("evidence_refs_json"),
        field_name="runtime_event_timeline.evidence_refs_json",
    )
    record["payload"] = sanitize_runtime_event_payload(payload)
    record["evidence_refs"] = _normalize_evidence_refs(evidence_refs)
    event = RuntimeEvent.create(
        event_type=record["event_type"],
        source_kind=record["source_kind"],
        source_ref=record["source_ref"],
        aggregate_type=record["aggregate_type"],
        aggregate_id=record["aggregate_id"],
        aggregate_version=int(record["aggregate_version"]),
        payload_ref=record["payload_ref"],
        payload=record["payload"],
        evidence_refs=record["evidence_refs"],
        dedupe_key=record["dedupe_key"],
        event_id=record["event_id"],
        redaction_level=record["redaction_level"],
        ts_ms=int(record["ts_ms"]),
    )
    expected_digest = runtime_event_payload_digest(event.payload)
    if record["payload_digest"] != expected_digest:
        raise RuntimeEventError(
            RUNTIME_EVENT_INVALID,
            "runtime event payload_digest does not match payload_json",
        )
    record["evidence_refs"] = list(record["evidence_refs"])
    return record


def _runtime_event_matches(
    record: Mapping[str, Any],
    expected: RuntimeEvent,
    *,
    allow_source_zero_timestamp: bool = False,
) -> bool:
    return (
        str(record.get("event_id")) == expected.event_id
        and str(record.get("schema_version")) == expected.schema_version
        and str(record.get("event_type")) == expected.event_type
        and str(record.get("source_kind")) == expected.source_kind
        and str(record.get("source_ref")) == expected.source_ref
        and str(record.get("aggregate_type")) == expected.aggregate_type
        and str(record.get("aggregate_id")) == expected.aggregate_id
        and int(record.get("aggregate_version", -1)) == expected.aggregate_version
        and str(record.get("payload_ref")) == expected.payload_ref
        and record.get("payload") == dict(expected.payload)
        and tuple(record.get("evidence_refs") or ()) == tuple(expected.evidence_refs)
        and str(record.get("dedupe_key")) == expected.dedupe_key
        and str(record.get("redaction_level")) == expected.redaction_level
        and (
            int(record.get("ts_ms", -1)) == expected.ts_ms
            or (
                allow_source_zero_timestamp
                and expected.ts_ms == 0
                and int(record.get("ts_ms", -1)) >= 0
            )
        )
    )


def validate_runtime_event_record_envelope(
    record: Mapping[str, Any],
    *,
    expected: RuntimeEvent,
) -> dict[str, Any]:
    """Validate the complete durable envelope against a typed expectation.

    Domain replayers must not validate only their inner payload: source,
    redaction, dedupe, evidence and timestamp fields are authority-bearing too.
    The repository facade normally performs the same checks, but replay code
    also accepts structural stores in tests and adapters, so it verifies the
    boundary again before installing state.
    """

    required = {
        "cursor",
        "event_id",
        "schema_version",
        "event_type",
        "source_kind",
        "source_ref",
        "aggregate_type",
        "aggregate_id",
        "aggregate_version",
        "payload_ref",
        "payload",
        "payload_digest",
        "evidence_refs",
        "dedupe_key",
        "redaction_level",
        "ts_ms",
        "created_ts_ms",
        "published_ts_ms",
    }
    item = dict(record)
    missing = sorted(required - set(item))
    if missing:
        raise RuntimeEventError(
            RUNTIME_EVENT_INVALID,
            f"runtime event envelope fields missing: {','.join(missing)}",
        )
    try:
        cursor = int(item["cursor"])
        created = int(item["created_ts_ms"])
        published = int(item["published_ts_ms"])
    except (TypeError, ValueError) as exc:
        raise RuntimeEventError(
            RUNTIME_EVENT_INVALID,
            "runtime event envelope integer is invalid",
        ) from exc
    if cursor < 1 or created < 0 or published < created:
        raise RuntimeEventError(
            RUNTIME_EVENT_INVALID,
            "runtime event envelope chronology is invalid",
        )
    expected.validate()
    if not _runtime_event_matches(item, expected):
        raise RuntimeEventError(
            RUNTIME_EVENT_INVALID,
            "runtime event outer envelope does not match typed expectation",
        )
    if str(item.get("payload_digest") or "") != runtime_event_payload_digest(
        expected.payload
    ):
        raise RuntimeEventError(
            RUNTIME_EVENT_INVALID,
            "runtime event envelope payload digest drift",
        )
    return item


class RuntimeEventStore:
    """SQLite 唯一仓储之上的受校验事件门面。

    门面负责输入规范化和重放验证，仓储负责原子持久化/领取围栏；两者分离使
    内存便利接口不能绕过耐久事件的 authority 边界。
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], int] = now_ms,
        timeout_ms: int = 5_000,
        verify_schema: bool = True,
        fault_injector: Callable[[str], None] | None = None,
        repository: RuntimeEventRepository | None = None,
    ) -> None:
        # 禁止内存 URI 和 file URI：该存储是恢复事实来源，必须具备进程外耐久性。
        raw_path = str(db_path or "").strip()
        if not raw_path or raw_path == ":memory:" or raw_path.lower().startswith("file:"):
            raise RuntimeEventError(
                RUNTIME_EVENT_INVALID,
                "RuntimeEventStore requires a durable sqlite file path",
            )
        self.db_path = Path(raw_path).expanduser().resolve()
        self._clock = clock
        try:
            self._repository = repository or RuntimeEventRepository(
                self.db_path,
                clock=clock,
                timeout_ms=timeout_ms,
                verify_schema=verify_schema,
                fault_injector=fault_injector,
            )
        except RuntimeEventRepositoryError as exc:
            raise RuntimeEventError(exc.reason_code, exc.detail) from exc

    def _now(self) -> int:
        return int(self._clock())

    def assert_schema_ready(self) -> None:
        """在发布或重放前确认迁移后的事件表可用，避免把缺表误报为业务失败。"""
        try:
            self._repository.assert_schema_ready()
        except RuntimeEventRepositoryError as exc:
            raise RuntimeEventError(exc.reason_code, exc.detail) from exc

    def assert_runtime_context_authority_schema(self) -> None:
        """Expose persistence-owned Runtime Context readiness as a typed port."""

        try:
            self._repository.assert_runtime_context_authority_schema()
        except RuntimeEventRepositoryError as exc:
            raise RuntimeEventError(exc.reason_code, exc.detail) from exc

    def append_event(self, event: RuntimeEvent | Mapping[str, Any]) -> dict[str, Any]:
        """重新验证并原子追加事件；重复去重键由仓储返回同一耐久事实。"""
        if isinstance(event, RuntimeEvent):
            item = RuntimeEvent.create(
                event_type=event.event_type,
                source_kind=event.source_kind,
                source_ref=event.source_ref,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                aggregate_version=event.aggregate_version,
                payload_ref=event.payload_ref,
                payload=event.payload,
                evidence_refs=tuple(event.evidence_refs),
                dedupe_key=event.dedupe_key,
                event_id=event.event_id,
                redaction_level=event.redaction_level,
                ts_ms=event.ts_ms,
            )
        else:
            item = RuntimeEvent.create(**dict(event))
        payload = sanitize_runtime_event_payload(item.payload)
        # 持久化前再次净化，即使调用者给的是已创建 RuntimeEvent 也不信任其内部映射。
        payload_json = _dumps(payload)
        payload_digest = f"sha256:{hashlib.sha256(payload_json.encode('utf-8')).hexdigest()}"
        ts_ms = int(item.ts_ms or self._now())
        created_ts_ms = self._now()
        try:
            row = self._repository.append_journal(
                {
                    "event_id": item.event_id,
                    "schema_version": RUNTIME_EVENT_SCHEMA_VERSION,
                    "event_type": item.event_type,
                    "source_kind": item.source_kind,
                    "source_ref": item.source_ref,
                    "aggregate_type": item.aggregate_type,
                    "aggregate_id": item.aggregate_id,
                    "aggregate_version": int(item.aggregate_version),
                    "payload_ref": item.payload_ref,
                    "payload_json": payload_json,
                    "payload_digest": payload_digest,
                    "evidence_refs_json": _dumps(list(item.evidence_refs)),
                    "dedupe_key": item.dedupe_key,
                    "redaction_level": item.redaction_level,
                    "ts_ms": ts_ms,
                    "created_ts_ms": created_ts_ms,
                    "published_ts_ms": created_ts_ms,
                }
            )
        except RuntimeEventRepositoryError as exc:
            raise RuntimeEventError(exc.reason_code, exc.detail) from exc
        return _runtime_event_record(row)

    def list_events(
        self,
        *,
        after_cursor: int = 0,
        limit: int = 100,
        event_type: str | None = None,
        source_kind: str | None = None,
    ) -> list[dict[str, Any]]:
        try:
            rows = self._repository.list_journal(
                after_cursor=after_cursor,
                limit=limit,
                event_type=_text(event_type) or None,
                source_kind=_normalize_source_kind(source_kind) if _text(source_kind) else None,
            )
        except RuntimeEventRepositoryError as exc:
            raise RuntimeEventError(exc.reason_code, exc.detail) from exc
        return [_runtime_event_record(row) for row in rows]

    def get_event_by_id(self, *, event_id: str) -> dict[str, Any] | None:
        """Read one canonical Runtime Event by its exact durable identity."""

        if not isinstance(event_id, str):
            raise RuntimeEventError(
                RUNTIME_EVENT_INVALID,
                "event_id must be a string",
            )
        raw_event_id = event_id
        identity = _require_durable_ref(raw_event_id, field_name="event_id")
        if raw_event_id != identity:
            raise RuntimeEventError(
                RUNTIME_EVENT_INVALID,
                "event_id must use its exact canonical form",
            )
        try:
            row = self._repository.get_journal_by_event_id(event_id=identity)
        except RuntimeEventRepositoryError as exc:
            raise RuntimeEventError(exc.reason_code, exc.detail) from exc
        return _runtime_event_record(row) if row is not None else None

    def get_events_by_ids(
        self,
        *,
        event_ids: Sequence[str],
    ) -> dict[str, dict[str, Any]]:
        """Read a bounded exact-id set without opening one connection per event."""

        try:
            rows = self._repository.get_journals_by_event_ids(event_ids=event_ids)
        except RuntimeEventRepositoryError as exc:
            raise RuntimeEventError(exc.reason_code, exc.detail) from exc
        records = [_runtime_event_record(row) for row in rows]
        return {str(record["event_id"]): record for record in records}

    def get_latest_event_for_aggregate(
        self,
        *,
        aggregate_type: str,
        aggregate_id: str,
    ) -> dict[str, Any] | None:
        """Read the latest durable revision for one exact aggregate."""

        kind = _require_text(
            aggregate_type,
            field_name="aggregate_type",
            max_length=128,
        )
        identity = _require_text(aggregate_id, field_name="aggregate_id")
        try:
            row = self._repository.get_latest_journal_for_aggregate(
                aggregate_type=kind,
                aggregate_id=identity,
            )
        except RuntimeEventRepositoryError as exc:
            raise RuntimeEventError(exc.reason_code, exc.detail) from exc
        return _runtime_event_record(row) if row is not None else None

    def list_latest_event_heads(
        self,
        *,
        aggregate_type: str,
        event_type: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Read one latest durable row per aggregate identity."""

        kind = _require_text(
            aggregate_type,
            field_name="aggregate_type",
            max_length=128,
        )
        event = _require_text(
            event_type,
            field_name="event_type",
            max_length=128,
        )
        try:
            rows = self._repository.list_latest_journal_heads(
                aggregate_type=kind,
                event_type=event,
                limit=limit,
            )
        except RuntimeEventRepositoryError as exc:
            raise RuntimeEventError(exc.reason_code, exc.detail) from exc
        return [_runtime_event_record(row) for row in rows]

    def list_events_for_aggregates(
        self,
        *,
        aggregate_refs: Sequence[tuple[str, str]],
        after_cursor: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Replay events for an explicit aggregate allowlist.

        Operator views use this method instead of scanning or exposing the
        unversioned timeline table.  Exact type/id pairs also avoid unrelated
        runtime activity consuming a task's cursor page.
        """

        normalized = list(
            dict.fromkeys(
                (
                    _require_text(kind, field_name="aggregate_type"),
                    _require_text(identifier, field_name="aggregate_id"),
                )
                for kind, identifier in aggregate_refs
            )
        )
        if not normalized:
            return []
        if len(normalized) > 2_000:
            raise RuntimeEventError(
                RUNTIME_EVENT_INVALID,
                "aggregate replay allowlist exceeds 2000 identities",
            )
        try:
            records = self._repository.list_journal_for_aggregates(
                aggregate_refs=normalized,
                after_cursor=after_cursor,
                limit=limit,
            )
        except RuntimeEventRepositoryError as exc:
            raise RuntimeEventError(exc.reason_code, exc.detail) from exc
        return [_runtime_event_record(row) for row in records]

    # -- Task Runtime projection delivery -----------------------------------

    def claim_projection_delivery(
        self,
        *,
        worker_id: str,
        lease_ms: int = 30_000,
    ) -> dict[str, Any] | None:
        """领取一个投影投递租约；跨进程所有权由仓储的令牌和过期时间裁决。"""
        try:
            return self._repository.claim_projection_delivery(
                worker_id=worker_id,
                lease_ms=lease_ms,
            )
        except RuntimeEventRepositoryError as exc:
            raise RuntimeEventError(exc.reason_code, exc.detail) from exc

    def complete_projection_delivery(
        self,
        *,
        delivery_id: str,
        worker_id: str,
        claim_token: str,
    ) -> dict[str, Any]:
        """仅完成持有同一 claim token 的投递，拒绝陈旧 worker 覆盖后来所有者。"""
        try:
            return self._repository.complete_projection_delivery(
                delivery_id=delivery_id,
                worker_id=worker_id,
                claim_token=claim_token,
            )
        except RuntimeEventRepositoryError as exc:
            raise RuntimeEventError(exc.reason_code, exc.detail) from exc

    def release_projection_delivery(
        self,
        *,
        delivery_id: str,
        worker_id: str,
        claim_token: str,
        detail: str,
        retry_delay_ms: int = 0,
    ) -> dict[str, Any]:
        """释放失败投递并指定下次重试时间；事件本身仍已耐久保存。"""
        try:
            return self._repository.release_projection_delivery(
                delivery_id=delivery_id,
                worker_id=worker_id,
                claim_token=claim_token,
                detail=detail,
                retry_delay_ms=retry_delay_ms,
            )
        except RuntimeEventRepositoryError as exc:
            raise RuntimeEventError(exc.reason_code, exc.detail) from exc

    def replay_projection_delivery(
        self,
        *,
        delivery_id: str,
        operator_ref: str,
    ) -> dict[str, Any]:
        try:
            return self._repository.replay_projection_delivery(
                delivery_id=delivery_id,
                operator_ref=operator_ref,
            )
        except RuntimeEventRepositoryError as exc:
            raise RuntimeEventError(exc.reason_code, exc.detail) from exc

    def rebuild_task_projection(self, *, operator_ref: str) -> dict[str, Any]:
        try:
            return self._repository.rebuild_task_projection(operator_ref=operator_ref)
        except RuntimeEventRepositoryError as exc:
            raise RuntimeEventError(exc.reason_code, exc.detail) from exc

    def get_projection_delivery(self, *, delivery_id: str) -> dict[str, Any]:
        try:
            return self._repository.get_delivery(delivery_id=delivery_id)
        except RuntimeEventRepositoryError as exc:
            raise RuntimeEventError(exc.reason_code, exc.detail) from exc

    def get_task_projection(
        self,
        *,
        aggregate_type: str,
        aggregate_id: str,
    ) -> dict[str, Any] | None:
        try:
            return self._repository.get_task_projection(
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
            )
        except RuntimeEventRepositoryError as exc:
            raise RuntimeEventError(exc.reason_code, exc.detail) from exc

    def list_task_projections(
        self,
        *,
        after_cursor: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        try:
            return self._repository.list_task_projections(
                after_cursor=after_cursor,
                limit=limit,
            )
        except RuntimeEventRepositoryError as exc:
            raise RuntimeEventError(exc.reason_code, exc.detail) from exc

    def projection_health(self) -> dict[str, Any]:
        try:
            return self._repository.projection_health()
        except RuntimeEventRepositoryError as exc:
            raise RuntimeEventError(exc.reason_code, exc.detail) from exc

    def append_task_outbox(self, outbox: Mapping[str, Any]) -> dict[str, Any]:
        """将 TaskStore outbox 的不可变来源投影为幂等时间线事件。

        首先查找已有投影；并发发布竞争时只接受与当前来源完全相同（或受控兼容）的
        耐久记录，防止 outbox 重试掩盖来源漂移。
        """
        row = dict(outbox)
        outbox_id = _require_text(row.get("outbox_id"), field_name="outbox_id")
        aggregate_type = _require_text(row.get("aggregate_type"), field_name="aggregate_type")
        aggregate_id = _require_text(row.get("aggregate_id"), field_name="aggregate_id")
        graph_revision: int | None = None
        runtime_aggregate_id = aggregate_id
        if aggregate_type == "TASK_GRAPH":
            # 图修订参与聚合身份，避免同一任务不同不可变图版本折叠为一个重放头。
            raw_revision = row.get("graph_revision")
            if isinstance(raw_revision, bool):
                raise RuntimeEventError(
                    RUNTIME_EVENT_INVALID,
                    "TaskGraph outbox graph_revision must be a positive integer",
                )
            try:
                graph_revision = int(raw_revision)
            except (TypeError, ValueError) as exc:
                raise RuntimeEventError(
                    RUNTIME_EVENT_INVALID,
                    "TaskGraph outbox graph_revision must be a positive integer",
                ) from exc
            if graph_revision < 1:
                raise RuntimeEventError(
                    RUNTIME_EVENT_INVALID,
                    "TaskGraph outbox graph_revision must be a positive integer",
                )
            runtime_aggregate_id = f"{aggregate_id}@{graph_revision}"
        elif row.get("graph_revision") is not None:
            raise RuntimeEventError(
                RUNTIME_EVENT_INVALID,
                "non-TaskGraph outbox must not declare graph_revision",
            )
        aggregate_version = int(row.get("aggregate_version"))
        event_type = _require_text(row.get("event_type"), field_name="event_type")
        payload_ref = _require_durable_ref(row.get("payload_ref"), field_name="payload_ref")
        evidence_refs = _normalize_evidence_refs(row.get("evidence_refs") or ())
        source_ref = f"taskstore://outbox/{outbox_id}"
        idempotency_key = _require_text(row.get("idempotency_key"), field_name="idempotency_key")
        idempotency_digest = f"sha256:{hashlib.sha256(idempotency_key.encode('utf-8')).hexdigest()}"
        created_value = row.get("created_ts_ms")
        source_ts_ms = (
            self._now() if created_value is None else int(created_value)
        )
        payload: dict[str, Any] = {
            "outbox_id": outbox_id,
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "aggregate_version": aggregate_version,
            "event_type": event_type,
            "payload_ref": payload_ref,
            "evidence_refs": list(evidence_refs),
            "idempotency_digest": idempotency_digest,
            "status": _text(row.get("status")) or "PENDING",
        }
        if graph_revision is not None:
            payload["graph_revision"] = graph_revision
        if _text(row.get("attempt_id")):
            payload["attempt_ref"] = f"taskstore://attempt/{_text(row.get('attempt_id'))}"
        current_event = RuntimeEvent.create(
                event_type=event_type,
                source_kind="TASK_STORE_OUTBOX",
                source_ref=source_ref,
                aggregate_type=aggregate_type,
                aggregate_id=runtime_aggregate_id,
                aggregate_version=aggregate_version,
                payload_ref=payload_ref,
                payload=payload,
                evidence_refs=evidence_refs,
                dedupe_key=build_runtime_event_dedupe_key(
                    source_kind="TASK_STORE_OUTBOX",
                    source_ref=source_ref,
                    event_type=event_type,
                    aggregate_type=aggregate_type,
                    aggregate_id=runtime_aggregate_id,
                    aggregate_version=aggregate_version,
                ),
                ts_ms=source_ts_ms,
            )
        compatible_events = [current_event]
        if graph_revision is not None:
            legacy_payload = dict(payload)
            legacy_payload.pop("graph_revision", None)
            compatible_events.append(
                RuntimeEvent.create(
                    event_type=event_type,
                    source_kind="TASK_STORE_OUTBOX",
                    source_ref=source_ref,
                    aggregate_type=aggregate_type,
                    aggregate_id=aggregate_id,
                    aggregate_version=aggregate_version,
                    payload_ref=payload_ref,
                    payload=legacy_payload,
                    evidence_refs=evidence_refs,
                    dedupe_key=build_runtime_event_dedupe_key(
                        source_kind="TASK_STORE_OUTBOX",
                        source_ref=source_ref,
                        event_type=event_type,
                        aggregate_type=aggregate_type,
                        aggregate_id=aggregate_id,
                        aggregate_version=aggregate_version,
                    ),
                    ts_ms=source_ts_ms,
                )
            )

        def existing_projection() -> dict[str, Any] | None:
            # 唯一 source fence 是并发重试后的权威依据；读取到不同事实必须 fail closed。
            try:
                durable = self._repository.get_task_outbox_journal(
                    outbox_id=outbox_id
                )
            except RuntimeEventRepositoryError as exc:
                raise RuntimeEventError(exc.reason_code, exc.detail) from exc
            if durable is None:
                return None
            record = _runtime_event_record(durable)
            if not any(
                _runtime_event_matches(
                    record,
                    candidate,
                    allow_source_zero_timestamp=(source_ts_ms == 0),
                )
                for candidate in compatible_events
            ):
                raise RuntimeEventError(
                    RUNTIME_EVENT_DURABLE_DATA_INVALID,
                    "TaskStore Outbox Runtime Event replay drifted from its immutable source",
                )
            return record

        existing = existing_projection()
        if existing is not None:
            return existing
        try:
            return self.append_event(current_event)
        except RuntimeEventError:
            # 预读和追加之间可能被其他发布者抢先；重新读取只可接纳完全匹配的同一事件。
            # A second publisher may win after the pre-read.  The 0095 unique
            # source fence makes this lookup authoritative; accept only an
            # exact current or checksum-reconstructable legacy projection.
            raced = existing_projection()
            if raced is not None:
                return raced
            raise

    def append_reference_event(
        self,
        *,
        source_kind: str,
        source_ref: str,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int = 0,
        payload_ref: str,
        attributes: Mapping[str, Any] | None = None,
        evidence_refs: tuple[str, ...] = (),
        ts_ms: int = 0,
    ) -> dict[str, Any]:
        payload = {
            "source_ref": _require_durable_ref(source_ref, field_name="source_ref"),
            "payload_ref": _require_durable_ref(payload_ref, field_name="payload_ref"),
            "attributes": dict(attributes or {}),
        }
        return self.append_event(
            RuntimeEvent.create(
                event_type=event_type,
                source_kind=source_kind,
                source_ref=source_ref,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                aggregate_version=aggregate_version,
                payload_ref=payload_ref,
                payload=payload,
                evidence_refs=evidence_refs,
                ts_ms=ts_ms,
            )
        )

    def append_tribe_event(self, tribe_event: Any) -> dict[str, Any]:
        data = tribe_event.to_dict() if hasattr(tribe_event, "to_dict") else dict(tribe_event)
        event_id = _require_text(data.get("event_id"), field_name="tribe.event_id")
        event_type = _require_text(data.get("event_type"), field_name="tribe.event_type")
        payload = data.get("payload") if isinstance(data.get("payload"), Mapping) else {}
        source_ref = f"tribe://event/{event_id}"
        payload_field_names = sorted(str(key) for key in payload.keys())
        forbidden_fields = [name for name in payload_field_names if _FORBIDDEN_KEY_PATTERN.search(name)]
        if forbidden_fields:
            raise RuntimeEventError(
                RUNTIME_EVENT_FORBIDDEN_FIELD,
                f"tribe event payload declares forbidden fields: {forbidden_fields}",
            )
        public_payload = {
            "tribe_event_id": event_id,
            "schema_version": _text(data.get("schema_version")) or "tribe.event.v1",
            "source_agent": _require_text(data.get("source_agent"), field_name="tribe.source_agent"),
            "target_agent": _text(data.get("target_agent")) or None,
            "payload_field_names": payload_field_names,
        }
        return self.append_event(
            RuntimeEvent.create(
                event_type=event_type,
                source_kind="TRIBE_EVENT",
                source_ref=source_ref,
                aggregate_type="tribe_event",
                aggregate_id=event_id,
                aggregate_version=0,
                payload_ref=source_ref,
                payload=public_payload,
                ts_ms=int(data.get("ts_ms") or self._now()),
            )
        )


@dataclass(slots=True)
class RuntimeOutboxPublisher:
    # 发布器只提供存活性重试；真正的跨进程互斥与陈旧所有者拒绝仍由耐久 claim fence 保证。
    """Publish TaskStore outbox rows into the runtime event timeline.

    The sequence is intentionally claim-append-complete:
    1. Claim one or more ready rows through the durable TaskStore fence.
    2. Write an idempotent runtime event.
    3. Complete the exact claim only after the event exists.

    If step 3 fails after step 2 succeeds, claim expiry permits a later owner
    to reuse the same event dedupe key and complete the still-pending row.
    The in-process lock remains a local batching guard; cross-process exclusion
    and stale-owner rejection belong to the durable claim token and fence.
    """

    task_store: Any
    event_store: RuntimeEventStore
    projection_worker: Any | None = None
    worker_id: str | None = None
    claim_lease_ms: int = 30_000
    retry_delay_ms: int = 1_000
    background_batch_limit: int = 100
    max_poll_interval_ms: int = 1_000
    close_join_timeout_ms: int = 10_000
    clock: Callable[[], int] = now_ms
    _publish_lock: threading.Lock = field(init=False, repr=False)
    _lifecycle_lock: threading.Lock = field(init=False, repr=False)
    _background_stop: threading.Event = field(init=False, repr=False)
    _background_wake: threading.Event = field(init=False, repr=False)
    _background_thread: threading.Thread | None = field(
        init=False,
        default=None,
        repr=False,
    )
    _closed: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        # 所有延迟和批量参数在构造期下界收敛，防止错误配置导致忙等或零租约。
        self._publish_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._background_stop = threading.Event()
        self._background_wake = threading.Event()
        self.worker_id = _text(self.worker_id) or (
            f"runtime-outbox-publisher::{uuid.uuid4().hex}"
        )
        self.claim_lease_ms = max(1, int(self.claim_lease_ms))
        self.retry_delay_ms = max(0, int(self.retry_delay_ms))
        self.background_batch_limit = max(1, int(self.background_batch_limit))
        self.max_poll_interval_ms = max(1, int(self.max_poll_interval_ms))
        self.close_join_timeout_ms = max(1, int(self.close_join_timeout_ms))
        if self.projection_worker is None:
            # Local import keeps eventing dependent on a small store protocol,
            # not on this integration facade.
            from host_brain.eventing.runtime_event_projection import (  # noqa: PLC0415
                RuntimeEventProjectionWorker,
            )

            self.projection_worker = RuntimeEventProjectionWorker(self.event_store)

    @property
    def background_running(self) -> bool:
        thread = self._background_thread
        return bool(thread is not None and thread.is_alive())

    def start(self) -> None:
        # 多次启动是幂等的；关闭后的发布器永远不能被重新激活以触碰已关闭存储。
        """Start the composition-owned retry loop exactly once.

        Directly constructed publishers stay synchronous until their owner
        explicitly starts them.  The loop is a liveness helper only; durable
        claims and their fencing tokens remain the delivery authority.
        """

        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeEventError(
                    RUNTIME_EVENT_OUTBOX_PUBLISH_FAILED,
                    "cannot start a closed runtime outbox publisher",
                )
            thread = self._background_thread
            if thread is not None and thread.is_alive():
                return
            self._background_stop.clear()
            self._background_wake.clear()
            thread = threading.Thread(
                target=self._run_background,
                name=f"runtime-outbox::{str(self.worker_id)[-32:]}",
                daemon=True,
            )
            self._background_thread = thread
            thread.start()

    def wake(self) -> None:
        # wake 仅缩短下一次轮询等待，不绕过 claim→append→complete 的交付协议。
        """Request an early retry without granting any delivery authority."""

        self._background_wake.set()

    def close(self) -> None:
        # 先标记关闭再与前台发布线性化，确保 close 返回后没有线程继续访问底层存储。
        """Stop, wake and join the retry worker before stores are closed."""

        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            self._background_stop.set()
            self._background_wake.set()
            thread = self._background_thread
        if thread is threading.current_thread():
            return
        # Linearize shutdown with any foreground publication that passed the
        # first lifecycle check before ``_closed`` was set.  Publishers also
        # recheck under this lock, so no late caller can touch stores after
        # close returns.
        with self._publish_lock:
            pass
        if thread is None:
            return
        thread.join(timeout=self.close_join_timeout_ms / 1_000)
        if thread.is_alive():
            raise RuntimeEventError(
                RUNTIME_EVENT_OUTBOX_PUBLISH_FAILED,
                "runtime outbox retry worker did not stop before close timeout",
            )

    def _run_background(self) -> None:
        # 单次失败保留 PENDING/租约状态供后续恢复，不能让后台线程因短暂故障退出。
        while not self._background_stop.is_set():
            backlog: Mapping[str, Any] | None = None
            try:
                result = self.publish_pending(limit=self.background_batch_limit)
                candidate = result.get("backlog")
                if isinstance(candidate, Mapping):
                    backlog = candidate
            except Exception:
                # The row remains PENDING or durably claimed.  The next bounded
                # poll retries without allowing a transient failure to kill the
                # only process-local liveness loop.
                backlog = None
            if self._background_stop.is_set():
                break
            if backlog is None:
                try:
                    backlog = self._inspect_delivery_backlog()
                except Exception:
                    backlog = None
            delay_ms = self._background_delay_ms(backlog)
            self._background_wake.wait(delay_ms / 1_000)
            self._background_wake.clear()

    def _background_delay_ms(
        self,
        backlog: Mapping[str, Any] | None,
    ) -> int:
        # 有就绪工作时立即再试；否则依据耐久 backoff 的下一唤醒时间有界等待。
        if backlog is None:
            return self.max_poll_interval_ms
        if int(backlog["delivery_ready_count"]) > 0:
            return 1
        wake_ts_ms = backlog.get("next_wakeup_ts_ms")
        if wake_ts_ms is None:
            return self.max_poll_interval_ms
        until_wake_ms = max(1, int(wake_ts_ms) - int(self.clock()))
        return min(self.max_poll_interval_ms, until_wake_ms)

    def _inspect_delivery_backlog(self) -> dict[str, int | None]:
        raw = dict(self.task_store.inspect_outbox_delivery_backlog())
        try:
            total_pending = int(raw["total_pending"])
            delivery_ready_count = int(raw["delivery_ready_count"])
            raw_wakeup = raw["next_wakeup_ts_ms"]
            next_wakeup_ts_ms = None if raw_wakeup is None else int(raw_wakeup)
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeEventError(
                RUNTIME_EVENT_OUTBOX_PUBLISH_FAILED,
                "task outbox backlog inspection returned an invalid result",
            ) from exc
        if (
            total_pending < 0
            or delivery_ready_count < 0
            or delivery_ready_count > total_pending
            or (total_pending == 0 and next_wakeup_ts_ms is not None)
            or (total_pending > 0 and next_wakeup_ts_ms is None)
            or (next_wakeup_ts_ms is not None and next_wakeup_ts_ms < 0)
        ):
            raise RuntimeEventError(
                RUNTIME_EVENT_OUTBOX_PUBLISH_FAILED,
                "task outbox backlog inspection returned impossible counters",
            )
        return {
            "total_pending": total_pending,
            "delivery_ready_count": delivery_ready_count,
            "next_wakeup_ts_ms": next_wakeup_ts_ms,
        }

    def publish_pending(self, *, limit: int = 100) -> dict[str, Any]:
        """串行化本进程发布批次；跨进程安全不依赖此内存锁。"""
        # Host, Internal and Scheduler coordinators share one process publisher.
        # Serialize its claim/append/complete drain across event loops and
        # worker threads.  Multi-process exclusion lives in the durable store.
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeEventError(
                    RUNTIME_EVENT_OUTBOX_PUBLISH_FAILED,
                    "runtime outbox publisher is closed",
                )
        with self._publish_lock:
            with self._lifecycle_lock:
                if self._closed:
                    raise RuntimeEventError(
                        RUNTIME_EVENT_OUTBOX_PUBLISH_FAILED,
                        "runtime outbox publisher closed before publication",
                    )
            result = self._publish_pending_locked(limit=limit)
        backlog = result.get("backlog")
        if isinstance(backlog, Mapping) and int(
            backlog.get("delivery_ready_count") or 0
        ) > 0:
            self._background_wake.set()
        return result

    def _publish_pending_locked(self, *, limit: int) -> dict[str, Any]:
        """执行 claim→append→complete；追加成功而投影失败不会回滚耐久事件。"""
        batch_limit = max(1, int(limit))
        pending: list[dict[str, Any]] = []
        published: list[dict[str, Any]] = []
        # Claim one row at a time.  Completing the oldest event immediately
        # makes the next version of that same aggregate eligible in this drain
        # and avoids hoarding unrelated claims when a later append fails.
        for _ in range(batch_limit):
            # 一次只领取一行并尽快完成，防止先占满租约后因后续失败阻塞无关聚合。
            claimed = list(
                self.task_store.claim_pending_outbox(
                    worker_id=str(self.worker_id),
                    lease_ms=self.claim_lease_ms,
                    limit=1,
                )
            )
            if not claimed:
                break
            if len(claimed) != 1:
                raise RuntimeEventError(
                    RUNTIME_EVENT_OUTBOX_PUBLISH_FAILED,
                    "task outbox single-claim request returned an invalid batch",
                )
            row = dict(claimed[0])
            pending.append(row)
            try:
                event_record = self.event_store.append_task_outbox(row)
            except Exception as exc:
                # 追加失败必须用同一 token 释放租约并设置 backoff，避免永久占有或重复盲投。
                try:
                    self.task_store.release_outbox_claim(
                        str(row["outbox_id"]),
                        worker_id=str(self.worker_id),
                        claim_token=str(row["claim_token"]),
                        fencing_token=int(row["claim_fencing_token"]),
                        expected_version=int(row["status_version"]),
                        retry_delay_ms=self.retry_delay_ms,
                        last_error=(
                            str(getattr(exc, "reason_code", "") or "").strip()
                            or type(exc).__name__
                        ),
                    )
                except Exception as release_exc:
                    raise RuntimeEventError(
                        RUNTIME_EVENT_OUTBOX_PUBLISH_FAILED,
                        "failed to publish an outbox row and release its durable claim",
                    ) from release_exc
                raise RuntimeEventError(
                    RUNTIME_EVENT_OUTBOX_PUBLISH_FAILED,
                    f"failed to publish outbox {row.get('outbox_id')}",
                ) from exc
            try:
                marked = self.task_store.complete_outbox_claim(
                    str(row["outbox_id"]),
                    worker_id=str(self.worker_id),
                    claim_token=str(row["claim_token"]),
                    fencing_token=int(row["claim_fencing_token"]),
                    expected_version=int(row["status_version"]),
                )
            except Exception as exc:
                # 事件已存在但完成标记失败时不能伪装成功；租约期满后可用同一去重键恢复。
                raise RuntimeEventError(
                    RUNTIME_EVENT_OUTBOX_PUBLISH_FAILED,
                    f"runtime event exists but outbox claim completion failed: "
                    f"{row.get('outbox_id')}",
                ) from exc
            published.append(
                {
                    "outbox_id": str(row["outbox_id"]),
                    "cursor": int(event_record["cursor"]),
                    "event_id": str(event_record["event_id"]),
                    "dedupe_key": str(event_record["dedupe_key"]),
                    "outbox_status": str(marked["status"]),
                    "outbox_status_version": int(marked["status_version"]),
                }
            )
        projection: dict[str, Any]
        try:
            projection = dict(
                self.projection_worker.drain(limit=max(1, int(limit)))
            )
        except Exception as exc:
            # 投影是派生视图：日志追加/claim 完成已权威提交，投影故障仅报告降级并待恢复。
            # Journal publication is authoritative and already committed.  A
            # projection fault remains in the leased durable outbox for crash
            # recovery; it must not turn the command commit into a false fail.
            if isinstance(exc, RuntimeEventError):
                reason_code = exc.reason_code
                detail = exc.detail
            else:
                reason_code = "RUNTIME_EVENT_PROJECTION_FAILED"
                detail = f"{type(exc).__name__}: projection delivery remains durable"
            projection = {
                "status": "DEGRADED",
                "reason_code": reason_code,
                "detail": detail,
                "processed_count": 0,
            }
        backlog: dict[str, int | None] | None
        backlog_error: dict[str, str] | None = None
        try:
            backlog = self._inspect_delivery_backlog()
        except Exception as exc:
            # Event append/completion above may already be authoritative.  A
            # diagnostic backlog read must not turn that commit into a false
            # publication failure.  Background retry and explicit drain both
            # fail closed on the missing snapshot and inspect again later.
            backlog = None
            backlog_error = {
                "reason_code": (
                    str(getattr(exc, "reason_code", "") or "").strip()
                    or "RUNTIME_EVENT_OUTBOX_BACKLOG_UNAVAILABLE"
                )
            }
        return {
            "status": "OK" if backlog_error is None else "DEGRADED",
            "pending_scanned": len(pending),
            "published_count": len(published),
            "published": published,
            "projection": projection,
            "backlog": backlog,
            "backlog_error": backlog_error,
        }


__all__ = [
    "RUNTIME_EVENT_DELIVERY_CLAIM_LOST",
    "RUNTIME_EVENT_DELIVERY_CONFLICT",
    "RUNTIME_EVENT_DELIVERY_MIGRATION_ASSET_KEY",
    "RUNTIME_EVENT_DELIVERY_MIGRATION_SHA256",
    "RUNTIME_EVENT_DELIVERY_NOT_FOUND",
    "RUNTIME_EVENT_DURABLE_DATA_INVALID",
    "RUNTIME_EVENT_FORBIDDEN_FIELD",
    "RUNTIME_EVENT_INVALID",
    "RUNTIME_EVENT_OUTBOX_PUBLISH_FAILED",
    "RUNTIME_EVENT_SCHEMA_NOT_READY",
    "RUNTIME_EVENT_SCHEMA_VERSION",
    "RUNTIME_EVENT_SOURCE_KINDS",
    "RUNTIME_EVENT_TABLES",
    "RuntimeEvent",
    "RuntimeEventError",
    "RuntimeEventStore",
    "RuntimeOutboxPublisher",
    "TASK_RUNTIME_PROJECTION",
    "build_runtime_event_dedupe_key",
    "runtime_event_payload_digest",
    "validate_runtime_event_record_envelope",
    "sanitize_runtime_event_payload",
]
