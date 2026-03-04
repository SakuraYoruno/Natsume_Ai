"""
TraceNodeBuilder — M18-C / M19-E
从多源审计日志聚合构建结构化节点列表，支持树形结构。
FR-OBS-010 / TASK-0190 / TASK-0197
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("Nastume.obs.trace")

# 节点类型映射（日志表 → node_type）
_LOG_TABLE_TO_NODE_TYPE: Dict[str, str] = {
    "intent_log":       "intent",
    "governor_log":     "governor",
    "skill_exec_log":   "skill",
    "llm_node_call_log":"llm",
    "voice_event_log":  "asr",   # 细分在 _infer_node_type
    "graph_run_trace":  "system",
}

_IO_MAX_BYTES = 64 * 1024  # 64KB


def _safe_json(text: Any) -> Any:
    if isinstance(text, str):
        try:
            return json.loads(text)
        except Exception:
            return text
    return text


def _truncate_io(data: Any) -> tuple[Any, bool]:
    """若 JSON 序列化后超过 64KB，截断并标注 truncated=True。"""
    if data is None:
        return None, False
    try:
        s = json.dumps(data, ensure_ascii=False)
        if len(s.encode()) > _IO_MAX_BYTES:
            return s[:_IO_MAX_BYTES].encode().decode("utf-8", errors="ignore") + "…[truncated]", True
    except Exception:
        pass
    return data, False


class TraceNodeBuilder:
    """
    从 SQLite Ledger 多源日志聚合构建单次请求的节点树。
    BUG-BE-002: 各 _fetch_* 方法改为 SQL 级 request_id 过滤（通过 fetch_by_request_id）。
    WARN-BE-005: _infer_parent_ids 改为基于 node_type 的树形推断，支持并行分支。
    """

    def __init__(self, ledger):
        self._ledger = ledger

    def build(self, request_id: str, include_io: bool = True) -> Dict[str, Any]:
        """
        构建指定 request_id 的节点列表。
        :param request_id: 请求 ID
        :param include_io: 是否包含 input/output 字段
        :return: {request_id, nodes, node_count, status, timestamp}
        :raises KeyError: 若 request_id 不存在
        """
        nodes: List[Dict[str, Any]] = []

        # 1) intent_log
        nodes += self._fetch_intent_nodes(request_id, include_io)
        # 2) governor_log
        nodes += self._fetch_governor_nodes(request_id, include_io)
        # 3) skill_exec_log
        nodes += self._fetch_skill_nodes(request_id, include_io)
        # 4) llm_node_call_log
        nodes += self._fetch_llm_nodes(request_id, include_io)
        # 5) voice_event_log（ASR/TTS）
        nodes += self._fetch_voice_nodes(request_id, include_io)

        if not nodes:
            raise KeyError(f"Trace not found: {request_id}")

        # 按 start_ts_ms 排序
        nodes.sort(key=lambda n: n.get("start_ts_ms") or 0)

        # WARN-BE-005: 基于 node_type 的树形 parent 推断（支持并行分支）
        self._infer_parent_ids(nodes)

        # 推断整体状态
        overall_status = "OK"
        for n in nodes:
            if n.get("status") in ("FAIL", "DOWN", "error"):
                overall_status = "FAIL"
                break

        return {
            "request_id": request_id,
            "nodes":      nodes,
            "node_count": len(nodes),
            "status":     overall_status,
            "timestamp":  int(time.time() * 1000),
        }

    # ------------------------------------------------------------------
    # BUG-BE-002: 各日志表的节点提取 — 改为 SQL 级 request_id 过滤
    # ------------------------------------------------------------------
    def _fetch_by_request_id(self, table: str, request_id: str) -> List[Dict]:
        """
        优先使用 ledger.fetch_by_request_id（SQL 级过滤）；
        若不存在则降级为 fetch_by_time_range + 内存过滤（兼容旧 ledger）。
        """
        if hasattr(self._ledger, "fetch_by_request_id"):
            try:
                return self._ledger.fetch_by_request_id(table, request_id)
            except Exception:
                pass
        # 降级：全量拉取后内存过滤（兼容旧版 ledger）
        try:
            rows = self._ledger.fetch_by_time_range(table, start_ms=0, end_ms=int(time.time() * 1000))
            return [r for r in rows if r.get("request_id") == request_id]
        except Exception:
            return []

    def _fetch_intent_nodes(self, request_id: str, include_io: bool) -> List[Dict]:
        rows = self._fetch_by_request_id("intent_log", request_id)
        result = []
        for r in rows:
            inp, inp_trunc = _truncate_io(_safe_json(r.get("raw_input"))) if include_io else (None, False)
            out, out_trunc = _truncate_io(_safe_json(r.get("parsed_intent"))) if include_io else (None, False)
            result.append({
                "node_id":      f"intent_{r.get('intent_id', r.get('id', 'unknown'))}",
                "node_type":    "intent",
                "node_name":    "Intent Parser",
                "display_name": "Intent Parser",
                "status":       "OK" if r.get("parse_ok") else "FAIL",
                "start_ts_ms":  r.get("ts_ms"),
                "end_ts_ms":    r.get("ts_ms"),
                "duration_ms":  None,
                "input":        inp,
                "output":       out,
                "truncated":    inp_trunc or out_trunc,
                "reason_code":  r.get("reason_code"),
                "parent_node_id": None,
                "side":         "host",
            })
        return result

    def _fetch_governor_nodes(self, request_id: str, include_io: bool) -> List[Dict]:
        rows = self._fetch_by_request_id("governor_log", request_id)
        result = []
        for r in rows:
            decision = r.get("decision", "")
            inp, _ = _truncate_io({"intent_id": r.get("intent_id")}) if include_io else (None, False)
            out, _ = _truncate_io({"decision": decision, "rule_trace": _safe_json(r.get("rule_trace"))}) if include_io else (None, False)
            result.append({
                "node_id":      f"governor_{r.get('id', 'unknown')}",
                "node_type":    "governor",
                "node_name":    "Governor Gate",
                "display_name": "Governor Gate",
                "status":       "OK" if decision == "APPROVE" else "FAIL",
                "start_ts_ms":  r.get("ts_ms"),
                "end_ts_ms":    r.get("ts_ms"),
                "duration_ms":  None,
                "input":        inp,
                "output":       out,
                "reason_code":  r.get("reason_code"),
                "parent_node_id": None,
                "side":         "host",
            })
        return result

    def _fetch_skill_nodes(self, request_id: str, include_io: bool) -> List[Dict]:
        rows = self._fetch_by_request_id("skill_exec_log", request_id)
        result = []
        for r in rows:
            inp, _ = _truncate_io(_safe_json(r.get("args"))) if include_io else (None, False)
            out, _ = _truncate_io(_safe_json(r.get("result"))) if include_io else (None, False)
            result.append({
                "node_id":      f"skill_{r.get('skill_name', 'unknown')}_{r.get('id', '')}",
                "node_type":    "skill",
                "node_name":    r.get("skill_name", "Skill"),
                "display_name": r.get("skill_name", "Skill"),
                "status":       "OK" if r.get("success") else "FAIL",
                "start_ts_ms":  r.get("ts_ms"),
                "end_ts_ms":    r.get("ts_ms"),
                "duration_ms":  r.get("duration_ms"),
                "input":        inp,
                "output":       out,
                "reason_code":  r.get("reason_code"),
                "parent_node_id": None,
                "side":         "host",
            })
        return result

    def _fetch_llm_nodes(self, request_id: str, include_io: bool) -> List[Dict]:
        rows = self._fetch_by_request_id("llm_node_call_log", request_id)
        result = []
        for r in rows:
            inp, _ = _truncate_io(_safe_json(r.get("input_messages"))) if include_io else (None, False)
            out, _ = _truncate_io(_safe_json(r.get("output_text"))) if include_io else (None, False)
            result.append({
                "node_id":      f"llm_{r.get('node_id', 'unknown')}_{r.get('id', '')}",
                "node_type":    "llm",
                "node_name":    r.get("node_id", "LLM Node"),
                "display_name": r.get("node_id", "LLM Node"),
                "status":       "OK" if str(r.get("status", "")).upper() == "OK" else "FAIL",
                "start_ts_ms":  r.get("ts_ms"),
                "end_ts_ms":    r.get("ts_ms"),
                "duration_ms":  r.get("latency_ms"),
                "input":        inp,
                "output":       out,
                "provider":     r.get("provider"),
                "model_id":     r.get("model_id"),
                "reason_code":  None,
                "parent_node_id": None,
                "side":         "host",
            })
        return result

    def _fetch_voice_nodes(self, request_id: str, include_io: bool) -> List[Dict]:
        rows = self._fetch_by_request_id("voice_event_log", request_id)
        result = []
        for r in rows:
            etype = str(r.get("event_type", "")).upper()
            if "ASR" in etype:
                node_type, display = "asr", "ASR"
            elif "TTS" in etype:
                node_type, display = "tts", "TTS"
            else:
                node_type, display = "system", etype or "Voice"
            inp, _ = _truncate_io(_safe_json(r.get("input_data"))) if include_io else (None, False)
            out, _ = _truncate_io(_safe_json(r.get("output_data"))) if include_io else (None, False)
            result.append({
                "node_id":      f"{node_type}_{r.get('id', 'unknown')}",
                "node_type":    node_type,
                "node_name":    display,
                "display_name": display,
                "status":       "OK" if str(r.get("status", "OK")).upper() == "OK" else "FAIL",
                "start_ts_ms":  r.get("ts_ms"),
                "end_ts_ms":    r.get("ts_ms"),
                "duration_ms":  r.get("latency_ms"),
                "input":        inp,
                "output":       out,
                "reason_code":  r.get("reason_code"),
                "parent_node_id": None,
                "side":         "edge",
            })
        return result

    @staticmethod
    def _infer_parent_ids(nodes: List[Dict]) -> None:
        """
        WARN-BE-005: 基于 node_type 的树形 parent 推断，支持并行分支。

        规则：
        - intent  节点：无 parent（根节点）
        - governor 节点：parent 为最近的 intent 节点
        - skill / llm 节点：parent 为最近的 governor 节点
        - asr / tts / vts 节点：parent 为 start_ts_ms 最近的 skill 节点
        - 其他节点：parent 为前一个节点（兜底线性链）

        支持多个节点共享同一 parent（并行分支）。
        """
        # 按 node_type 分组，记录各类型节点（已按 start_ts_ms 排序）
        intent_nodes:   List[Dict] = [n for n in nodes if n["node_type"] == "intent"]
        governor_nodes: List[Dict] = [n for n in nodes if n["node_type"] == "governor"]
        skill_nodes:    List[Dict] = [n for n in nodes if n["node_type"] == "skill"]

        def _nearest_before(candidates: List[Dict], ts: Optional[int]) -> Optional[str]:
            """找到 start_ts_ms <= ts 的最近节点 node_id。"""
            if not candidates:
                return None
            if ts is None:
                return candidates[-1]["node_id"]
            best = None
            for c in candidates:
                c_ts = c.get("start_ts_ms") or 0
                if c_ts <= ts:
                    best = c["node_id"]
            return best or candidates[0]["node_id"]

        for node in nodes:
            if node.get("parent_node_id") is not None:
                continue  # 已有 parent，跳过

            ntype = node["node_type"]
            ts = node.get("start_ts_ms")

            if ntype == "intent":
                node["parent_node_id"] = None

            elif ntype == "governor":
                node["parent_node_id"] = _nearest_before(intent_nodes, ts)

            elif ntype in ("skill", "llm"):
                node["parent_node_id"] = _nearest_before(governor_nodes, ts)

            elif ntype in ("asr", "tts", "vts"):
                # parent 为 start_ts_ms 最近的 skill 节点
                node["parent_node_id"] = _nearest_before(skill_nodes, ts)

            else:
                # 兜底：线性链（前一个节点）
                idx = nodes.index(node)
                if idx > 0:
                    node["parent_node_id"] = nodes[idx - 1]["node_id"]
