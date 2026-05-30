"""SQLite-backed ledger with triptych schema, WAL mode, and replay-friendly indexes.

WAL (Write-Ahead Logging) mode is enabled by default for improved concurrent
read/write throughput. When WAL activation fails (e.g. read-only filesystem),
the ledger degrades to DELETE journal mode and logs AUDIT_WAL_FAILED.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any
import sqlite3

from host_brain.audit.ledger_constants import (
    DEFAULT_WAL_CONFIG,
    INTENT_COLUMNS,
    GOVERNOR_COLUMNS,
    SKILL_EXEC_COLUMNS,
    MODEL_CONTROL_COLUMNS,
    CANON_PROPOSAL_COLUMNS,
    CANON_CHANGE_COLUMNS,
    WORLD_CANON_DOC_COLUMNS,
    MEMORY_OP_COLUMNS,
    LIFE_EPISODE_COLUMNS,
    LIFE_EVIDENCE_COLUMNS,
    LIFE_EPISODE_LINK_COLUMNS,
    LIFE_MEMORY_CORRECTION_COLUMNS,
    LIFE_MEMORY_COMPACTION_COLUMNS,
    LIFE_OPEN_LOOP_COLUMNS,
    LIFE_MEMORY_COLLECTION_COLUMNS,
    LIFE_MEMORY_COLLECTION_ITEM_COLUMNS,
    LIFE_MEMORY_ENTITY_COLUMNS,
    LIFE_MEMORY_ENTITY_MENTION_COLUMNS,
    VAULT_MANIFEST_COLUMNS,
    VAULT_GC_COLUMNS,
    AIRLOCK_COLUMNS,
    CHANNEL_IO_COLUMNS,
    AVATAR_ACTION_COLUMNS,
    CLAWD_ACTION_COLUMNS,
    AGENT_LOOP_COLUMNS,
    SERVICE_HEALTH_COLUMNS,
    MIGRATION_COLUMNS,
    CONFIG_SNAPSHOT_COLUMNS,
    VOICE_EVENT_COLUMNS,
    RETRIEVAL_EVENT_COLUMNS,
    VISION_EVENT_COLUMNS,
    BOND_UPDATE_COLUMNS,
    SANDBOX_EXEC_COLUMNS,
    SCHEDULER_EVENT_COLUMNS,
    CAPABILITY_FORGE_PROPOSAL_COLUMNS,
    P8_SUBGRAPH_FAILURE_LOG_COLUMNS,
    P8_GRAPH_RUN_LOG_COLUMNS,
    WalConfig,
)
from host_brain.audit.ledger_fetch_mixin import LedgerFetchMixin
from host_brain.audit.ledger_log_mixin import LedgerLogMixin
from host_brain.audit.ledger_query_mixin import LedgerQueryMixin
from host_brain.audit.ledger_scheduler_mixin import LedgerSchedulerMixin
from host_brain.audit.ledger_wal_mixin import LedgerWalMixin
from host_brain.audit.ledger_schema import apply_schema

from host_brain.core.ids import now_ms

logger = logging.getLogger(__name__)


def _env_flag(name: str, *, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    normalized = str(raw).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _clean_anchor_text(value: Any, *, limit: int = 900) -> str:
    text = str(value or "").strip()
    if len(text) <= max(1, int(limit)):
        return text
    return text[: max(1, int(limit)) - 1].rstrip() + "..."


def _join_detail(*parts: Any, limit: int = 900) -> str:
    cleaned = [_clean_anchor_text(part, limit=220) for part in parts if _clean_anchor_text(part, limit=220)]
    return _clean_anchor_text(" | ".join(cleaned), limit=limit)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _voice_gateway_evidence_refs(payload: dict[str, Any], *, request_id: Any, session_id: Any) -> list[str]:
    meta = _json_object(payload.get("payload"))
    platform = str(meta.get("platform") or meta.get("reply_channel") or "").strip().lower()
    source = str(meta.get("source") or meta.get("input_source") or "").strip().lower()
    conversation_id = str(meta.get("conversation_id") or meta.get("reply_target") or session_id or "").strip()
    msg_id = str(meta.get("msg_id") or meta.get("message_id") or meta.get("message_seq") or "").strip()
    is_gateway = (
        platform in {"qq", "wechat", "gateway", "onebot", "wecom", "feishu"}
        or source == "gateway"
        or conversation_id.startswith(("qq_", "wechat_", "wx_", "wecom_", "feishu_"))
    )
    if not is_gateway:
        return []
    refs: list[str] = []
    if msg_id:
        refs.append(f"gateway://message/{msg_id}")
    elif request_id:
        refs.append(f"gateway://message/{request_id}")
    if conversation_id:
        refs.append(f"gateway://conversation/{conversation_id}")
    return refs


def _voice_gateway_person_id(payload: dict[str, Any], *, session_id: Any) -> str | None:
    meta = _json_object(payload.get("payload"))
    platform = str(meta.get("platform") or meta.get("reply_channel") or "").strip().lower()
    source = str(meta.get("source") or meta.get("input_source") or "").strip().lower()
    conversation_id = str(meta.get("conversation_id") or meta.get("reply_target") or session_id or "").strip()
    is_gateway = (
        platform in {"qq", "wechat", "gateway", "onebot", "wecom", "feishu"}
        or source == "gateway"
        or conversation_id.startswith(("qq_", "wechat_", "wx_", "wecom_", "feishu_"))
    )
    if not is_gateway:
        return None
    try:
        from host_brain.bond.user_identity import resolve_person_id  # noqa: PLC0415

        return resolve_person_id(
            None,
            user_id=str(meta.get("person_id") or meta.get("user_id") or "").strip() or None,
            session_id=str(session_id or "").strip() or None,
            platform=platform or None,
            from_id=str(meta.get("from_id") or "").strip() or None,
            conversation_id=conversation_id or None,
        )
    except Exception:
        return None


class SQLiteLedger(
    LedgerFetchMixin,
    LedgerLogMixin,
    LedgerQueryMixin,
    LedgerSchedulerMixin,
    LedgerWalMixin,
):
    """Audit ledger persisted by sqlite3 with WAL mode and concurrent write protection."""

    def __init__(
        self,
        db_path: str | Path = "host_brain/data/audit.sqlite3",
        *,
        check_same_thread: bool = True,
        wal_config: WalConfig | None = None,
    ) -> None:
        self.db_path = str(db_path)
        self._wal_config = wal_config or DEFAULT_WAL_CONFIG
        self._write_lock = threading.RLock()  # RLock allows re-entrant acquisition within transaction()
        self._wal_status: str = "UNKNOWN"
        self._wal_degradation_reason: str | None = None

        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(self.db_path, check_same_thread=check_same_thread)
        self._conn.row_factory = sqlite3.Row
        # 表结构缓存：避免每次 _table_has_column 调用 PRAGMA table_info
        self._column_cache: dict[str, set[str]] = {}
        self._column_cache_lock = threading.Lock()
        self._table_name_cache: set[str] | None = None
        self._apply_wal_config()
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._ensure_schema()

    # -------------------------------------------------------------------
    # Properties（快捷访问各日志表）
    # -------------------------------------------------------------------

    @property
    def intent_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("intent_log")

    @property
    def governor_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("governor_log")

    @property
    def skill_exec_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("skill_exec_log")

    @property
    def model_control_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("model_control_log")

    @property
    def error_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("error_log")

    @property
    def qa_violation_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("qa_violation_log")

    @property
    def canon_proposal_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("canon_proposal_log")

    @property
    def canon_change_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("canon_change_log")

    @property
    def world_canon_doc(self) -> list[dict[str, Any]]:
        return self._fetch_all("world_canon_doc")

    @property
    def memory_ops_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("memory_ops_log")

    @property
    def life_episode(self) -> list[dict[str, Any]]:
        return self._fetch_all("life_episode")

    @property
    def life_evidence(self) -> list[dict[str, Any]]:
        return self._fetch_all("life_evidence")

    @property
    def life_episode_link(self) -> list[dict[str, Any]]:
        return self._fetch_all("life_episode_link")

    @property
    def life_memory_correction(self) -> list[dict[str, Any]]:
        return self._fetch_all("life_memory_correction")

    @property
    def life_memory_compaction(self) -> list[dict[str, Any]]:
        return self._fetch_all("life_memory_compaction")

    @property
    def life_open_loop(self) -> list[dict[str, Any]]:
        return self._fetch_all("life_open_loop")

    @property
    def life_memory_collection(self) -> list[dict[str, Any]]:
        return self._fetch_all("life_memory_collection")

    @property
    def life_memory_collection_item(self) -> list[dict[str, Any]]:
        return self._fetch_all("life_memory_collection_item")

    @property
    def life_memory_entity(self) -> list[dict[str, Any]]:
        return self._fetch_all("life_memory_entity")

    @property
    def life_memory_entity_mention(self) -> list[dict[str, Any]]:
        return self._fetch_all("life_memory_entity_mention")

    @property
    def vault_manifest_index(self) -> list[dict[str, Any]]:
        return self._fetch_all("vault_manifest_index")

    @property
    def vault_gc_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("vault_gc_log")

    @property
    def airlock_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("airlock_log")

    @property
    def channel_io_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("channel_io_log")

    @property
    def avatar_action_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("avatar_action_log")

    @property
    def clawd_action_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("clawd_action_log")

    @property
    def agent_loop_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("agent_loop_log")

    @property
    def supervisor_decision_log(self) -> list[dict[str, Any]]:
        """P5-4：Supervisor 决策观测日志（rule/llm/llm_fallback_rule、failure_code、selected_agent）。"""
        return self._fetch_all("supervisor_decision_log")

    @property
    def p8_subgraph_failure_log(self) -> list[dict[str, Any]]:
        """P8-1：subgraph/local loop 失败分布聚合日志。"""
        return self._fetch_all("p8_subgraph_failure_log")

    @property
    def p8_graph_run_log(self) -> list[dict[str, Any]]:
        """P8-1：三图 run_graph 执行记录（延迟聚合 + Intent budget breach 聚合）。"""
        return self._fetch_all("p8_graph_run_log")

    @property
    def service_health_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("service_health_log")

    @property
    def migration_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("migration_log")

    @property
    def config_snapshot_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("config_snapshot_log")

    @property
    def voice_event_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("voice_event_log")

    @property
    def retrieval_event_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("retrieval_event_log")

    @property
    def vision_event_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("vision_event_log")

    @property
    def bond_update_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("bond_update_log")

    @property
    def sandbox_exec_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("sandbox_exec_log")

    @property
    def scheduler_event_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("scheduler_event_log")

    @property
    def capability_forge_proposal_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("capability_forge_proposal_log")

    @property
    def llm_pool_call_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("llm_pool_call_log")

    @property
    def llm_token_usage_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("llm_token_usage_log")

    # -------------------------------------------------------------------
    # 写入方法（业务 log_* 方法）
    # -------------------------------------------------------------------

    def log_intent(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in INTENT_COLUMNS}
        # M31-C：向后兼容 lint_result_json 字段
        if "lint_result_json" in row and row["lint_result_json"] is not None:
            payload["lint_result"] = row["lint_result_json"]
        if payload.get("tool_visibility_hash") is None:
            payload["tool_visibility_hash"] = ""
        if payload.get("lint_result") is None:
            payload["lint_result"] = ""
        self._insert("intent_log", payload)

    def log_governor(self, row: dict[str, Any]) -> None:
        available_columns = {key for key in GOVERNOR_COLUMNS if self._table_has_column("governor_log", key)}
        payload = {key: row.get(key) for key in available_columns}
        # FIX-4.2：tribe_params_snapshot 需要 JSON 序列化（dict → str）
        if payload.get("tribe_params_snapshot") is not None and isinstance(payload["tribe_params_snapshot"], dict):
            payload["tribe_params_snapshot"] = json.dumps(
                payload["tribe_params_snapshot"], ensure_ascii=False, default=str
            )
        self._insert("governor_log", payload)

    def log_skill_exec(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in SKILL_EXEC_COLUMNS}
        if not payload.get("event_id"):
            payload["event_id"] = f"evt_{uuid.uuid4().hex}"
        self._insert("skill_exec_log", payload)
        self._log_life_audit_anchor(
            event_source="skill",
            event_id=str(payload.get("event_id") or payload.get("evidence_ref") or ""),
            request_id=payload.get("request_id"),
            intent_id=payload.get("intent_id"),
            task_id=payload.get("skill_name"),
            status=payload.get("result_status"),
            reason_code=payload.get("result_code"),
            detail=_join_detail(
                "skill",
                payload.get("skill_name"),
                payload.get("result_status"),
                payload.get("result_code"),
                payload.get("result_digest"),
            ),
            ts_ms=payload.get("ts_ms"),
            metadata={
                "audit_table": "skill_exec_log",
                "actor_id": "natsume",
                "event_kind": "skill_execution",
                "approval_token_ref": payload.get("approval_token_ref"),
                "confirm_token_ref": payload.get("confirm_token_ref"),
                "undo_token_ref": payload.get("undo_token_ref"),
                "manifest_ref": payload.get("manifest_ref"),
                "evidence_refs": [
                    ref
                    for ref in (
                        payload.get("evidence_ref"),
                        f"skill://exec/{payload.get('event_id')}",
                    )
                    if ref
                ],
            },
        )

    def log_model_control(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in MODEL_CONTROL_COLUMNS}
        self._insert("model_control_log", payload)

    def log_supervisor_decision(self, row: dict[str, Any]) -> None:
        """P5-4：记录一次 Supervisor 决策（rule/llm/llm_fallback_rule、failure_code、selected_agent）。"""
        payload = {
            "request_id": str(row.get("request_id") or ""),
            "ts_ms": int(row.get("ts_ms") or 0),
            "supervisor_mode": str(row.get("supervisor_mode") or "rule").strip(),
            "supervisor_decision_source": str(row.get("supervisor_decision_source") or "rule").strip(),
            "supervisor_failure_code": str(row.get("supervisor_failure_code") or "").strip() or None,
            "supervisor_selected_agent": str(row.get("supervisor_selected_agent") or "").strip(),
        }
        self._insert("supervisor_decision_log", payload)

    def log_error(self, *, request_id: str | None, stage: str, reason_code: str, detail: str) -> None:
        self._insert(
            "error_log",
            {
                "request_id": request_id,
                "stage": stage,
                "reason_code": reason_code,
                "detail": detail,
                "ts_ms": now_ms(),
            },
        )

    def log_qa_violation(self, *, request_id: str | None, code: str, detail: str, field: str | None = None) -> None:
        self._insert(
            "qa_violation_log",
            {
                "request_id": request_id,
                "code": code,
                "detail": detail,
                "field": field,
                "ts_ms": now_ms(),
            },
        )

    def log_canon_proposal(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in CANON_PROPOSAL_COLUMNS}
        # INSERT OR IGNORE：proposal_id UNIQUE 约束下幂等写入，已存在则跳过
        cols = [k for k, v in payload.items() if v is not None or k in ("proposal_id", "canon_kind", "target_id", "status", "ts_ms")]
        placeholders = ", ".join("?" for _ in cols)
        col_names = ", ".join(cols)
        values = [self._encode_value("canon_proposal_log", c, payload[c]) for c in cols]
        with self._write_lock:
            with self._conn:
                self._conn.execute(
                    f"INSERT OR IGNORE INTO canon_proposal_log ({col_names}) VALUES ({placeholders})",
                    values,
                )

    def log_canon_change(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in CANON_CHANGE_COLUMNS}
        self._insert("canon_change_log", payload)

    def log_memory_op(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in MEMORY_OP_COLUMNS}
        self._insert("memory_ops_log", payload)

    def log_life_episode(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in LIFE_EPISODE_COLUMNS}
        self._insert("life_episode", payload)

    def log_life_evidence(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in LIFE_EVIDENCE_COLUMNS}
        self._insert("life_evidence", payload)

    def log_life_episode_link(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in LIFE_EPISODE_LINK_COLUMNS}
        self._insert("life_episode_link", payload)

    def log_life_memory_correction(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in LIFE_MEMORY_CORRECTION_COLUMNS}
        self._insert("life_memory_correction", payload)

    def log_life_memory_compaction(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in LIFE_MEMORY_COMPACTION_COLUMNS}
        self._insert("life_memory_compaction", payload)

    def log_life_open_loop(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in LIFE_OPEN_LOOP_COLUMNS}
        self._insert("life_open_loop", payload)

    def log_life_memory_collection(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in LIFE_MEMORY_COLLECTION_COLUMNS}
        self._insert("life_memory_collection", payload)

    def log_life_memory_collection_item(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in LIFE_MEMORY_COLLECTION_ITEM_COLUMNS}
        self._insert("life_memory_collection_item", payload)

    def log_life_memory_entity(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in LIFE_MEMORY_ENTITY_COLUMNS}
        self._insert("life_memory_entity", payload)

    def log_life_memory_entity_mention(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in LIFE_MEMORY_ENTITY_MENTION_COLUMNS}
        self._insert("life_memory_entity_mention", payload)

    def _log_life_audit_anchor(
        self,
        *,
        event_source: str,
        event_id: str | None = None,
        request_id: str | None = None,
        intent_id: str | None = None,
        person_id: str | None = None,
        session_id: str | None = None,
        task_id: str | None = None,
        status: str | None = None,
        reason_code: str | None = None,
        detail: str | None = None,
        ts_ms: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not _env_flag("HOST_LIFE_AUDIT_ANCHOR_ENABLED", default=True):
            return
        source = str(event_source or "").strip().lower()
        if not source:
            return
        summary = _clean_anchor_text(detail, limit=900)
        meta = dict(metadata or {})
        if not summary and not str(status or "").strip() and not str(reason_code or "").strip():
            return
        try:
            from host_brain.memory.life_memory import LifeMemoryStore  # noqa: PLC0415

            LifeMemoryStore(ledger=self).write_background_episode(
                event_source=source,
                event_id=str(event_id or "").strip() or None,
                request_id=str(request_id or "").strip() or None,
                intent_id=str(intent_id or "").strip() or None,
                person_id=str(person_id or "").strip() or None,
                session_id=str(session_id or "").strip() or None,
                task_id=str(task_id or "").strip() or None,
                status=str(status or "").strip() or None,
                reason_code=str(reason_code or "").strip() or None,
                detail=summary,
                ts_ms=int(ts_ms or now_ms()),
                metadata={
                    **meta,
                    "anchor_only": True,
                    "memory_layer": "life_episode",
                    "audit_anchor_source": source,
                },
            )
        except Exception as exc:
            logger.debug("life audit anchor skipped source=%s event_id=%s: %s", source, event_id, exc)

    def log_vault_manifest(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in VAULT_MANIFEST_COLUMNS}
        self._insert("vault_manifest_index", payload)

    def log_vault_gc(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in VAULT_GC_COLUMNS}
        self._insert("vault_gc_log", payload)

    def log_airlock(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in AIRLOCK_COLUMNS}
        self._insert("airlock_log", payload)

    def log_channel_io(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in CHANNEL_IO_COLUMNS}
        self._insert("channel_io_log", payload)
        channel = str(payload.get("channel") or "channel").strip().lower() or "channel"
        message_id = str(payload.get("message_id") or payload.get("request_id") or "").strip()
        self._log_life_audit_anchor(
            event_source="channel",
            event_id=message_id or None,
            request_id=payload.get("request_id"),
            person_id=payload.get("user_id"),
            session_id=payload.get("thread_key"),
            task_id=f"{channel}_{str(payload.get('direction') or 'io').strip().lower()}",
            status=payload.get("status"),
            reason_code=payload.get("reason_code"),
            detail=_join_detail(
                "channel",
                channel,
                payload.get("direction"),
                payload.get("status"),
                payload.get("stage"),
                payload.get("detail"),
            ),
            ts_ms=payload.get("ts_ms"),
            metadata={
                "audit_table": "channel_io_log",
                "channel": channel,
                "direction": payload.get("direction"),
                "payload_digest": payload.get("payload_digest"),
                "evidence_refs": [
                    ref
                    for ref in (
                        f"channel://message/{message_id}" if message_id else None,
                        f"gateway://message/{message_id}" if message_id and channel in {"qq", "wechat", "gateway", "onebot"} else None,
                    )
                    if ref
                ],
            },
        )

    def log_avatar_action(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in AVATAR_ACTION_COLUMNS}
        self._insert("avatar_action_log", payload)
        self._log_life_audit_anchor(
            event_source="avatar",
            event_id=str(payload.get("request_id") or payload.get("expression_id") or ""),
            request_id=payload.get("request_id"),
            task_id=payload.get("expression_id") or payload.get("trigger") or "avatar_action",
            status=payload.get("status"),
            reason_code=payload.get("reason_code"),
            detail=_join_detail(
                "avatar",
                payload.get("trigger"),
                payload.get("emotion_tag"),
                payload.get("expression_id"),
                payload.get("status"),
                payload.get("detail"),
            ),
            ts_ms=payload.get("ts_ms"),
            metadata={"audit_table": "avatar_action_log", "action_kind": "avatar_action"},
        )

    def log_clawd_action(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in CLAWD_ACTION_COLUMNS}
        self._insert("clawd_action_log", payload)
        self._log_life_audit_anchor(
            event_source="clawd",
            event_id=str(payload.get("manifest_ref") or payload.get("request_id") or ""),
            request_id=payload.get("request_id"),
            intent_id=payload.get("intent_id"),
            task_id=payload.get("action_name") or "clawd_action",
            status=payload.get("status"),
            reason_code=payload.get("reason_code"),
            detail=_join_detail(
                "clawd",
                payload.get("action_name"),
                payload.get("target_desc"),
                payload.get("status"),
                payload.get("reason_code"),
                payload.get("detail"),
            ),
            ts_ms=payload.get("ts_ms"),
            metadata={
                "audit_table": "clawd_action_log",
                "action_kind": "clawd_action",
                "confidence": payload.get("confidence"),
                "evidence_refs": [
                    ref
                    for ref in (
                        payload.get("before_artifact_ref"),
                        payload.get("after_artifact_ref"),
                        payload.get("manifest_ref"),
                    )
                    if ref
                ],
            },
        )

    def log_agent_loop(self, row: dict[str, Any]) -> None:
        """写入 Agent Loop 中间状态（每轮/每阶段一行）。

        必填字段：request_id, session_id, goal, phase, ts_ms
        可选字段：round, status, goal_achieved, action_plan_json, report_json,
                  action_count, success_count, failed_count, latency_ms, error, extra_json
        """
        payload = {key: row.get(key) for key in AGENT_LOOP_COLUMNS}
        # goal_achieved 存为 0/1 整数
        if payload.get("goal_achieved") is None:
            payload["goal_achieved"] = 0
        else:
            payload["goal_achieved"] = 1 if payload["goal_achieved"] else 0
        # round 默认 0（loop 级事件）
        if payload.get("round") is None:
            payload["round"] = 0
        self._insert("agent_loop_log", payload)

    def log_service_health(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in SERVICE_HEALTH_COLUMNS}
        self._insert("service_health_log", payload)

    def log_migration(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in MIGRATION_COLUMNS}
        self._insert("migration_log", payload)

    def log_config_snapshot(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in CONFIG_SNAPSHOT_COLUMNS}
        self._insert("config_snapshot_log", payload)

    def log_voice_event(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in VOICE_EVENT_COLUMNS}
        self._insert("voice_event_log", payload)
        evidence_refs = _voice_gateway_evidence_refs(
            payload,
            request_id=payload.get("request_id"),
            session_id=payload.get("session_id"),
        )
        person_id = _voice_gateway_person_id(payload, session_id=payload.get("session_id"))
        self._log_life_audit_anchor(
            event_source="voice",
            event_id=str(payload.get("request_id") or f"{payload.get('event_type') or 'voice'}:{payload.get('seq') or 0}"),
            request_id=payload.get("request_id"),
            person_id=person_id,
            session_id=payload.get("session_id"),
            task_id=payload.get("event_type") or "voice_event",
            status=payload.get("status"),
            reason_code=payload.get("reason_code"),
            detail=_join_detail(
                "voice",
                payload.get("event_type"),
                payload.get("direction"),
                payload.get("status"),
                payload.get("detail"),
            ),
            ts_ms=payload.get("ts_ms"),
            metadata={
                "audit_table": "voice_event_log",
                "direction": payload.get("direction"),
                "seq": payload.get("seq"),
                "payload": payload.get("payload"),
                "evidence_refs": evidence_refs,
                "resolved_person_id_source": "voice_gateway_payload" if person_id else None,
            },
        )

    def log_retrieval_event(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in RETRIEVAL_EVENT_COLUMNS}
        self._insert("retrieval_event_log", payload)

    def log_vision_event(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in VISION_EVENT_COLUMNS}
        self._insert("vision_event_log", payload)
        self._log_life_audit_anchor(
            event_source="vision",
            event_id=str(payload.get("source_ref") or payload.get("request_id") or ""),
            request_id=payload.get("request_id"),
            intent_id=payload.get("intent_id"),
            task_id=payload.get("event_type") or "vision_event",
            status=payload.get("status"),
            reason_code=payload.get("reason_code"),
            detail=_join_detail(
                "vision",
                payload.get("event_type"),
                payload.get("status"),
                payload.get("stage"),
                payload.get("detail"),
            ),
            ts_ms=payload.get("ts_ms"),
            metadata={
                "audit_table": "vision_event_log",
                "source_ref": payload.get("source_ref"),
                "payload": payload.get("payload"),
                "evidence_refs": [payload.get("source_ref")] if payload.get("source_ref") else [],
            },
        )

    def log_bond_update(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in BOND_UPDATE_COLUMNS}
        self._insert("bond_update_log", payload)

    def log_sandbox_exec(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in SANDBOX_EXEC_COLUMNS}
        self._insert("sandbox_exec_log", payload)
        self._log_life_audit_anchor(
            event_source="sandbox",
            event_id=str(payload.get("evidence_ref") or payload.get("sandbox_id") or payload.get("request_id") or ""),
            request_id=payload.get("request_id"),
            intent_id=payload.get("intent_id"),
            task_id=payload.get("sandbox_id") or "sandbox_exec",
            status=payload.get("status"),
            reason_code=payload.get("reason_code") or (f"exit_code={payload.get('exit_code')}" if payload.get("exit_code") not in (None, 0) else None),
            detail=_join_detail(
                "sandbox",
                payload.get("sandbox_id"),
                payload.get("status"),
                payload.get("reason_code"),
                payload.get("stdout_preview"),
                payload.get("stderr_preview"),
            ),
            ts_ms=payload.get("ts_ms"),
            metadata={
                "audit_table": "sandbox_exec_log",
                "action_kind": "sandbox_exec",
                "exit_code": payload.get("exit_code"),
                "duration_ms": payload.get("duration_ms"),
                "resource_usage": payload.get("resource_usage"),
                "evidence_refs": [payload.get("evidence_ref")] if payload.get("evidence_ref") else [],
            },
        )

    def log_scheduler_event(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in SCHEDULER_EVENT_COLUMNS}
        self._insert("scheduler_event_log", payload)
        self._log_life_audit_anchor(
            event_source="scheduler",
            event_id=str(payload.get("event_id") or payload.get("intent_id") or payload.get("task_id") or ""),
            request_id=payload.get("event_id"),
            intent_id=payload.get("intent_id"),
            task_id=payload.get("task_id"),
            status=payload.get("status"),
            reason_code=payload.get("reason_code"),
            detail=_join_detail(
                "scheduler",
                payload.get("task_id"),
                payload.get("status"),
                payload.get("reason_code"),
                payload.get("error_detail"),
            ),
            ts_ms=payload.get("ts_ms") or payload.get("trigger_ts"),
            metadata={
                "audit_table": "scheduler_event_log",
                "trigger_ts": payload.get("trigger_ts"),
                "mode_at_trigger": payload.get("mode_at_trigger"),
            },
        )

    def log_capability_forge_proposal(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in CAPABILITY_FORGE_PROPOSAL_COLUMNS}
        payload["proposal_id"] = str(payload.get("proposal_id") or "").strip()
        payload["event_type"] = str(payload.get("event_type") or "").strip().upper() or "UNKNOWN"
        payload["status"] = str(payload.get("status") or "").strip().upper() or "UNKNOWN"
        payload["stage"] = str(payload.get("stage") or "").strip().upper() or "UNKNOWN"
        payload["session_id"] = str(payload.get("session_id") or "").strip() or None
        payload["candidate_key"] = str(payload.get("candidate_key") or "").strip() or None
        payload["reason_code"] = str(payload.get("reason_code") or "").strip().upper() or None
        payload["actor"] = str(payload.get("actor") or "").strip() or None
        payload["ts_ms"] = int(payload.get("ts_ms") or now_ms())
        self._insert("capability_forge_proposal_log", payload)

    def log_p8_subgraph_failure(self, row: dict[str, Any]) -> None:
        """P8-1：写入 subgraph/local loop 失败记录。

        必填字段：request_id, ts_ms, log_category ('subgraph' | 'local_loop'), error_code
        可选字段：execution_profile, failed_node, detail
        """
        payload = {key: row.get(key) for key in P8_SUBGRAPH_FAILURE_LOG_COLUMNS}
        self._insert("p8_subgraph_failure_log", payload)

    def log_p8_graph_run(self, row: dict[str, Any]) -> None:
        """P8-1：写入一次 run_graph 执行记录（延迟 + budget breach）。

        必填字段：request_id, ts_ms
        可选字段：execution_profile, latency_ms, status, budget_breach_code
        """
        payload = {key: row.get(key) for key in P8_GRAPH_RUN_LOG_COLUMNS}
        self._insert("p8_graph_run_log", payload)

    def list_capability_forge_proposals(
        self,
        *,
        proposal_id: str | None = None,
        status: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 20000))
        offset = max(0, int(offset))
        conditions: list[str] = []
        params: list[Any] = []
        if proposal_id is not None and str(proposal_id).strip():
            conditions.append("proposal_id = ?")
            params.append(str(proposal_id).strip())
        if status is not None and str(status).strip():
            conditions.append("status = ?")
            params.append(str(status).strip().upper())
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._conn.execute(
            f"SELECT * FROM capability_forge_proposal_log {where_clause} ORDER BY ts_ms ASC, id ASC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [self._decode_row("capability_forge_proposal_log", row) for row in rows]

    def get_latest_capability_forge_proposals(self, *, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.list_capability_forge_proposals(limit=max(1, int(limit)) * 20, offset=0)
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            proposal_id = str(row.get("proposal_id") or "").strip()
            if not proposal_id:
                continue
            latest[proposal_id] = dict(row)
        ordered = sorted(
            latest.values(),
            key=lambda item: int(item.get("ts_ms") or 0),
            reverse=True,
        )
        return ordered[: max(1, int(limit))]

    # -------------------------------------------------------------------
    # Canon 提案查询与更新
    # -------------------------------------------------------------------

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        """按 proposal_id 获取最新的 canon_proposal_log 行。"""
        row = self._conn.execute(
            "SELECT * FROM canon_proposal_log WHERE proposal_id = ? ORDER BY id DESC LIMIT 1",
            (proposal_id,),
        ).fetchone()
        if row is None:
            return None
        return self._decode_row("canon_proposal_log", row)

    def get_canon_change_by_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        """按 proposal_id 获取对应的 canon_change_log 行（含 before_body / after_body）。"""
        row = self._conn.execute(
            "SELECT * FROM canon_change_log WHERE proposal_id = ? ORDER BY id DESC LIMIT 1",
            (proposal_id,),
        ).fetchone()
        if row is None:
            return None
        return self._decode_row("canon_change_log", row)

    def list_proposals(
        self,
        *,
        status: str | None = None,
        canon_kind: str | None = None,
        target_doc_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
        after_id: int | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """列出 canon_proposal_log 记录，支持过滤和分页。"""
        limit = min(int(limit), 100)
        offset = max(int(offset), 0)

        conditions: list[str] = []
        params: list[Any] = []

        if status is not None:
            conditions.append("status = ?")
            params.append(str(status))
        if canon_kind is not None:
            conditions.append("canon_kind = ?")
            params.append(str(canon_kind))
        if target_doc_id is not None:
            conditions.append("target_id = ?")
            params.append(str(target_doc_id))

        # F-38: cursor-based pagination（after_id 优先于 offset）
        use_cursor = after_id is not None
        if use_cursor:
            conditions.append("id < ?")
            params.append(int(after_id))

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        count_row = self._conn.execute(
            f"SELECT COUNT(*) FROM canon_proposal_log {where_clause}",
            params,
        ).fetchone()
        total = int(count_row[0]) if count_row else 0

        if use_cursor:
            rows = self._conn.execute(
                f"SELECT * FROM canon_proposal_log {where_clause} ORDER BY id DESC LIMIT ?",
                params + [limit],
            ).fetchall()
        else:
            rows = self._conn.execute(
                f"SELECT * FROM canon_proposal_log {where_clause} ORDER BY id DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()

        items: list[dict[str, Any]] = []
        for row in rows:
            raw_id = row["id"] if "id" in row.keys() else None
            decoded = self._decode_row("canon_proposal_log", row)
            if raw_id is not None:
                decoded["row_id"] = int(raw_id)
            items.append(decoded)
        return items, total

    def update_proposal_status(
        self,
        proposal_id: str,
        *,
        new_status: str,
        ts_ms: int | None = None,
    ) -> bool:
        """按 proposal_id 更新提案状态和时间戳。"""
        update_ts = int(ts_ms if ts_ms is not None else now_ms())
        with self._write_lock:
            with self._conn:
                cursor = self._conn.execute(
                    "UPDATE canon_proposal_log SET status = ?, ts_ms = ? WHERE proposal_id = ?",
                    (new_status, update_ts, proposal_id),
                )
        return cursor.rowcount > 0

    def update_proposal_content(
        self,
        proposal_id: str,
        *,
        proposed_title: str,
        proposed_tags: list[str] | None,
        proposed_body: str,
    ) -> bool:
        """Backfill or update reviewable proposal content."""
        with self._write_lock:
            with self._conn:
                cursor = self._conn.execute(
                    """
                    UPDATE canon_proposal_log
                    SET proposed_title = ?, proposed_tags = ?, proposed_body = ?
                    WHERE proposal_id = ?
                    """,
                    (
                        str(proposed_title or "").strip(),
                        self._encode_value(
                            "canon_proposal_log",
                            "proposed_tags",
                            list(proposed_tags or []),
                        ),
                        str(proposed_body or "").strip(),
                        proposal_id,
                    ),
                )
        return cursor.rowcount > 0

    # -------------------------------------------------------------------
    # World Canon Doc
    # -------------------------------------------------------------------

    def upsert_world_canon_doc(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in WORLD_CANON_DOC_COLUMNS}
        if not payload.get("doc_id"):
            raise ValueError("world_canon_doc requires doc_id")

        with self._write_lock:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO world_canon_doc(doc_id, version, hash, status, source_path, updated_ts)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(doc_id) DO UPDATE SET
                        version = excluded.version,
                        hash = excluded.hash,
                        status = excluded.status,
                        source_path = excluded.source_path,
                        updated_ts = excluded.updated_ts
                    """,
                    (
                        payload["doc_id"],
                        payload["version"],
                        payload["hash"],
                        payload["status"],
                        payload["source_path"],
                        payload["updated_ts"],
                    ),
                )

    def get_world_canon_doc(self, doc_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM world_canon_doc WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        if row is None:
            return None
        return self._decode_row("world_canon_doc", row)

    # -------------------------------------------------------------------
    # M10-C: 语义记忆 CRUD
    # -------------------------------------------------------------------

    def log_semantic_memory(self, row: dict[str, Any]) -> None:
        """写入语义记忆条目。

        Active rows remain idempotent, but a soft-deleted row with the same
        semantic identity/source hash is revived. Otherwise a retired synthetic
        semantic can keep occupying the unique source_hash slot and block the
        production semantic distilled later.
        """
        source_ids = row.get("source_memory_ids", [])
        tags = row.get("tags", [])
        payload = {
            "semantic_id": str(row.get("semantic_id", "")),
            "summary": str(row.get("summary", "")),
            "source_memory_ids": json.dumps(list(source_ids), ensure_ascii=False),
            "source_hash": str(row.get("source_hash", "")),
            "distill_strategy": str(row.get("distill_strategy", "merge_top_k")),
            "confidence": float(row.get("confidence", 0.0)),
            "tags": json.dumps(list(tags), ensure_ascii=False),
            "ts_ms": int(row.get("ts_ms", 0)),
            "operator_node_id": str(row.get("operator_node_id", "distillation_engine")),
        }
        cols = list(payload.keys())
        placeholders = ", ".join("?" for _ in cols)
        col_names = ", ".join(cols)
        values = [payload[c] for c in cols]
        with self._write_lock:
            with self._conn:
                existing = self._conn.execute(
                    """
                    SELECT id, deleted_at
                    FROM semantic_memory
                    WHERE source_hash = ? OR semantic_id = ?
                    ORDER BY
                        CASE WHEN source_hash = ? THEN 0 ELSE 1 END,
                        id DESC
                    LIMIT 1
                    """,
                    (
                        payload["source_hash"],
                        payload["semantic_id"],
                        payload["source_hash"],
                    ),
                ).fetchone()
                if existing is not None and existing["deleted_at"] is not None:
                    self._conn.execute(
                        """
                        UPDATE semantic_memory
                        SET semantic_id = ?,
                            summary = ?,
                            source_memory_ids = ?,
                            source_hash = ?,
                            distill_strategy = ?,
                            confidence = ?,
                            tags = ?,
                            ts_ms = ?,
                            operator_node_id = ?,
                            deleted_at = NULL
                        WHERE id = ?
                        """,
                        (
                            payload["semantic_id"],
                            payload["summary"],
                            payload["source_memory_ids"],
                            payload["source_hash"],
                            payload["distill_strategy"],
                            payload["confidence"],
                            payload["tags"],
                            payload["ts_ms"],
                            payload["operator_node_id"],
                            existing["id"],
                        ),
                    )
                    return
                if existing is not None:
                    return
                self._conn.execute(
                    f"INSERT OR IGNORE INTO semantic_memory ({col_names}) VALUES ({placeholders})",
                    values,
                )

    def get_semantic_memory_by_source_hash(self, source_hash: str) -> dict[str, Any] | None:
        """按 source_hash 查询语义记忆（幂等写入查重用）。"""
        row = self._conn.execute(
            "SELECT * FROM semantic_memory WHERE source_hash = ? AND deleted_at IS NULL LIMIT 1",
            (str(source_hash),),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_semantic_memory(row)

    def get_semantic_memory_by_id(self, semantic_id: str) -> dict[str, Any] | None:
        """按 semantic_id 精确查询语义记忆（O(1) 索引查询，避免全量扫描）。"""
        row = self._conn.execute(
            "SELECT * FROM semantic_memory WHERE semantic_id = ? AND deleted_at IS NULL LIMIT 1",
            (str(semantic_id),),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_semantic_memory(row)

    def list_semantic_memories(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """列出语义记忆（分页，按 ts_ms 降序）。"""
        total_row = self._conn.execute(
            "SELECT COUNT(*) FROM semantic_memory WHERE deleted_at IS NULL"
        ).fetchone()
        total = int(total_row[0]) if total_row else 0

        rows = self._conn.execute(
            "SELECT * FROM semantic_memory WHERE deleted_at IS NULL "
            "ORDER BY ts_ms DESC LIMIT ? OFFSET ?",
            (int(limit), int(offset)),
        ).fetchall()
        items = [self._row_to_semantic_memory(r) for r in rows]
        return items, total

    def _row_to_semantic_memory(self, row: Any) -> dict[str, Any]:
        """将 semantic_memory sqlite 行转换为 Python dict。"""
        d = dict(row)
        for json_field in ("source_memory_ids", "tags"):
            raw = d.get(json_field, "[]")
            try:
                d[json_field] = json.loads(raw) if isinstance(raw, str) else list(raw or [])
            except (ValueError, TypeError):
                d[json_field] = []
        return d

    # -------------------------------------------------------------------
    # 事务上下文管理器
    # -------------------------------------------------------------------

    def transaction(self):
        """上下文管理器：将多个 DB 操作包裹在单个 SQLite 事务中（F-35）。

        用法::

            with ledger.transaction():
                ledger.update_proposal_status(...)
                ledger.log_canon_change(...)

        事务内任何异常都会触发 ROLLBACK，正常退出则 COMMIT。
        """
        @contextlib.contextmanager
        def _tx():
            with self._write_lock:
                self._conn.execute("BEGIN")
                try:
                    yield
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise

        return _tx()

    # -------------------------------------------------------------------
    # Schema 初始化
    # -------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        apply_schema(self._conn)
        self._ensure_life_memory_columns()
        self._ensure_canon_proposal_content_columns()

    def _ensure_life_memory_columns(self) -> None:
        additions = {
            "actor_id": "TEXT",
            "intent": "TEXT",
            "action": "TEXT",
            "result": "TEXT",
            "base_salience": "REAL NOT NULL DEFAULT 0.0",
            "memory_strength": "REAL NOT NULL DEFAULT 0.0",
            "decay_profile_id": "TEXT NOT NULL DEFAULT 'NORMAL'",
            "retention_state": "TEXT NOT NULL DEFAULT 'COLD'",
            "last_recalled_ts_ms": "INTEGER",
            "recall_count": "INTEGER NOT NULL DEFAULT 0",
            "reinforcement_count": "INTEGER NOT NULL DEFAULT 0",
            "compressed_into_episode_id": "TEXT",
            "retention_until_ts_ms": "INTEGER",
            "forget_reason": "TEXT",
        }
        changed = False
        for column, ddl_type in additions.items():
            if self._table_has_column("life_episode", column):
                continue
            with self._write_lock:
                with self._conn:
                    self._conn.execute(
                        f"ALTER TABLE life_episode ADD COLUMN {column} {ddl_type}"
                    )
            changed = True
            with self._column_cache_lock:
                self._column_cache.pop("life_episode", None)
        if changed:
            with self._write_lock:
                with self._conn:
                    self._conn.execute(
                        """
                        UPDATE life_episode
                        SET
                            base_salience = COALESCE(NULLIF(base_salience, 0.0), COALESCE(salience, 0.0)),
                            memory_strength = CASE
                                WHEN COALESCE(memory_strength, 0.0) > 0.0 THEN memory_strength
                                ELSE COALESCE(salience, 0.0)
                            END,
                            retention_state = CASE
                                WHEN COALESCE(NULLIF(memory_strength, 0.0), COALESCE(salience, 0.0)) >= 0.75 THEN 'HOT'
                                WHEN COALESCE(NULLIF(memory_strength, 0.0), COALESCE(salience, 0.0)) >= 0.35 THEN 'WARM'
                                WHEN COALESCE(NULLIF(memory_strength, 0.0), COALESCE(salience, 0.0)) >= 0.10 THEN 'COLD'
                                ELSE 'GONE'
                            END
                        """
                    )
        with self._write_lock:
            with self._conn:
                self._conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_life_episode_retention
                    ON life_episode(retention_state, memory_strength, last_touched_ts_ms)
                    """
                )

    def _ensure_canon_proposal_content_columns(self) -> None:
        additions = {
            "proposed_title": "TEXT",
            "proposed_tags": "TEXT",
            "proposed_body": "TEXT",
        }
        for column, ddl_type in additions.items():
            if self._table_has_column("canon_proposal_log", column):
                continue
            with self._write_lock:
                with self._conn:
                    self._conn.execute(
                        f"ALTER TABLE canon_proposal_log ADD COLUMN {column} {ddl_type}"
                    )
            with self._column_cache_lock:
                self._column_cache.pop("canon_proposal_log", None)
