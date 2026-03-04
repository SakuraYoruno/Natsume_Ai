# M17/M18 监控面板 v3 代码审查报告

**审查日期**：2026-03-04
**审查范围**：M17（前端 SPA）+ M18（后端 API 扩展）
**审查人**：AI Code Review
**代码状态**：DONE（1577 全量通过，0 failed）

---

## 一、总体评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构合规性 | ⭐⭐⭐⭐⭐ | 完全符合 DevSpec §29 约定 |
| 代码质量 | ⭐⭐⭐⭐ | 整体清晰，存在若干可改进点 |
| 测试覆盖 | ⭐⭐⭐⭐ | 后端 26 个测试全覆盖，前端无自动化测试 |
| 安全性 | ⭐⭐⭐ | 存在 2 个中风险项 |
| 性能 | ⭐⭐⭐ | 存在 2 个潜在性能瓶颈 |

---

## 二、前端代码审查（M17）

### 2.1 架构合规性 ✅

| 契约项 | 实际状态 | 结论 |
|--------|---------|------|
| React 18 + Vite 5 | `react@18.3.1` + `vite@5.4.11` | ✅ 符合 |
| Tailwind CSS 3 | `tailwindcss@3.4.16` | ✅ 符合 |
| React Router 6 | `react-router-dom@6.28.0` | ✅ 符合 |
| React Flow | `@xyflow/react@12.3.6` | ✅ 符合 |
| ECharts 5 | `echarts@5.5.1` | ✅ 符合 |
| Zustand | `zustand@5.0.2` | ⚠️ 契约要求 4.x，实际安装 5.x（API 兼容，但版本偏差） |
| Lucide React | `lucide-react@0.468.0` | ✅ 符合 |
| 4 个核心页面路由 | Overview/Sessions/Trace/Metrics | ✅ 符合 |
| 产出目录 | `static/monitor/` | ✅ 符合 |

**发现 [WARN-FE-001]**：`zustand` 实际版本为 `5.0.2`，DevSpec §29.2 约定为 `4.x`。Zustand 5 的 `create` API 有破坏性变更（`immer` 中间件签名变化），当前代码使用基础 `create` 无影响，但文档版本约定需同步更新。

---

### 2.2 路由设计 ✅

`App.jsx` 使用 `HashRouter`，路由结构：

```
/ → /overview（重定向）
/overview → Overview.jsx
/sessions → Sessions.jsx
/trace → Trace.jsx
/trace/:requestId → Trace.jsx（带参数）
/metrics → Metrics.jsx
```

**发现 [WARN-FE-002]**：DevSpec §29.3.2 约定 Sessions → Trace 跳转使用 `#trace?rid=xxx`（URL hash query），但实际实现为 `navigate('/trace/${s.request_id}')`（路径参数），两者功能等价但与契约描述不一致。建议更新 DevSpec 以反映实际实现。

---

### 2.3 Zustand Store ✅

`useAppStore.js` 实现的字段：

| 契约字段 | 实际字段 | 状态 |
|---------|---------|------|
| `pollingEnabled` | ✅ | 符合 |
| `lastPolledAt` | ❌ 缺失 | 未实现 |
| `pollingError` | ❌ 缺失 | 未实现 |
| `timeRange` | ✅ | 符合 |
| `selectedRequestId` | ✅ | 符合 |

**发现 [WARN-FE-003]**：Store 缺少 `lastPolledAt` 和 `pollingError` 两个契约字段。当前 Overview 页的轮询错误通过组件本地 `useState` 管理，未走全局 Store，导致多页面无法共享轮询状态。

---

### 2.4 Overview 页 ⚠️

**发现 [BUG-FE-001]（中）**：`Overview.jsx` 中汇总统计行读取的是 `data?.summary` 字段（`summary.total / summary.healthy / summary.degraded / summary.error`），但 `obs_routes.py` 的 `ServicesHealthResponse` 返回的是顶层字段 `total / up / degraded / down`，**不存在 `summary` 子对象**。

```javascript
// 当前代码（错误）
const summary = data?.summary ?? {}
// 读取 summary.total / summary.healthy / summary.degraded / summary.error

// 实际 API 响应结构
{ total: 15, up: 12, degraded: 2, down: 1, unknown: 0, services: [...] }
```

这会导致总览页顶部 4 个统计卡片**始终显示 `—`**，是功能性 Bug。

**发现 [WARN-FE-004]**：`ServiceCard` 组件接收 `name / status / latency / detail` 属性，但 API 返回的服务对象字段为 `service_id / display_name / status / p95_ms / call_count`，字段名不匹配，服务卡片内容可能无法正确渲染。

---

### 2.5 Sessions 页 ✅

整体实现良好。过滤栏（时间范围/状态/关键词）、分页、行点击跳转 Trace 均已实现。

**发现 [WARN-FE-005]**：翻页时 `load(page + 1)` 与 `setPage(page + 1)` 同时调用，但 `load` 的 `useCallback` 依赖了 `page`，存在闭包陈旧值问题——翻页后 `load` 内部的 `page` 仍是旧值，实际请求的页码可能不正确。建议将 `page` 作为参数传入而非依赖闭包。

---

### 2.6 Trace 页 ✅

React Flow 实现完整，Dagre 布局、CustomNode、NodeDetailPanel、全屏切换均已实现。

**发现 [WARN-FE-006]**：`api/obs.js` 中 `fetchTraceNodes` 调用的端点为 `/obs/trace/${requestId}/nodes`，但 `obs_routes.py` 注册的端点为 `/obs/trace/{request_id}`（**无 `/nodes` 后缀**）。前端调用路径与后端路由不匹配，真实 API 调用会返回 404。

当前通过 Mock 数据掩盖了此问题（`id.startsWith('mock')` 走 Mock 分支），但切换真实 API 后会立即暴露。

---

### 2.7 Metrics 页 ✅

ECharts 四图表实现完整，时间范围选择器通过 Zustand 驱动。

**发现 [WARN-FE-007]**：Metrics 页有 Mock 降级逻辑（后端不可用时使用 Mock 数据），这是合理的防御性设计，但 Mock 数据使用 `Math.random()` 生成，每次刷新数据不稳定，可能干扰运维判断。建议 Mock 降级时显示明显的"数据来源：模拟"标注。

---

## 三、后端代码审查（M18）

### 3.1 obs_routes.py ⚠️

**发现 [BUG-BE-001]（高）**：`GET /obs/trace/{request_id}` 端点在 `row is None` 的降级路径中，尝试访问 `ledger.governor_log`（直接属性访问），但 `SQLiteLedger` 并不暴露 `.governor_log` 属性，应使用 `ledger.fetch_by_time_range("governor_log", ...)` 方法。此路径会抛出 `AttributeError`，导致 500 错误。

```python
# 当前代码（错误）
gov_rows = [r for r in ledger.governor_log if r.get("request_id") == request_id]

# 应改为
gov_rows = [r for r in ledger.fetch_by_time_range("governor_log", 0, _now_ms())
            if r.get("request_id") == request_id]
```

**发现 [WARN-BE-001]**：`obs_routes.py` 的 `GET /obs/services/health` 端点与 `service_health_aggregator.py` 的 `ServiceHealthAggregator` 类**完全独立实现**，存在逻辑重复。`obs_routes.py` 直接从 `voice_event_log` 和 `llm_node_call_log` 聚合，而 `ServiceHealthAggregator` 使用探测函数模式。两套实现的服务列表也不一致（`obs_routes.py` 有 6 个边缘服务 + 9 个 LLM 节点；`ServiceHealthAggregator` 有 10 个 Host + 5 个 Edge）。

**发现 [WARN-BE-002]**：`obs_routes.py` 的 `GET /obs/sessions` 端点与 `session_query.py` 的 `SessionQueryService` 类**完全独立实现**，同样存在逻辑重复。路由层直接实现了查询逻辑，而 Service 层的实现更完整（支持 `channel` 过滤、`time_range` 字符串参数）。

**发现 [WARN-BE-003]**：`obs_routes.py` 的 `GET /obs/metrics/timeseries` 端点与 `metrics_timeseries.py` 的 `MetricsTimeseriesService` 类**完全独立实现**，第三次重复。

> **根因分析**：M18 同时产出了 Service 层（`host_brain/obs/`）和 Route 层（`obs_routes.py`），但 Route 层未调用 Service 层，导致三组平行实现。Service 层的实现质量更高（更完整的过滤参数、降采样、reason_code 分布），但实际对外暴露的是 Route 层的简化版本。

---

### 3.2 service_health_aggregator.py ✅

实现质量高。并发探测、超时保护、side 过滤均正确实现。

**发现 [WARN-BE-004]**：`ServiceHealthResult.to_dict()` 返回的字段名为 `name`，但 DevSpec §29.8.1 约定的响应字段名为 `service_name`。字段名偏差可能导致前端解析失败。

---

### 3.3 trace_node_builder.py ⚠️

**发现 [BUG-BE-002]（中）**：`_fetch_intent_nodes`、`_fetch_governor_nodes`、`_fetch_skill_nodes`、`_fetch_voice_nodes` 均使用 `fetch_by_time_range(table, 0, int(time.time() * 1000))` 拉取**全量数据**后在内存中过滤 `request_id`。当审计日志量大时（如运行数月后），这会导致严重的内存和性能问题。

**发现 [WARN-BE-005]**：`_infer_parent_ids` 使用简单线性链（每个节点的 parent 是前一个节点），无法正确表达并行分支结构（如 `governor → memory` 和 `governor → llm_safe` 同时发生）。DevSpec §29.6.1 要求"支持分支/并行"，当前实现不满足此要求。

---

### 3.4 session_query.py ✅

实现完整，过滤逻辑清晰，分页正确。

**发现 [WARN-BE-006]**：`_fmt_ts` 函数使用 `datetime.datetime.fromtimestamp`（本地时区），在服务器时区与用户时区不一致时会显示错误时间。建议使用 `datetime.datetime.utcfromtimestamp` 并附加 UTC 标注，或使用 ISO 8601 格式。

---

### 3.5 metrics_timeseries.py ✅

实现最为完整，降采样、多 metric 批量查询、reason_code 分布均已实现。

**发现 [WARN-BE-007]**：`_compute_latency_dist` 仅从 `graph_run_trace` 的 `total_latency_ms` 聚合，只有 `Overall` 一个维度，无法按服务类型（ASR/TTS/LLM）分解延迟。DevSpec §29.7.1 要求"各服务响应延迟 P50/P95 柱状图"，当前实现不满足多服务维度要求。

---

### 3.6 测试覆盖 ✅

| 测试文件 | 测试编号 | 用例数 | 状态 |
|---------|---------|--------|------|
| `test_service_health_aggregator.py` | T-OBS-HEALTH-01~06 | 6 | ✅ 全覆盖 |
| `test_session_query.py` | T-OBS-SESS-01~06 | 6 | ✅ 全覆盖 |
| `test_trace_node_builder.py` | T-OBS-TRACE-01~08 | 8 | ✅ 全覆盖 |
| `test_metrics_timeseries.py` | T-OBS-TS-01~06 | 6 | ✅ 全覆盖 |

**发现 [WARN-TEST-001]**：`test_trace_node_builder.py` 中 T-OBS-TRACE-03 测试的是"parent_node_id 正确反映执行树结构"，但断言仅验证"第一个节点无 parent，后续节点有 parent"，未验证并行分支场景（如两个节点共享同一 parent）。测试通过不代表并行分支逻辑正确。

**发现 [WARN-TEST-002]**：`obs_routes.py` 的 4 个端点**无集成测试**，Route 层与 Service 层的重复实现差异未被测试覆盖。

---

## 四、发现项汇总

### 🔴 高风险（需立即修复）

| 编号 | 位置 | 描述 | 影响 |
|------|------|------|------|
| BUG-BE-001 | `obs_routes.py` Trace 降级路径 | 访问不存在的 `ledger.governor_log` 属性 | 降级路径 500 错误 |
| BUG-FE-001 | `Overview.jsx` 汇总统计行 | 读取不存在的 `data.summary` 字段 | 总览页统计卡片始终显示 `—` |
| WARN-FE-006 | `api/obs.js` Trace API 调用 | 路径 `/obs/trace/{id}/nodes` 与后端路由 `/obs/trace/{id}` 不匹配 | 切换真实 API 后 Trace 页 404 |

### 🟡 中风险（建议近期修复）

| 编号 | 位置 | 描述 | 影响 |
|------|------|------|------|
| BUG-BE-002 | `trace_node_builder.py` 全量拉取 | 无时间范围约束，全量拉取审计日志后内存过滤 | 数据量大时 OOM / 超时 |
| WARN-BE-001~003 | `obs_routes.py` | Route 层与 Service 层三组平行实现，Route 层功能更弱 | 维护成本高，功能不一致 |
| WARN-FE-004 | `Overview.jsx` + `ServiceCard.jsx` | ServiceCard 接收字段名与 API 响应字段名不匹配 | 服务卡片内容无法正确渲染 |
| WARN-FE-005 | `Sessions.jsx` 翻页逻辑 | 闭包陈旧值问题，翻页请求页码可能不正确 | 翻页功能异常 |
| WARN-BE-005 | `trace_node_builder.py` parent 推断 | 线性链不支持并行分支，DevSpec §29.6.1 要求支持分支/并行 | Trace 图并行节点布局错误 |

### 🟢 低风险（可在下一迭代处理）

| 编号 | 位置 | 描述 |
|------|------|------|
| WARN-FE-001 | `package.json` | Zustand 版本 5.x 与 DevSpec 约定 4.x 不一致，需更新文档 |
| WARN-FE-002 | `Sessions.jsx` + DevSpec | Trace 跳转方式与契约描述不一致，需更新 DevSpec |
| WARN-FE-003 | `useAppStore.js` | Store 缺少 `lastPolledAt` / `pollingError` 契约字段 |
| WARN-FE-007 | `Metrics.jsx` | Mock 降级无明显标注，可能误导运维 |
| WARN-BE-004 | `service_health_aggregator.py` | `to_dict()` 返回 `name` 字段，契约要求 `service_name` |
| WARN-BE-006 | `session_query.py` 时间格式化 | 使用本地时区，跨时区部署时显示错误 |
| WARN-BE-007 | `metrics_timeseries.py` 延迟分布 | 仅有 Overall 维度，缺少按服务类型分解 |
| WARN-TEST-001 | `test_trace_node_builder.py` | T-OBS-TRACE-03 未覆盖并行分支场景 |
| WARN-TEST-002 | `tests/unit/` | `obs_routes.py` 4 个端点无集成测试 |

---

## 五、修复优先级建议

```
P0（本迭代必修）：BUG-BE-001、BUG-FE-001、WARN-FE-006
P1（下次迭代）：BUG-BE-002、WARN-BE-001~003、WARN-FE-004、WARN-FE-005
P2（技术债）：其余 WARN-* 项
```

---

## 六、结论

M17/M18 整体架构设计合理，技术栈选型符合契约，后端 Service 层（`host_brain/obs/`）实现质量高，测试覆盖完整。主要问题集中在：

1. **Route 层与 Service 层脱节**：`obs_routes.py` 未复用 Service 层，导致三组平行实现，且 Route 层功能更弱；
2. **前后端字段名对齐不足**：Overview 页 `summary` 字段、ServiceCard 字段名、Trace API 路径均存在前后端不一致；
3. **全量数据加载**：`TraceNodeBuilder` 无时间范围约束，存在生产环境性能风险。
