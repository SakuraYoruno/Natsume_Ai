# 数字生命多智能体族 P0 执行清单（2026-04-08）

- 关联蓝图：`docs/50_project/arch/DIGITAL_LIFE_MULTI_AGENT_BLUEPRINT_V1_20260408.md`
- 目标：在不打断现网验证的前提下，完成协议地基、写权限约束、链路追踪补全。
- 范围：P0 只做“地基统一”，不做大规模业务重写。

---

## P0 完成定义（DoD）

满足以下四项即算 P0 完成：

1. 有统一 `AgentEnvelope` 与 `ContextPack` 结构并被主链路真实使用。
2. `PersonaCore` 单写权限已由 `StateArbiter` 强约束。
3. Trace 页可展示主 DAG + subagent 子图。
4. 语义兜底只保留边界型最小兜底，不再改题意。

---

## 当前进展（2026-04-08）

- ✅ `T01` 协议模型已落地：`AgentEnvelope` / `ContextPack` 已接入主链路。
- ✅ `T02` 写权限已落地：`StateWriteArbiter` 生效，非授权写入返回 `DENY_WRITE_SCOPE`。
- ✅ `T03~T05` 主链路与可观测已接入：runtime/trace/scheduler history 已补字段并在 WebUI 可见。
- ✅ `T06~T07` 已收口：去改题意兜底、内心活动链路输出补齐可观测元信息。
- ✅ `T08` 已完成：`scripts/runtime/verify_multi_agent_p0.ps1` 全量验收通过（测试 + runtime + scheduler + trace）。

---

## 任务清单（按执行顺序）

## P0-T01：定义统一协议模型

- 目标：定义 `AgentEnvelope` / `ContextPack` 数据结构与 schema 版本。
- 建议文件：
  - `host_brain/app/schemas/agent_envelope.py`（新）
  - `host_brain/app/schemas/context_pack.py`（新）
- 交付：
  - Pydantic 模型 + `schema_version` 字段
  - 序列化/反序列化单测
- 验收：
  - `pytest -q tests/unit/*schema*`

## P0-T02：引入 StateArbiter 写权限策略

- 目标：对状态写入做 agent 粒度授权。
- 建议文件：
  - `host_brain/app/state_arbiter.py`（新增或扩展）
  - `host_brain/app/state_arbiter_queries.py`（扩展）
- 规则：
  - `SoulState` 仅 `persona_core` 可写
  - 非授权写入返回 `DENY_WRITE_SCOPE`
- 验收：
  - 非授权写 `SoulState` 单测失败（预期）
  - 授权写通过

## P0-T03：ContextPack Builder 落地

- 目标：从分散状态注入迁移到统一 `ContextPack`。
- 建议文件：
  - `host_brain/app/chat_context_builder.py`
  - `host_brain/app/top_agent_prompt.py`
- 交付：
  - 生成 `ContextPack` 并传入主链路
  - 兼容旧字段（过渡期）
- 验收：
  - 关键路径无回归，旧调用不报错

## P0-T04：主链路节点 Envelope 化

- 目标：`planner/rag/motive/policy/dispatch/chat` 统一输出 envelope。
- 建议文件：
  - `host_brain/app/top_agent.py`
  - `host_brain/app/graph/*`
- 交付：
  - 节点输出统一封装
  - 失败态 `status/reason/fallback_used` 标准化
- 验收：
  - `/obs/runtime-snapshot` 可见节点化结果

## P0-T05：Subagent 子图 Trace 补全

- 目标：前端可查看 subagent 内部节点图，而非只显示聚合结果。
- 建议文件：
  - `host_brain/app/obs_routes_core.py`
  - `host_brain/app/monitor_ui/src/components/*Trace*`
- 交付：
  - `node_id/parent_node_id/agent_family/degrade_code/output_digest`
  - 展开式子图展示
- 验收：
  - WebUI 可从主节点进入 subgraph 详情

## P0-T06：最小兜底收口（去改题意）

- 目标：仅保留防空/防越界/防系统话兜底。
- 建议文件：
  - `host_brain/app/chat_agent.py`
  - `host_brain/app/prompt_builder.py`
  - `host_brain/persona/*`（与语义改写相关处）
- 交付：
  - 清单化删除“改题意兜底”
  - 保留边界型兜底并可观测
- 验收：
  - 典型输入不再被兜底改写题意

## P0-T07：InnerActivityAgent 对齐协议

- 目标：当前独立内心活动 agent 输出也使用 envelope。
- 建议文件：
  - `host_brain/persona/inner_activity_agent.py`
  - `host_brain/persona/inner_world.py`
- 交付：
  - 输出包含 `source/fallback_reason/context_digest/schema_version`
- 验收：
  - runtime-snapshot 中可区分 llm/fallback 原因

## P0-T08：回归与验收脚本

- 目标：固定 P0 验收脚本，避免“感觉完成”。
- 建议文件：
  - `scripts/runtime/verify_multi_agent_p0.ps1`（新）
  - `docs/35_quality/audits/`（补报告）
- 交付：
  - 一键验证：健康、trace 字段、写权限、兜底策略
- 验收：
  - 脚本返回 `PASS/FAIL` + 失败原因
- 执行示例：
  - `powershell -ExecutionPolicy Bypass -File scripts/runtime/verify_multi_agent_p0.ps1 -SkipRuntime`
  - `powershell -ExecutionPolicy Bypass -File scripts/runtime/verify_multi_agent_p0.ps1`

---

## 执行节奏建议（默认）

- Wave A（T01~T03）：协议与状态地基
- Wave B（T04~T05）：链路与可观测
- Wave C（T06~T08）：兜底收口 + 验收固化

---

## 风险控制

1. 所有新能力加 feature flag：
   - `HOST_MULTI_AGENT_V1_ENABLED`
   - `HOST_TRACE_SUBGRAPH_ENABLED`
2. 逐步灰度：
   - 默认灰度到开发态会话，不直接全量。
3. 保留旧链路回退：
   - 任意阶段可快速切换到旧路径。

---

## 本文档后的直接动作

1. 跑 `scripts/runtime/verify_multi_agent_p0.ps1` 固化 `T08` 验收结果
2. 在预发/生产窗口复验 `/obs/runtime-snapshot` 与 `/obs/scheduler/history` 字段完整性
3. 进入下一阶段（P1）拆族与路由统一
