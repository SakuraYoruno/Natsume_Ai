"""LedgerLogMixin：LLM 调用、图运行追踪等写入方法。"""

from __future__ import annotations

import sqlite3
import threading
from typing import Any


class LedgerLogMixin:
    """LLM 和图运行日志写入 Mixin。"""

    _conn: sqlite3.Connection
    _write_lock: threading.Lock

    def log_llm_node_call(self, row: dict[str, Any]) -> None:
        """记录单次 LLM 云节点调用（被动监控节点）。
        必填字段：request_id / node_id / provider / model_id / status / ts_ms
        可选字段：reason_code / latency_ms / input_digest / output_digest
        """
        payload = {
            "request_id": str(row.get("request_id") or ""),
            "node_id": str(row.get("node_id") or ""),
            "provider": str(row.get("provider") or ""),
            "model_id": str(row.get("model_id") or ""),
            "status": str(row.get("status") or "OK"),
            "reason_code": row.get("reason_code"),
            "latency_ms": row.get("latency_ms"),
            "input_digest": row.get("input_digest"),
            "output_digest": row.get("output_digest"),
            "ts_ms": int(row.get("ts_ms") or 0),
        }
        self._insert("llm_node_call_log", payload)  # type: ignore[attr-defined]

    def log_graph_run_trace(self, row: dict[str, Any]) -> None:
        """记录一次完整的 LangGraph 会话链路（每次 graph run 结束后写入）。
        必填字段：request_id / status / ts_ms
        可选字段：session_id / total_latency_ms / reason_code / node_count / nodes_json / edges_json
        """
        payload = {
            "request_id": str(row.get("request_id") or ""),
            "session_id": row.get("session_id"),
            "total_latency_ms": row.get("total_latency_ms"),
            "status": str(row.get("status") or "OK"),
            "reason_code": row.get("reason_code"),
            "node_count": row.get("node_count"),
            "nodes_json": row.get("nodes_json"),
            "edges_json": row.get("edges_json"),
            "ts_ms": int(row.get("ts_ms") or 0),
        }
        keys = list(payload.keys())
        serialized = [self._encode_value("graph_run_trace", k, payload[k]) for k in keys]  # type: ignore[attr-defined]
        placeholders = ", ".join("?" for _ in keys)
        columns = ", ".join(keys)
        sql = f"INSERT OR REPLACE INTO graph_run_trace ({columns}) VALUES ({placeholders})"
        with self._write_lock:
            with self._conn:
                self._conn.execute(sql, serialized)

    def log_llm_route(self, row: dict[str, Any]) -> None:
        """记录 LLM 多模型路由决策（M25-A 正式方法）。
        必填字段：request_id / model_id / reason / ts_ms
        可选字段：latency_ms / fallback_used
        """
        payload = {
            "request_id": str(row.get("request_id") or ""),
            "model_id": str(row.get("model_id") or ""),
            "reason": str(row.get("reason") or ""),
            "latency_ms": int(row.get("latency_ms") or 0),
            "fallback_used": 1 if row.get("fallback_used") else 0,
            "ts_ms": int(row.get("ts_ms") or 0),
        }
        self._insert("llm_route_log", payload)  # type: ignore[attr-defined]

    def log_llm_pool_call(self, row: dict[str, Any]) -> None:
        """记录共享模型池调用尝试与最终落点。"""
        payload = {
            "request_id": str(row.get("request_id") or ""),
            "scene_type": str(row.get("scene_type") or ""),
            "cognitive_tier": str(row.get("cognitive_tier") or ""),
            "attempts_json": str(row.get("attempts_json") or "[]"),
            "attempt_count": int(row.get("attempt_count") or 0),
            "final_model": str(row.get("final_model") or ""),
            "final_provider": str(row.get("final_provider") or ""),
            "pool_exhausted": 1 if row.get("pool_exhausted") else 0,
            "cached": 1 if row.get("cached") else 0,
            "degraded_to_rule": 1 if row.get("degraded_to_rule") else 0,
            "total_latency_ms": int(row.get("total_latency_ms") or 0),
            "ts_ms": int(row.get("ts_ms") or 0),
        }
        self._insert("llm_pool_call_log", payload)  # type: ignore[attr-defined]

    def log_llm_token_usage(self, row: dict[str, Any]) -> None:
        """记录共享模型池调用的 token 用量或估算。"""
        payload = {
            "request_id": str(row.get("request_id") or ""),
            "scene_type": str(row.get("scene_type") or ""),
            "cognitive_tier": str(row.get("cognitive_tier") or ""),
            "provider": str(row.get("provider") or ""),
            "model_id": str(row.get("model_id") or ""),
            "prompt_tokens": int(row.get("prompt_tokens") or 0),
            "completion_tokens": int(row.get("completion_tokens") or 0),
            "total_tokens": int(row.get("total_tokens") or 0),
            "billable_prompt_tokens": int(row.get("billable_prompt_tokens") or 0),
            "billable_completion_tokens": int(row.get("billable_completion_tokens") or 0),
            "billable_total_tokens": int(row.get("billable_total_tokens") or 0),
            "estimated": 1 if row.get("estimated", True) else 0,
            "cached": 1 if row.get("cached") else 0,
            "pool_exhausted": 1 if row.get("pool_exhausted") else 0,
            "attempt_count": int(row.get("attempt_count") or 0),
            "billable_attempt_count": int(row.get("billable_attempt_count") or 0),
            "source": str(row.get("source") or "estimate"),
            "detail_json": row.get("detail_json") or {},
            "ts_ms": int(row.get("ts_ms") or 0),
        }
        self._insert("llm_token_usage_log", payload)  # type: ignore[attr-defined]

    def log_llm_route_config_change(self, row: dict[str, Any]) -> None:
        """记录 LLM 路由配置热更新变更（M26-C）。
        必填字段：request_id / before_hash / after_hash / changed_fields / ts_ms
        """
        payload = {
            "request_id":     str(row.get("request_id") or ""),
            "before_hash":    str(row.get("before_hash") or ""),
            "after_hash":     str(row.get("after_hash") or ""),
            "changed_fields": str(row.get("changed_fields") or "[]"),
            "ts_ms":          int(row.get("ts_ms") or 0),
        }
        self._insert("llm_route_config_log", payload)  # type: ignore[attr-defined]
