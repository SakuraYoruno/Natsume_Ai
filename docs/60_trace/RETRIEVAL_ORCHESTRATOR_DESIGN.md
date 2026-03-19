受众：MASTER
标题：RetrievalOrchestrator 设计契约

# RetrievalOrchestrator 设计契约

**版本**：v1.1
**创建日期**：2026-03-09
**最后更新**：2026-03-19
**状态**：已落地（同步路径），流式路径待接入

## 1. Goal
Define a stable retrieval orchestration contract for chat paths:
- unified input protocol: `query`, `session_state`, `trace_id`
- unified output protocol: `retrieval_bundle`
- explicit path semantics: `success / partial_failure / all_failure`

Implemented in:
- `host_brain/retrieval/orchestrator.py`

## 2. Interface

### 2.1 Request
`RetrievalOrchestratorRequest`
- `trace_id: str`
- `query: str`
- `session_state: dict[str, Any]`
- `request_id: str`
- `session_id: str | None`
- `intent_id: str | None`
- `top_k: int`
- `ts_ms: int | None`
- `emotion_context: str | None`

### 2.2 Result
`RetrievalOrchestratorResult`
- `success: bool`
- `status: str`
- `reason_code: str | None`
- `path: str` (`success | partial_failure | all_failure`)
- `detail: str`
- `trace_id: str`
- `query: str`
- `session_state: dict[str, Any]`
- `retrieval_summary: dict[str, Any]`
- `retrieval_evidence_items: list[dict[str, Any]]`
- `retrieval_bundle: dict[str, Any]`

### 2.3 retrieval_bundle shape
```json
{
  "trace_id": "...",
  "query": "...",
  "session_state": { "...": "..." },
  "path": "success|partial_failure|all_failure",
  "summary": { "...": "..." },
  "evidence_items": [ { "...": "..." } ]
}
```

## 3. Timeout & Degrade
- `arun(..., timeout_ms)` supports orchestration timeout.
- Timeout maps to:
  - `status=DEGRADED`
  - `reason_code=RETRIEVAL_TIMEOUT`
  - `path=all_failure`
- Runtime exception maps to:
  - `status=DEGRADED`
  - `reason_code=RETRIEVAL_FAILED`
  - `path=all_failure`

## 4. Path Rules
- `success`:
  - `status=OK` and `degraded=false`
- `partial_failure`:
  - degraded, but has any useful output (`route_count>0` or `candidate_count>0` or `rerank_topk>0` or `evidence_items>0`)
- `all_failure`:
  - degraded and no usable retrieval payload

## 5. Runtime Integration
- `/v1/chat`（同步路径）：
  - 已切换到 `RetrievalOrchestrator.arun(...)`
  - 继续将 `retrieval_summary/retrieval_evidence_items` 喂入 prompt builder
- `/v1/chat/stream`（流式路径）：
  - `_run_retrieval(...)` 已委托给 orchestrator
  - 保持原函数签名以兼容测试

## 6. Tests
- `tests/unit/test_retrieval_orchestrator.py`
  - success path
  - partial failure path
  - all failure path
  - timeout degrade mapping

## 7. 当前接入状态（2026-03-19）

> **P15 封板后的现状评估**

| 路径 | 召回接入 | 情感层接入 | 审计写入 | 状态 |
|------|---------|-----------|---------|------|
| `/v1/chat`（同步 Graph） | ✅ 已接入 | ✅ 已接入 | ✅ 已接入 | 正常 |
| `/v1/chat/stream`（流式） | ⚠️ 委托给 orchestrator，但实际绕过了六路召回 | ❌ 未接入 | ❌ 未接入 | **待修复** |

### 已知问题

**流式路径是"残缺路径"**：当前 `/v1/chat/stream` 虽然调用了 `_run_retrieval()`，但实际上流式路径绕过了完整的 Graph 节点链，导致：
- 六路召回（Episodic / StyleCanon / WorldCanon / Bond / 全文检索 / 重排）未完整执行
- 情感层（`emotion_context.py`）未接入
- Governor 审计链未写入
- Bond 更新未触发

### 修复方向

参见 `docs/60_trace/ARCH_REVIEW_AND_ROADMAP.md` §四（流式Chat的正确架构设计）：
- 召回在流式输出**前**并行完成（~200ms）
- 审计写入在**后台异步**进行（不阻塞首字节）
- 情感层在召回后、LLM 调用前读取

### 变更记录

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-03-09 | 初版，定义 orchestrator 契约，同步路径已落地 |
| v1.1 | 2026-03-19 | 补充当前接入状态评估，标注流式路径残缺问题；文件重命名去日期后缀 |
