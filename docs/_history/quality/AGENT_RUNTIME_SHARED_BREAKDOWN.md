受众：CODEX
标题：`agent_runtime_shared.py` 拆分提案（OPT-10）

# `agent_runtime_shared.py` 拆分提案（OPT-10）

**状态**：草案 ｜ **日期**：2026-04-20 ｜ **目标 PR**：OPT-10-A/B/C 系列 ｜ **源文件行数**：约 2744 行

**上游规划**：[OPTIMIZATION_PLAN_20260420.md §14 / OPT-10](./OPTIMIZATION_PLAN_20260420.md)

---

## 1. 背景

`host_brain/app/agent_runtime_shared.py` 长期作为"运行时共享工具"的聚集地，
截至本次审计已达 **2744 行 / 约 106.5 KB**，并在 OPTIMIZATION_PLAN_20260420
的 `test_no_fat_shared_module`（OPT-10）硬规则下被**临时豁免**
（`SHARED_MODULE_LEGACY_EXEMPT`）。

本提案的目标是：

- **不扩大现有豁免**（硬规则已生效，新文件自动受 500 行约束）；
- 按**业务职责**将该文件切成 4 ~ 5 个小模块，每个 ≤ 500 行；
- 拆分过程中**不改变任何对外行为**，仅做"剪切 + 重命名 + re-export"；
- 完成后把 `agent_runtime_shared.py` 从豁免名单移除。

---

## 2. 顶层符号清单（审计输出）

审计 `^(def |class |async def )` 命中 34 个顶层符号，按粗略职责分组如下：

### 2.1 Trace / Audit 相关（persist & summarize）

| 符号 | 行 | 作用 |
|------|----|------|
| `_normalize_retrieval_summary_for_audit` | 174 | 把 retrieval_summary 归一化成 audit payload |
| `_build_trace_agent_envelope` | 215 | 组装 trace 事件的 agent envelope |
| `_build_trace_stage_node` | 284 | 组装 trace stage node |
| `_persist_post_process_audit` (async) | 342 | 持久化后置处理 audit |
| `_async_post_process` (async) | 2451 | 整体后置异步管道（含 audit 入口） |

### 2.2 SubAgent Gateway（出站子代理调用）

| 符号 | 行 | 作用 |
|------|----|------|
| `_SubAgentGatewayAdapter` (class) | 791 | 代理 SubAgent 调用的 adapter |
| `_build_subagent_gateway_from_runtime` | 912 | 从 runtime 构造 gateway |

### 2.3 Fast-path / 快速应答分支

| 符号 | 行 | 作用 |
|------|----|------|
| `_detect_recent_turn_query_role` | 940 | 识别"最近一轮 query role" |
| `_load_continuity_turns` | 962 | 载入连续性 turn 列表 |
| `_maybe_build_recent_turn_answer` | 988 | 基于最近轮构造 fast-path reply |
| `_looks_like_active_task_context` | 1204 | 识别"激活任务"上下文 |
| `_pick_non_repeating_variant` | 1227 | 从变体中挑非重复项 |
| `_normalize_phatic_fastpath_text` | 1240 | 规范化寒暄 fastpath 文本 |
| `_looks_like_comfort_fastpath_message` | 1247 | 识别安慰 fastpath |
| `_resolve_phatic_fastpath_variants` | 1256 | 读模板变体 |
| `_maybe_build_phatic_fastpath_reply` | 1274 | 构造寒暄 fastpath 回复 |
| `_maybe_build_task_progress_fastpath` | 1459 | 构造任务进度 fastpath |
| `_maybe_build_previous_meaning_answer` | 1666 | "上一句是什么意思"类 fastpath |

### 2.4 Persona Session / Hook

| 符号 | 行 | 作用 |
|------|----|------|
| `_resolve_persona_session_emotion` | 1101 | 计算 session 结束情绪 |
| `_run_persona_session_end_hooks` (async) | 1142 | 触发 session end hooks |
| `_infer_emotion_tag_for_tts` | 1185 | 推断 TTS emotion tag |

### 2.5 LLM 输出解析 / 委派处理

| 符号 | 行 | 作用 |
|------|----|------|
| `_extract_delegate_visible_text` | 1350 | 提取委派可见文本（OPT-3 已接入 soft_fail） |
| `_render_delegated_result_naturally` | 1362 | 自然语言渲染委派结果 |
| `_extract_delegate_from_llm_output` | 1740 | 从 LLM 输出中抽 delegate 三元组 |
| `_extract_visible_text` | 1782 | 从原始 LLM 输出抽可见文本 |
| `_should_buffer_stream_output` | 1708 | SSE 是否缓冲决策 |

### 2.6 Retrieval / Prefetch

| 符号 | 行 | 作用 |
|------|----|------|
| `_resolve_retrieval_timeout_seconds` | 1391 | 解析 retrieval 超时 |
| `_should_use_direct_chat_fast_mode` | 1401 | 是否直聊快速模式 |
| `_should_use_context_chat_fast_mode` | 1425 | 是否上下文快速模式 |
| `_resolve_direct_chat_retrieval_timeout_seconds` | 1449 | 直聊 retrieval 超时 |
| `_build_retrieval_bond_summary` | 1798 | 构造 retrieval bond summary |
| `_prefetch_retrieval_persona_inputs` (async) | 1822 | 并行预取 persona 输入 |

### 2.7 SSE 编排（协程主体）

| 符号 | 行 | 作用 |
|------|----|------|
| `_cortex_orchestrator_sse_generator_impl` (async) | 1878 | SSE 生成器实现（**最长方法**） |

### 2.8 其他 / 运行时文本

| 符号 | 行 | 作用 |
|------|----|------|
| `_load_runtime_texts` | 53 | 载入模板运行时文本 |

---

## 3. 拆分目标模块

按"变更率 + 调用拓扑 + 测试边界"三维度聚类，建议拆成 **5 个新模块**。
每个模块都 ≤ 500 行（OPT-10 硬规则）。

| 新模块 | 行数（估计） | 职责 | 承接符号 |
|--------|------------|------|---------|
| `runtime_trace_audit.py` | ~ 400 | Trace envelope / stage / post-process audit | §2.1 的 5 项 |
| `runtime_fast_paths.py` | ~ 480 | 寒暄 / 任务进度 / "上一句是什么意思" / 最近轮 fastpath | §2.3 的 11 项 + 调用相关的私有工具 |
| `runtime_retrieval_helpers.py` | ~ 260 | retrieval timeout / prefetch / bond summary | §2.6 的 6 项 |
| `runtime_llm_output_parse.py` | ~ 220 | LLM 输出解析 / delegate 抽取 / 可见文本抽取 | §2.5 的 5 项 |
| `runtime_sse_orchestrator.py` | ~ 600 | `_cortex_orchestrator_sse_generator_impl` 主体 | §2.7（单独成模块） |

**剩余留在 `agent_runtime_shared.py`（或改名 `runtime_persona_hooks.py`）**：
- `_load_runtime_texts`
- `_resolve_persona_session_emotion` / `_run_persona_session_end_hooks` / `_infer_emotion_tag_for_tts`
- `_SubAgentGatewayAdapter` + `_build_subagent_gateway_from_runtime`

**注意**：§2.7 的 `_cortex_orchestrator_sse_generator_impl` 单体 600 行超过 500 行硬线。
方案：在搬迁时**进一步拆成** `_prepare_*` / `_emit_*` / `_finalize_*` 三段，
使新模块最终约 450 ~ 480 行。

---

## 4. 拆分执行计划

分 **3 个 PR**，每个 PR 都跑全量 contract + relevant unit test。

### OPT-10-A：抽出 Trace / Audit（§2.1）

- 新建 `host_brain/app/runtime_trace_audit.py`；
- 从 `agent_runtime_shared.py` 剪切 §2.1 五个符号；
- 在原文件保留 `from host_brain.app.runtime_trace_audit import *`
  形式的 re-export，以保证**外部 import 零破坏**；
- 运行：
  - `tests/contract/test_ci_gate_contract.py::TestSharedModuleLint`
  - `tests/contract/test_prompt_context_contract.py`
  - `tests/contract/test_prompt_payload_builder_contract.py`

### OPT-10-B：抽出 Fast-paths + Retrieval + LLM 输出解析（§2.3 / §2.6 / §2.5）

- 同步新建 `runtime_fast_paths.py`、`runtime_retrieval_helpers.py`、`runtime_llm_output_parse.py`；
- 保留 re-export；
- 在此 PR 内**顺带补契约测试**：`tests/contract/test_fast_paths_contract.py`
  锁定 fastpath 模块 ≤ 500 行。

### OPT-10-C：抽出 SSE Orchestrator + 移除豁免（§2.7）

- 新建 `runtime_sse_orchestrator.py`，并在搬迁同时拆 `_cortex_orchestrator_sse_generator_impl`
  为 `_prepare_sse_context` + `_stream_sse_events` + `_finalize_sse_envelope` 三段；
- 原 `agent_runtime_shared.py` 预计此时应 ≤ 500 行；
- **从 `tests/contract/test_ci_gate_contract.py` 的 `SHARED_MODULE_LEGACY_EXEMPT` 集合移除
  `host_brain/app/agent_runtime_shared.py`**，该契约测试由此把 OPT-10 的硬规则对该文件恢复；
- 跑全量 `pytest -m "unit or contract"`。

---

## 5. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 外部 import 依赖 `agent_runtime_shared.XXX` 名字，搬走后断裂 | 每个 PR 保留 re-export（`from ... import *`），并在 OPT-10-C 后用 grep 盘点外部引用，统一迁移到新模块路径，最后再删 re-export |
| `_cortex_orchestrator_sse_generator_impl` 跨 600 行、内部局部变量多 | 先**剪切到新文件再原样跑回归**；再在下一个 PR 拆 3 段 |
| soft_fail / 观测指标 stage 名字已命名为 `agent_runtime_shared.*` | 继续保留该 stage 前缀，不随模块改名而改，以免把 dashboard 历史序列打断 |
| 并行 OPT-7 也在拆 `chat_agent.py`，可能与本提案 import 路径冲突 | OPT-10 三个 PR 依次 merge；OPT-7 在期间只改 `chat_agent.py` 自身 |

---

## 6. 完成定义（DoD）

- `host_brain/app/agent_runtime_shared.py` ≤ 500 行；
- `SHARED_MODULE_LEGACY_EXEMPT` 集合为空（或已保留的遗留条目全部 ≤ 500 行）；
- `test_no_fat_shared_module` 不再依赖豁免；
- `scripts/verify_optimization_20260420.sh` 全绿；
- 没有 import 断裂（`python -c "import host_brain.app.agent_runtime_shared"` 成功）；
- `docs/35_quality/OPTIMIZATION_PLAN_20260420.md` 的 OPT-10 状态更新为"已完成"。

---

## 7. 审计流水（供复核）

```bash
$ wc -l host_brain/app/agent_runtime_shared.py
2744

$ grep -E '^(def |class |async def )' host_brain/app/agent_runtime_shared.py | wc -l
34

$ du -h host_brain/app/agent_runtime_shared.py
106K
```

本提案不修改源码，仅作为 OPT-10 后续 3 个 PR 的**蓝图**。执行由 OPT-10-A 开始。
