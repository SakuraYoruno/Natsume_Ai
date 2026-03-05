# Host Chat Real Chain Trace

- request_id: `req_bc56917feac740b4adea1486d88a6435`
- run_time_utc: `2026-03-05T16:54:12.980000+00:00`
- endpoint: `POST /v1/chat`
- session_id: `session::real-chain-20260305-003`
- thread_id: `thread::real-chain-20260305-003`
- decision: `APPROVE`
- quality_stage: `S_COMPLETE`
- reason_code: `None`

## Query In/Out

### Input Query
```json
{
  "message": "Explain Natsume Host-Edge architecture responsibilities and give two concrete operations notes.",
  "session_id": "session::real-chain-20260305-003",
  "thread_id": "thread::real-chain-20260305-003"
}
```

### Final Answer
```json
{
  "request_id": "req_bc56917feac740b4adea1486d88a6435",
  "narrative": "夏目系统中的架构，主要由“主机大脑”（Host Brain）和“边缘客户端”（Edge Client）两部分构成呢。\n\n“主机大脑”是运行在主机侧的核心决策节点，它负责意图解析、Governor 决策、技能调度和记忆管理，是系统智能的中心。\n\n而“边缘客户端”呢，则运行在边缘设备侧，是一个感知与执行的节点。它主要负责 ASR（自动语音识别）、TTS（文本转语音）、VTS（虚拟形象同步）的驱动，并将感知到的信息通过 RPC 上报给主机大脑。\n\n关于运维，我有两点小小的观察：\n1.  **主机大脑的稳定性：** 因为主机大脑承担了所有核心决策，它的健康运行对整个系统的智能至关重要。在日常运维中，关注其意图解析和技能调度模块的日志与性能，能帮助我们确保系统能准确理解并响应用户。\n2.  **边缘客户端的连接与驱动：** 边缘客户端是用户与系统直接交互的界面。所以，确保边缘设备与主机大脑之间的 RPC 连接稳定，以及 ASR/TTS/VTS 驱动正常工作，是保障用户顺畅体验的关键。任何连接或驱动的异常，都可能直接影响用户感知呢。",
  "decision": "APPROVE",
  "reason_code": null,
  "quality": {
    "stage": "S_COMPLETE",
    "reason_code": null
  },
  "audit_refs": {
    "intent_id": "intent::req_bc56917feac740b4adea1486d88a6435",
    "governor_stage": "S_INTENT_OK",
    "thread_id": "thread::real-chain-20260305-003",
    "retrieval": {
      "status": "OK",
      "reason_code": null,
      "route_count": 2,
      "candidate_count": 4,
      "rerank_topk": 4,
      "evidence_refs": [
        "evd://canon_world::wc-003-persona-core/1",
        "evd://canon_world::wc-001-system-terms/2",
        "evd://canon_style::natsume_ai_cn/3",
        "evd://canon_world::wc-002-architecture-invariants/4"
      ],
      "reason_chain": [],
      "degraded": false
    },
    "llm": {
      "status": "OK",
      "reason_code": null,
      "stage": "S_ORCH_RUN",
      "provider": "gemini",
      "model_id": "gemini-2.5-flash",
      "model_profile_id": "mp.chat.v1",
      "latency_ms": null,
      "draft_only": true
    }
  }
}
```

## Full Node Chain

| # | node_id | type | stage | status | input | output |
|---|---|---|---|---|---|---|
| 1 | `query_ingress` | `http` | `S_REQUEST_RECEIVED` | `OK` | `{"message": "Explain Natsume Host-Edge architecture responsibilities and give two concrete operations notes.", "session_id": "session::real-...` | `{"request_id": "req_bc56917feac740b4adea1486d88a6435", "session_id": "session::real-chain-20260305-003", "thread_id": "thread::real-chain-20...` |
| 2 | `intent_builder` | `system` | `LENIENT_OK` | `OK` | `{"message": "Explain Natsume Host-Edge architecture responsibilities and give two concrete operations notes.", "thread_id": "thread::real-ch...` | `{"intent_id": "intent::req_bc56917feac740b4adea1486d88a6435", "proposer_node_id": "core_reasoner_primary", "normalized_intent": {"action": "...` |
| 3 | `governor_rule_engine` | `rule_engine` | `S_INTENT_OK` | `APPROVE` | `{"intent_id": "intent::req_bc56917feac740b4adea1486d88a6435", "thread_id": "thread::real-chain-20260305-003", "snapshot_refs": {"governance_...` | `{"decision": "APPROVE", "reason_code": null, "tokens": {"approval_token_ref": "approval::intent::req_bc56917feac740b4adea1486d88a6435"}, "ru...` |
| 4 | `retrieval_embedding` | `retrieval` | `S_RET_EMBEDDING` | `OK` | `{"intent_id": "intent::req_bc56917feac740b4adea1486d88a6435", "op_kind": "EMBEDDING"}` | `{"reason_code": null, "detail": "embedding generated", "payload": {"embedding_dim": 1024, "expected_dim": 1024, "latency_ms": 54, "node_sele...` |
| 5 | `retrieval_recall` | `retrieval` | `S_RET_RECALL` | `OK` | `{"intent_id": "intent::req_bc56917feac740b4adea1486d88a6435", "op_kind": "RECALL"}` | `{"reason_code": null, "detail": "multi-route recall fused", "payload": {"candidate_count": 4, "output_status": "OK", "query_length": 95, "ro...` |
| 6 | `retrieval_rerank` | `retrieval` | `S_RET_RERANK` | `OK` | `{"intent_id": "intent::req_bc56917feac740b4adea1486d88a6435", "op_kind": "RERANK"}` | `{"reason_code": null, "detail": "rerank completed", "payload": {"candidate_count": 4, "expected_top_k": 4, "latency_ms": 52, "node_selector"...` |
| 7 | `retrieval_evidence_pack` | `retrieval` | `S_RET_EVIDENCE_PACK` | `OK` | `{"intent_id": "intent::req_bc56917feac740b4adea1486d88a6435", "op_kind": "EVIDENCE_PACK"}` | `{"reason_code": null, "detail": "evidence pack built", "payload": {"evidence_refs_count": 4, "output_status": "OK", "pack_id": "epk_16f04a11...` |
| 8 | `retrieval_pipeline` | `retrieval` | `S_RET_PIPELINE` | `OK` | `{"intent_id": "intent::req_bc56917feac740b4adea1486d88a6435", "op_kind": "PIPELINE"}` | `{"reason_code": null, "detail": "retrieval pipeline completed", "payload": {"evidence_refs": 4, "fallback_embedding_used": false, "output_st...` |
| 9 | `chat_model` | `llm` | `S_ORCH_RUN` | `OK` | `{"provider": "gemini", "model_id": "gemini-2.5-flash", "model_profile_id": "mp.chat.v1", "user_message": "Explain Natsume Host-Edge architec...` | `{"reason_code": null, "drift_detected": false, "actual_param_hash": "c8f48c59db14b314a10c5df35a8a5e002a5495584202403e232565860a5572f6", "llm...` |
| 10 | `final_response` | `response` | `S_COMPLETE` | `OK` | `{"decision": "APPROVE", "reason_code": null, "approval_token_ref": "approval::intent::req_bc56917feac740b4adea1486d88a6435"}` | `{"request_id": "req_bc56917feac740b4adea1486d88a6435", "narrative": "夏目系统中的架构，主要由“主机大脑”（Host Brain）和“边缘客户端”（Edge Client）两部分构成呢。\n\n“主机大脑”是运行...` |

## Rule Engine (Governor)

| gate | hit | reason | snapshot | threshold |
|---|---|---|---|---|
| `R1_TRUST` | `False` | `PASS` | `{"trust_bucket": "normal"}` | `{"min_trust_bucket": ">=30"}` |
| `R2_ENERGY` | `False` | `PASS` | `{"energy_bucket": "normal"}` | `{"min_energy_bucket": ">=20"}` |
| `R3_SYSMODE` | `False` | `PASS` | `{"sys_mode": "normal"}` | `{"blocked_modes": ["locked", "maintenance"]}` |
| `R4_RATE_LIMIT` | `False` | `PASS` | `{"rate_limit_hit": false}` | `{"window": "global_bucket_window"}` |
| `R5_SCOPE` | `False` | `PASS` | `{"scope": "host"}` | `{"allowlist": ["host", "memory", "system"]}` |
| `R6_SAFETY` | `False` | `PASS` | `{"safety_flag": false}` | `{"safety_flag_required": false}` |
| `R7_AIRLOCK` | `False` | `PASS` | `{"airlock_blocked": false}` | `{"airlock_blocked_required": false}` |
| `R8_EMOTION` | `False` | `PASS` | `{"emotion_tag": "NEUTRAL", "mood_state": "steady", "simplified_prompt": false}` | `{"blocked_emotions": ["FRUSTRATED", "ANGRY"], "critical_mood_simplified": true}` |

## Retrieval Multi-route Recall

- Current runtime routes:
```json
{
  "episodic_route": {
    "collection": "episodic",
    "top_k": 8,
    "score_weight": 1.0,
    "node_selector": "default,natsu-node-01"
  },
  "canon_route": {
    "collection": "canon",
    "top_k": 4,
    "score_weight": 0.75,
    "node_selector": "default,natsu-node-01"
  }
}
```
- Runtime embed routes:
```json
{
  "host_default": {
    "provider": "sentence_transformers",
    "model_id": "models/Qwen3-Embedding-0.6B",
    "dim": 1024,
    "timeout_ms": 20000,
    "node_selector": "default,natsu-node-01"
  }
}
```
- Runtime rerank routes:
```json
{
  "host_default": {
    "provider": "qwen3_reranker",
    "model_id": "models/Qwen3-Reranker-0.6B",
    "top_k": 5,
    "timeout_ms": 30000,
    "node_selector": "default,natsu-node-01"
  }
}
```
- Execution events for this request:
  - `EMBEDDING` `S_RET_EMBEDDING` `OK` payload={"embedding_dim": 1024, "expected_dim": 1024, "latency_ms": 54, "node_selector": "default,natsu-node-01", "output_status": "OK", "provider": "sentence_transformers", "route_id": "host_default", "text_length": 95}
  - `RECALL` `S_RET_RECALL` `OK` payload={"candidate_count": 4, "output_status": "OK", "query_length": 95, "route_count": 2, "route_error_count": 0}
  - `RERANK` `S_RET_RERANK` `OK` payload={"candidate_count": 4, "expected_top_k": 4, "latency_ms": 52, "node_selector": "default,natsu-node-01", "output_status": "OK", "provider": "qwen3_reranker", "query_length": 95, "route_id": "host_default", "topk_count": 4}
  - `EVIDENCE_PACK` `S_RET_EVIDENCE_PACK` `OK` payload={"evidence_refs_count": 4, "output_status": "OK", "pack_id": "epk_16f04a11a8345652", "query_length": 95, "serialization_version": "evidence_pack_v1", "topk_count": 4}
  - `PIPELINE` `S_RET_PIPELINE` `OK` payload={"evidence_refs": 4, "fallback_embedding_used": false, "output_status": "OK", "query_length": 95, "recall_candidates": 4, "rerank_topk": 4}

## Model Node I/O

### Chat Model Node
```json
{
  "model_control_log": {
    "id": 25,
    "request_id": "req_bc56917feac740b4adea1486d88a6435",
    "node_id": "chat_model",
    "model_node_id": "chat_model",
    "model_profile_id": "mp.chat.v1",
    "provider": "gemini",
    "model_id": "gemini-2.5-flash",
    "expected_param_hash": null,
    "actual_param_hash": "c8f48c59db14b314a10c5df35a8a5e002a5495584202403e232565860a5572f6",
    "drift_detected": 0,
    "approval_token_ref": null,
    "evidence_ref": null,
    "reason_code": null,
    "status": "OK",
    "stage": "S_ORCH_RUN",
    "ts_ms": 1772729652980
  },
  "llm_summary": {
    "status": "OK",
    "reason_code": null,
    "stage": "S_ORCH_RUN",
    "provider": "gemini",
    "model_id": "gemini-2.5-flash",
    "model_profile_id": "mp.chat.v1",
    "latency_ms": null,
    "draft_only": true
  },
  "model_input_summary": {
    "user_message": "Explain Natsume Host-Edge architecture responsibilities and give two concrete operations notes.",
    "retrieval_summary": {
      "status": "OK",
      "reason_code": null,
      "route_count": 2,
      "candidate_count": 4,
      "rerank_topk": 4,
      "evidence_refs": [
        "evd://canon_world::wc-003-persona-core/1",
        "evd://canon_world::wc-001-system-terms/2",
        "evd://canon_style::natsume_ai_cn/3",
        "evd://canon_world::wc-002-architecture-invariants/4"
      ],
      "reason_chain": [],
      "degraded": false
    }
  },
  "model_output_summary": {
    "narrative": "夏目系统中的架构，主要由“主机大脑”（Host Brain）和“边缘客户端”（Edge Client）两部分构成呢。\n\n“主机大脑”是运行在主机侧的核心决策节点，它负责意图解析、Governor 决策、技能调度和记忆管理，是系统智能的中心。\n\n而“边缘客户端”呢，则运行在边缘设备侧，是一个感知与执行的节点。它主要负责 ASR（自动语音识别）、TTS（文本转语音）、VTS（虚拟形象同步）的驱动，并将感知到的信息通过 RPC 上报给主机大脑。\n\n关于运维，我有两点小小的观察：\n1.  **主机大脑的稳定性：** 因为主机大脑承担了所有核心决策，它的健康运行对整个系统的智能至关重要。在日常运维中，关注其意图解析和技能调度模块的日志与性能，能帮助我们确保系统能准确理解并响应用户。\n2.  **边缘客户端的连接与驱动：** 边缘客户端是用户与系统直接交互的界面。所以，确保边缘设备与主机大脑之间的 RPC 连接稳定，以及 ASR/TTS/VTS 驱动正常工作，是保障用户顺畅体验的关键。任何连接或驱动的异常，都可能直接影响用户感知呢。"
  }
}
```

### Retrieval Embedding Model Node
```json
{
  "embedding_dim": 1024,
  "expected_dim": 1024,
  "latency_ms": 54,
  "node_selector": "default,natsu-node-01",
  "output_status": "OK",
  "provider": "sentence_transformers",
  "route_id": "host_default",
  "text_length": 95
}
```

### Retrieval Rerank Model Node
```json
{
  "candidate_count": 4,
  "expected_top_k": 4,
  "latency_ms": 52,
  "node_selector": "default,natsu-node-01",
  "output_status": "OK",
  "provider": "qwen3_reranker",
  "query_length": 95,
  "route_id": "host_default",
  "topk_count": 4
}
```

## Observability Status

- `/observability/trace` source counts:
```json
{
  "governor_log": 1,
  "intent_log": 1,
  "model_control_log": 1,
  "retrieval_event_log": 5
}
```
- `/obs/trace/{request_id}` output:
```json
{
  "request_id": "req_bc56917feac740b4adea1486d88a6435",
  "session_id": null,
  "status": "OK",
  "total_latency_ms": null,
  "node_count": 1,
  "nodes": [
    {
      "node_id": "governor",
      "node_type": "system",
      "display_name": "Governor",
      "provider": null,
      "model_id": null,
      "status": "OK",
      "latency_ms": null,
      "input_data": null,
      "output_data": null,
      "ts_ms": 1772729652980,
      "error": null
    }
  ],
  "edges": [],
  "ts_ms": 1772729652980,
  "timestamp": 1772729682736
}
```

## Limits Found

- No `graph_run_trace` row for this request, so `/obs/trace` degraded to governor-only graph view.
- No `llm_node_call_log` row for this request, so full raw prompt/completion digests are not persisted.
- Runtime recall routes are `2` (`episodic_route`, `canon_route`), not six-route in current host config.