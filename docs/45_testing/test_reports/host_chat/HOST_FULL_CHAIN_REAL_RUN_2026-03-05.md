受众：MASTER
标题：HOST 全服务全链路真实环境运行报告（2026-03-05）

# HOST 全服务全链路真实环境运行报告（2026-03-05）

## 1. 执行概览
- 执行时间（UTC+8）: 2026-03-05 23:41 ~ 23:54
- 运行环境: `Natsume_ENV=dev`
- Host: `127.0.0.1:18080`（PID `44608`）
- Edge: `127.0.0.1:17800`（PID `40596`）
- 目标结论: **未满足“所有服务不降级”**
- 核心结论:
  - 注册在服务注册表中的 14 个必需服务均为 `UP`。
  - 但真实链路存在降级项（见第 5 节），因此“全服务无降级”目标未达成。

## 2. 证据文件
- `runtime/reports/start_host_side_20260305_234151.json`
- `runtime/reports/start_edge_side_20260305_234222.json`
- `runtime/reports/real_full_chain_probe_20260305_234847.json`
- `runtime/reports/chat_real_generation_en_20260305_235452.json`
- `runtime/reports/env_readiness_report_runtime_aligned_20260305_235400.json`
- `runtime/reports/env_readiness_report_with_fixture_inventory_20260305_235255.json`
- `runtime/reports/env_readiness_report_20260305_234921.json`（用于定位默认节点清单路径问题）

## 3. 14 个必需服务逐项结果（不漏项）
数据来源: `GET /startup` 返回 `report.services`

| service_id | owner | status | reason_code | detail |
|---|---|---|---|---|
| host.audit.sqlite_ledger | host | UP | - | sqlite ledger path accessible: host_brain/data/audit.sqlite3 |
| host.governor.decision | host | UP | - | governor decision engine ready |
| host.skills.loader | host | UP | - | skills loader ready (env=dev) |
| host.integrations.llm_gateway | host | UP | - | llm gateway enabled |
| host.retrieval.embedding | host | UP | - | retrieval embedding enabled |
| host.retrieval.reranker | host | UP | - | retrieval reranker enabled |
| host.vision.screen_ocr | host | UP | - | vision screen_ocr enabled |
| host.vision.camera_recognizer | host | UP | - | vision camera_recognizer enabled |
| edge.rpc.gateway | edge | UP | - | edge rpc gateway enabled |
| edge.rpc.asr | edge | UP | - | edge asr enabled |
| edge.rpc.tts | edge | UP | - | edge tts enabled |
| edge.rpc.barge_in | edge | UP | - | edge barge-in enabled |
| edge.rpc.fallback_llm | edge | UP | - | edge fallback llm enabled |
| edge.rpc.resource_gate | edge | UP | - | edge resource gate enabled |

## 4. 全链路接口实测结果

| Probe | HTTP | 延迟(ms) | 结果 |
|---|---:|---:|---|
| host.health | 200 | 39 | OK |
| host.ready | 200 | 19 | 返回 `DEGRADED` |
| host.startup | 200 | 14 | `boot_status=READY`, required_unavailable=0 |
| host.health.mcp | 200 | 14 | OK |
| host.health.canon_sync | 200 | 13 | OK |
| host.obs.services.health | 200 | 14 | 返回 15 项监控服务状态（多项 UNKNOWN） |
| edge.health (`/v1/edge/health`) | 200 | 13 | ACCEPTED |
| host.chat.real_generation | 200 | 18079 | 真实生成链路已走通 |

## 5. 降级项与原因（详细）

### 5.1 Edge 启动前置检查降级（ASR/TTS/VTS）
来源: `start_edge_side_20260305_234222.json`
- `asr_status=degraded`
- `tts_status=degraded`
- `vts_status=degraded`
- `startup_warnings`:
  - `STARTUP_ASR_DEGRADED`
  - `STARTUP_TTS_DEGRADED`
  - `STARTUP_VTS_DEGRADED`
说明: 这是 `start_edge.ps1` 对本地端口探测的降级告警；与 `GET /v1/edge/health` 中组件“loaded/available”是两个维度。

### 5.2 Host `/ready` 返回 DEGRADED
来源: `real_full_chain_probe_20260305_234847.json` -> `host.ready`
- `ready=false`
- `status=DEGRADED`
- 直接原因: `dependency_matrix` 中 `graph_runtime` 不可用（`status=UNKNOWN`，无 reason_code）
- 同时 `services.required_unavailable=0`，即 14 个必需服务未掉线。

### 5.3 环境 readiness 报告降级（Neo4j 认证失败）
来源: `env_readiness_report_runtime_aligned_20260305_235400.json`
- `readiness_status=DEGRADED`
- `reason_code=SERVICE_UNAVAILABLE_DEPENDENCY`
- 不可用依赖:
  - `host.bond.neo4j`
  - 错误明细: `Neo.ClientError.Security.Unauthorized`（认证失败）
说明: 当前 `sec://neo4j/local-default` 未配置有效密码，导致真实 Neo4j 探针失败。

### 5.4 真实 `/v1/chat` 生成链路中的检索降级
来源: `chat_real_generation_en_20260305_235452.json`
- 决策: `decision=APPROVE`
- LLM: `provider=gemini`, `model_id=gemini-2.5-flash`, `status=OK`
- 检索: `status=DEGRADED`, `reason_code=RETR_EPACK_INPUT_INVALID`
- reason_chain:
  - `CHROMA_ENDPOINT_UNAVAILABLE`
  - `RETR_RERANK_INPUT_INVALID`
  - `RETR_EPACK_INPUT_INVALID`
说明: 真实云模型生成成功，但检索子链路当前为降级态。

### 5.5 分布式一键启动脚本的运行问题（附加）
来源: 本次执行记录
- `scripts/runtime/start_distributed_services.ps1` 在传参链路中触发 host 参数错位（`--host` 被误传），导致无法稳定用于本次真实拉起。
- 本次改为分别调用 `start_host_side.ps1` + `start_edge_side.ps1` 完成真实运行与测试。

## 6. 最终判定
- “不漏任意服务”核对结果: **已覆盖并逐项列出 14/14 必需服务**。
- “所有服务不降级”判定结果: **未达成**。
- 当前可确认无降级的范围:
  - 服务注册表 14 必需服务：全部 `UP`。
- 当前仍有降级的范围:
  - Edge 启动前置健康探测（ASR/TTS/VTS）
  - Host `/ready`（graph_runtime UNKNOWN）
  - 环境依赖探针（Neo4j 认证失败）
  - Chat 检索子链路（RETR_EPACK_INPUT_INVALID）
