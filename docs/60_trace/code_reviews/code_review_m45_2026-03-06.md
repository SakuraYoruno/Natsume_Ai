# M45 代码审查报告（2026-03-06）

**里程碑**：M45 Edge 运行时装配收口  
**范围**：TASK-0440 ~ TASK-0444  
**审查人**：AI Coding Agent

## 1. 目标与结论

M45 目标是补齐 Edge 运行时装配闭环，使 `python -m edge_client` 可以稳定启动，并输出统一健康快照。  
本次已完成：

- TASK-0440：`runtime/bootstrap.py` 接口收口（`bootstrap/startup/shutdown`）；
- TASK-0441：`runtime/health.py` 收口（`collect_health` + 聚合规则）；
- TASK-0442：`__main__.py` 启动入口修复并接入 FastAPI lifespan；
- TASK-0443：新增 M45 测试矩阵（13 个）并执行回归；
- TASK-0444：本报告落地。

## 2. 代码变更摘要

### TASK-0440 M45-A：`edge_client/runtime/bootstrap.py`

- 保留原 `build_edge_runtime()` 装配能力，并新增 `bootstrap(config)` 别名入口。
- 新增 `startup(runtime)`：
  - 按顺序调用组件生命周期（`asr -> tts -> vts -> fallback -> resource_gate -> rpc_client`）；
  - 仅在组件存在 `startup()` 时调用；
  - 单组件异常会记录日志，不中断其他组件。
- 新增 `shutdown(runtime)`：
  - 按逆序调用组件 `shutdown()`；
  - 最后执行 `runtime.close()` 作为兼容收口。
- 在 `EdgeRuntime` 增加兼容属性别名：
  - `asr/tts/vts/fallback/rpc_client` 映射到旧字段，兼容新旧调用口径。

### TASK-0441 M45-B：`edge_client/runtime/health.py`

- 新增 `collect_health(runtime)`，输出结构：
  - `status`（`ok|degraded|down`）；
  - `components`（组件级健康）；
  - `resource_gate`（含 `snapshot` 与 `queue_depth`）；
  - `timestamp`（ISO 8601）。
- 健康聚合规则落地：
  - 任一 `down` -> `down`；
  - 否则任一 `degraded/error` -> `degraded`；
  - 否则 `ok`。
- 保持旧接口 `build_edge_health_snapshot()` 兼容：
  - 原有 `stage/services/resources/components/config_snapshot/ts_ms` 字段继续保留；
  - 同时补充 `status/timestamp/resource_gate`。

### TASK-0442 M45-C：`edge_client/__main__.py` + `edge_client/app/fastapi_app.py`

- `__main__.py`：
  - 显式导入并接入 `bootstrap/startup/shutdown/collect_health`；
  - 新增 argparse 入口（支持 `--help`，并允许 `--host/--port` 覆盖）；
  - `if __name__ == "__main__"` 改为 `main(sys.argv[1:])`。
- `fastapi_app.py`：
  - `create_edge_fastapi_app()` 新增可注入 hook：
    - `startup_hook`、`shutdown_hook`、`health_collector`；
  - FastAPI `lifespan` 中调用 startup/shutdown；
  - `/v1/edge/health` 路由接入 `collect_health`，并保持旧快照结构字段不丢失。

### TASK-0443 M45-D：测试与回归

- 新增测试文件：
  - `tests/unit/test_edge_runtime_bootstrap_m45.py`（T-EDGE-BOOT-01~05）
  - `tests/unit/test_edge_runtime_health_m45.py`（T-EDGE-HEALTH-01~05）
  - `tests/unit/test_edge_main_m45.py`（T-EDGE-MAIN-01~03）

## 3. 测试执行记录

### 3.1 M45 新增测试

命令：

```bash
python -m pytest tests/unit/test_edge_runtime_bootstrap_m45.py tests/unit/test_edge_runtime_health_m45.py tests/unit/test_edge_main_m45.py -q --tb=short
```

结果：**13 passed**

### 3.2 Edge 关键回归（契约）

命令：

```bash
python -m pytest tests/contract/test_edge_bootstrap_contract.py tests/contract/test_edge_health_contract.py tests/contract/test_edge_rpc_gateway_contract.py -q --tb=short
```

结果：**8 passed**

### 3.3 Edge 相关范围回归（过滤执行）

命令：

```bash
python -m pytest tests/contract tests/unit -k edge -q --tb=short
```

结果：**142 passed, 2152 deselected**

### 3.4 全量回归（TASK-0443 要求）

命令：

```bash
python -m pytest --tb=short -q
```

结果：**2481 passed, 11 skipped, 3 failed**

失败项：

1. `tests/contract/test_llm_gateway_contract.py::TestLlmGatewayContract::test_gateway_provider_unavailable_deepseek_or_gemini_cold_start_degrades`
2. `tests/integration/test_host_milestone_trial_run.py::TestRetrievalEmbedding::test_embedding_single_text`
3. `tests/integration/test_host_milestone_trial_run.py::TestMilestoneSummaryReport::test_generate_milestone_summary`

说明：失败集中在 LLM Gateway 与 Embedding 试运行链路，未涉及本次 M45 变更文件（Edge runtime/bootstrap/health/main）。M45 新增测试与 Edge 相关回归均已通过。

## 4. 变更文件清单

- `edge_client/runtime/bootstrap.py`（修改）
- `edge_client/runtime/health.py`（修改）
- `edge_client/runtime/__init__.py`（修改）
- `edge_client/app/fastapi_app.py`（修改）
- `edge_client/__main__.py`（修改）
- `tests/unit/test_edge_runtime_bootstrap_m45.py`（新增）
- `tests/unit/test_edge_runtime_health_m45.py`（新增）
- `tests/unit/test_edge_main_m45.py`（新增）
- `docs/60_trace/code_reviews/code_review_m45_2026-03-06.md`（新增）

## 5. 审查结论

- M45 的核心交付（runtime 装配、生命周期、健康聚合、主入口修复）已完整落地。
- M45 定义的新增测试（>=13）已满足且全部通过。
- 全量回归已执行并留痕；当前存在 3 个非 M45 相关失败，建议在后续里程碑单独处理。
