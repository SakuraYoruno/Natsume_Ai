受众：MASTER
标题：文档-代码对齐状态报告

# 文档-代码对齐状态报告

版本：v2.1 Living
日期：2026-06-30
范围：README、架构主链、Pentest 专项文档、文档地图与架构目录。
结论：本轮 P1 对齐项已完成；仍有 dataflow、DevSpec、PRD、Soul 和理论/Trace 文档需要后续专项核验。

## 1. 本轮已对齐

| 文档 | 本轮状态 | 当前口径 |
|---|---|---|
| `README.md` | 已重写 | 运行面、入口、端口、模块规模、Monitor proxy、Pentest 事实已按代码更新 |
| `docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md` | 已重写到 v4.1 | Host/Obs/Edge/Gateway/Desktop/MateEngine、路由、Chat 主链、Persona、Tribe、Pentest、Monitor UI 已按代码更新 |
| `docs/50_project/arch/CORE_MODULES_DETAILED_REFERENCE.md` | 已重写到 v4.1 | 从空/旧内容改为模块级参考，覆盖 Host App、Surface、Memory、Runtime、Tribe、Governor、Skills、Pentest、Edge、Monitor UI |
| `host_brain/pentest/CAPABILITY.md` | 已重写到 v2.2 | ToolSpec/Executor、side-effect guard、online safety、Kali bridge、在线资产、K8s/CI/CD 当前边界已按代码更新 |
| `docs/50_project/arch/PENTEST_MODULES_ARCHITECTURE.md` | 已新增 v1.0 | 补齐 Pentest tools、agent、adversarial、swarm、online、report、data 与安全边界专项架构 |
| `docs/00_index/00_DOCMAP.md` | 已更新到 v5.3 | 当前阅读顺序和架构文档入口指向 v4.1/v2.2/v1.0 |
| `docs/50_project/arch/index.md` | 已更新到 v1.2 | 架构目录不再指向 v3.0，新增 Pentest 专项入口 |

## 2. 已核验代码事实

| 事实项 | 当前代码值 |
|---|---:|
| `host_brain/app/` | 228 Python 文件 |
| `host_brain/persona/` | 75 Python 文件 |
| `host_brain/persona/surface/` | 17 Python 文件 |
| `host_brain/pentest/` | 124 Python 文件 |
| `host_brain/pentest/agent/` | 31 Python 文件 |
| `host_brain/pentest/adversarial/` | 9 Python 文件 |
| `host_brain/pentest/swarm/` | 8 Python 文件 |
| `host_brain/pentest/online/` | 5 Python 文件 |
| `edge_client/` | 80 Python 文件 |
| `host_brain/app/monitor_ui/src/` | 458 source files |
| Tribe family specs | 23 |
| Tribe default agent descriptors | 25 |
| Pentest ToolSpec | 88 |
| Pentest executor | 92 |
| Pentest side-effect ToolSpec | 37 |
| Pentest ToolSpec 缺 executor | 0 |
| Kali wrapper class | 17 |

## 3. 已纠正的旧口径

- Host app 不直接 mount `/monitor` 静态目录；当前 `/monitor/` 由 `scripts/runtime/start_monitor_proxy.ps1` 生成的 Nginx proxy 暴露。
- `/v1/chat` 与 `/v1/edge/events` 由 `host_brain/app/app_factory.py` 直接注册，不属于 `router_registry.py` 的普通 router include。
- Pentest ToolSpec/Executor 真源位于 `host_brain/pentest/tools/tool_specs.py` 与 `host_brain/pentest/tools/tool_executors.py`。
- Pentest 不再沿用旧的工具总数口径；当前是 88 个 ToolSpec、92 个 executor。
- Phase 3 `pentest_vuln_*` 当前是 12 个 ToolSpec，不是 13 个。
- K8s audit 和 CI/CD scan 现在有 executor；K8s audit 当前返回 manual-verification findings，不应写成完整自动 kube-hunter 审计。
- Censys 有 `censys_host_lookup()` helper，但当前没有独立 `pentest_censys_lookup` ToolSpec。
- `ONLINE_SCAN_MODE` 当前只有 `offline` 和 `manual`，没有 `auto`。
- `edge_command_pusher` 不在当前 Tribe default descriptor 列表中。

## 4. 仍需后续核验

| 文档/区域 | 当前风险 | 建议优先级 |
|---|---|---|
| `docs/50_project/arch/dataflow_report.md` | 仍可能缺 Surface 主链、Memory-to-Surface、Governor、回调图等最新链路 | P2 |
| `docs/30_devspec/DevSpec.md` | Agent Runtime、Persona/Surface、反馈系统契约可能落后于代码 | P2 |
| `Soul.md` | Persona 叙事层可能未完全反映当前 self_model、inner_world、reply_plan 等实现 | P2 |
| `docs/20_prd/PRD.md` | 产品口径未在本轮逐条审计 | P3 |
| `docs/15_theory/` | 理论契约未在本轮逐条审计 | P3 |
| `docs/40_agents/AGENTS.md` | Agent 协作规范未在本轮逐条审计 | P3 |
| `docs/60_trace/requirements_trace.md` | Trace 覆盖关系未在本轮逐条审计 | P3 |

## 5. 下一轮建议

1. 先用代码路径核验 `dataflow_report.md` 的请求流、事件流和回流图。
2. 再更新 `DevSpec.md` 中与 Host route、Surface chain、Pentest approval、Monitor proxy 相关的接口/契约。
3. 最后处理 PRD、Soul、theory、Trace 的叙事和需求层口径。
