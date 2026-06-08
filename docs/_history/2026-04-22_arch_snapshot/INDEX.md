受众：MASTER
标题：2026-04-22 架构快照归档说明

# 2026-04-22 架构快照

本目录归档了 2026-04-22 时点的三份核心事实文档：

| 文件 | 原路径 | 替代版本 |
|---|---|---|
| `CURRENT_CODE_ARCHITECTURE_v2.2_20260422.md` | `docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md` | 同名文件 v3.0（2026-05-30） |
| `CORE_MODULES_DETAILED_REFERENCE_v2.1_20260419.md` | `docs/50_project/arch/CORE_MODULES_DETAILED_REFERENCE.md` | 同名文件 v3.0（2026-05-30） |
| `dataflow_report_old.md` | `docs/50_project/arch/dataflow_report.md` | 同名文件 v3.0（2026-05-30） |

## 归档原因

2026-04-22 之后到 2026-05-30，仓库代码出现以下重大变化：

1. **Host Main 入口迁移**：从 `host_brain/app/fastapi_app.py` 单文件迁到 `asgi.py + app_factory.py + router_registry.py` 三件套，旧 `fastapi_app.py` 已删除（仅 obs 端保留）。
2. **聊天主链重构**：引入 `chat_routes.py` + `chat_immune_gate` 入口编排；`chat_agent.py` 拆分出 `chat_agent_preprocessor.py` / `chat_agent_postprocessor.py` / `chat_payload_builder.py`；`prompt_builder.py` 拆为 `prompt_builder_utils.py` + `node_generation_prompt.py`；`graph_config_builder.py` 拆出 `graph_config_llm_runtime.py`。
3. **新主链落地**：`SurfaceMainChain` 成为 normal chat 默认作者，旧 `prompt_builder` 大量旧链能力转为提供 facts/state/perception 的辅助通路（详见 0.1 Normal Chat 主线记忆章节）。
4. **新增子系统**：`host_brain/bio_ops/`（Codex Bio-Ops）、`host_brain/codex_runtime/`（WorkOrder-first 执行面）、`host_brain/security_immune/`（安全免疫层）、`host_brain/persona/surface_*`（surface chain 全家桶）、`host_brain/memory/life_memory*`（life memory SSOT）。
5. **Tribe family A 级升级**：23 个 family 全部完成 P0~P4 批次升级，删除灰度开关。
6. **Monitor UI 重构**：拆分高密度页面为多个独立组件，新增 BioOps / Pathology / LifeTimeline 等专题页。

## 归档时间

- 归档执行：2026-05-30
- 归档对应 git commit：`789df7ae`（branch `codex`）
- 替换文档发布版本：v3.0

## 阅读建议

- 如需查看历史"当前代码事实"作对照、做考古、追溯重构来龙去脉，可参考本目录文件。
- **不要**把本目录文件作为当前实现层任务来源；当前事实以 `docs/50_project/arch/` 下的 v3.0 文档与代码本身为准。
