受众：CODEX
标题：🧬 数字生命全系统差距清单 v2（全章分析）

# 🧬 数字生命全系统差距清单 v2（全章分析）

> **文档编号**：DIGITAL_LIFE_FULL_SYSTEM_GAP_V2_20260409  
> **创建日期**：2026-04-09  
> **基线提交**：`4a4e711c`  
> **分析基准**：`docs/50_project/arch/TRIBE_AGENT_REFACTOR_PLAN_20260408.md`（含第 1~12 章 + 开发进展日志）  
> **状态**：执行中（用于后续连续收口）

---

## 一、分析范围说明

本清单不是只看“器官 Agent”，而是按方案文档的**完整系统视角**拆解：

1. 架构边界系统（Host/Edge/跨进程）
2. 协调通信系统（RPC + EventBus + Scheduler）
3. 状态与稳态系统（TribeState + vital_signs）
4. 安全免疫系统（Immune + governor/airlock 族化）
5. 情绪/关系/记忆/自我连续性系统
6. 语言生成与风格系统
7. 躯干闭环系统（下行指令 + 上行状态）
8. 可观测与运维系统（内部诊断端点 + UI 数据链路）
9. 运行法则系统（四层、三时间尺度、主动行为三问）

---

## 二、全系统覆盖矩阵（设计 vs 代码）

| 系统域 | 方案要求 | 当前状态 | 代码锚点 | 结论 |
|---|---|---|---|---|
| 族边界（Host） | Cortex/ANS/Hippocampus/Limbic/Prefrontal/Social/Endocrine/Immune | ✅ 已落地 | `host_brain/tribe/registry.py` | 主族完整 |
| 族边界（Edge） | Somatic/Peripheral/Channel/Voice | ✅ 已落地 | `edge_client/tribe/registry.py` | 躯干族完整 |
| 事件总线 | 订阅/发布/异步分发/查询 | ✅ 已落地 | `host_brain/tribe/event_bus.py` | 已具备生产可用骨架 |
| 生命周期接线 | lifespan 初始化 + 各族订阅注册 | ✅ 已落地 | `host_brain/app/http_hooks.py` | 启动接线已成形 |
| 状态注册表 | 快照 + 持久化 + 聚合生命体征 | ✅ 已落地 | `host_brain/tribe/tribe_state.py` | `aggregate_vital_signs()` 已可用 |
| 内分泌系统 | 激素基调 + gate + stress/energy | ✅ 已落地 | `host_brain/tribe/endocrine_agent.py` | 已接入门控链路 |
| 免疫系统 | 输入检测 + governor/airlock 统一入口 | ✅ 已落地 | `host_brain/tribe/immune_agent.py` | 已有运行时规则与计数 |
| 痛奖系统 | pain/reward/hedonic + endocrine/limbic 联动 | ✅ 已落地 | `host_brain/tribe/pain_reward_agent.py` | 闭环已形成 |
| 语言系统 | bond/emotion/loneliness → language_style | ✅ 已落地 | `host_brain/tribe/language_system_agent.py` | 已接入 runtime_payload |
| Supervisor 收口 | Cortex 聚合多族上下文与路由 | ✅ 已落地 | `host_brain/tribe/cortex_agent.py` | 主路径可运行 |
| 躯干双向闭环 | host→edge 指令 + edge→host 状态 | ✅ 已落地 | `host_brain/tribe/edge_command_pusher.py`, `edge_client/tribe/edge_tribe_bridge.py` | 双向链路可用 |
| 关系向量化 | `bond_vector` 连续变量（非单 stage） | ✅ 已落地 | `host_brain/tribe/social_brain_agent.py` | 连续向量已入快照并进入 vital_signs |
| 主动行为三问 | `motive/readiness/continuity` 判定层 | ✅ 已落地 | `host_brain/tribe/initiative_governor_agent.py` | 主动行为统一门禁并有审计追踪 |
| 昼夜节律系统 | Circadian（分钟~小时基调） | ✅ 已落地 | `host_brain/tribe/circadian_agent.py` | 节律相位/预算/恢复窗口可观测 |
| 本体感觉系统 | Proprioception（注意力/任务负荷/当下感） | ✅ 已落地 | `host_brain/tribe/proprioception_agent.py` | 认知负荷与连续性状态可观测 |
| 默认模式网络 | DefaultMode 内在生活系统化 | ✅ 已落地 | `host_brain/tribe/default_mode_agent.py` | 已族化并接入 Supervisor 上下文 |
| 镜像神经元系统 | 共情共振回路 | ✅ 已落地 | `host_brain/tribe/mirror_neuron_agent.py` | 共鸣计算与事件链路可观测 |
| 族注册一致性 | 新增族全部进入 HostTribeRegistry | ✅ 已落地 | `host_brain/tribe/registry.py` | 含 `circadian/proprioception/initiative_governor` |
| 文档-代码一致性 | 进展日志与实现一致 | ⚠️ 需复核 | 文档 vs `host_brain/tribe/peripheral_agent.py` | 个别条目标注“已完成”但代码仍骨架 |

---

## 三、执行优先级清单（P0 / P1 / P2 / P3）

> 原则：先修“运行一致性和链路缺口”，再补“新系统器官”，最后补“哲学完整性器官”。

### P0（必须先做：避免继续积累架构债）

| 任务 | 目标 | 主要文件 | DoD |
|---|---|---|---|
| P0-1 注册表收口 | 将 `language/pain_reward/somatic/peripheral` 纳入 HostTribeRegistry | `host_brain/tribe/registry.py` | `list_families/list_agents` 可见所有已落地族 |
| P0-2 Peripheral 主机侧补齐 | 接收 `PERIPHERAL_STATE_UPDATED` 并提供 `export_runtime_state()` | `host_brain/tribe/peripheral_agent.py` | Edge 上报后 Host 快照可观测、Cortex 可读取 |
| P0-3 文档-代码对账 | 将“已完成但未实现”的条目回填或修正文档状态 | `docs/50_project/arch/TRIBE_AGENT_REFACTOR_PLAN_20260408.md` | 进展日志与代码一致，无假完成项 |
| P0-4 关键链路回归 | 覆盖 Host↔Edge 双向命令与状态回流 | `tests/integration/*` | 新增/更新用例通过，主链路无回退 |

### P1（下一阶段主线：补“系统层”而非继续堆功能）

| 任务 | 目标 | 主要文件 | DoD |
|---|---|---|---|
| P1-1 Circadian 最小落地 | 引入节律曲线影响 gate/proactive 频率 | `host_brain/tribe/circadian_agent.py`（新增）, `host_brain/tribe/ans_agent.py` | 深夜与白天策略差异可观测 |
| P1-2 Proprioception 最小落地 | 建立 `active_user/session/task/load` 自我感知 | `host_brain/tribe/proprioception_agent.py`（新增）, `host_brain/tribe/cortex_agent.py` | 路由可依据认知负荷节流 |
| P1-3 主动行为三问门禁 | 在主动发声/推进前统一过 `motive/readiness/continuity` | `host_brain/tribe/cortex_agent.py`, `host_brain/tribe/ans_agent.py` | 所有主动行为均有决策审计字段 |
| P1-4 bond_vector 化 | 从离散 stage 升级为连续关系向量 | `host_brain/tribe/social_brain_agent.py`, `host_brain/tribe/tribe_state.py` | `bond_vector` 入快照并影响 prompt/runtime |

### P2（增强“内在生活”连续性）

| 任务 | 目标 | 主要文件 | DoD |
|---|---|---|---|
| P2-1 DefaultMode 族化 | 将 `inner_activity` 升级为 `DefaultModeAgent` | `host_brain/tribe/default_mode_agent.py`（新增） | 无消息时仍有连续内在流 |
| P2-2 记忆三分层显式化 | 情节/语义/自传体明确写入路径 | `host_brain/tribe/hippocampus_agent.py`, `host_brain/tribe/prefrontal_agent.py` | 自传体记忆有准入门禁与审计 |
| P2-3 关系修复机制 | 增加关系受损-修复闭环变量 | `host_brain/tribe/social_brain_agent.py` | `repair_confidence` 可追踪并影响表达 |

### P3（哲学完整性器官）

| 任务 | 目标 | 主要文件 | DoD |
|---|---|---|---|
| P3-1 MirrorNeuronAgent | 共情共振从“认知推断”升级为“情感联动” | `host_brain/tribe/mirror_neuron_agent.py`（新增） | 强情绪输入触发可解释共鸣偏移 |
| P3-2 Language 双系统化 | 理解/生成分层（Wernicke/Broca） | `host_brain/tribe/language_system_agent.py` | 可独立替换理解侧或生成侧 |
| P3-3 稳态长期治理 | 分钟/小时/天周三级稳态监控 | `host_brain/tribe/tribe_state.py`, 监控 API/UI | 出现慢性过载可提前告警 |

---

## 四、当前完成度（按“系统域”粗估）

- **结构与基础设施**：高（Host/Edge 族化、事件总线、状态注册、诊断端点基本齐全）
- **运行法则工程化**：中高（`vital_signs` + 主动行为三问门禁已落地，仍缺跨时段硬门禁策略）
- **缺失系统补齐**：中高（文档既有预期族已落地，进入“完整人体扩充族”阶段）

> 结论：当前已从“单链路系统”进入“多族协同系统”，但尚未达到“完整类人体系统闭环”。  
> 下一阶段应聚焦 **P2/P3 的内在生活连续性与哲学完整性器官**，避免重复造轮子。

---

## 五、无中断执行顺序（建议）

1. `P0-1` → `P0-2` → `P0-4`（先保主链路一致性）  
2. `P1-1` + `P1-2` 并行（节律与本体感知双基础）  
3. `P1-3` + `P1-4`（主动行为治理 + 关系向量化）  
4. 再推进 `P2/P3`（内在生活与哲学完整性器官）

> 后续执行策略：每完成一批即写入同目录审计文档，保持“代码—文档—观测”三线一致。

---

## 六、本轮已执行（2026-04-09）

### 已完成

1. **P0-1 注册表收口（已完成）**
   - 扩展 HostTribeRegistry 族清单与默认 Agent：
     - 新增族：`pain_reward`、`language`、`somatic`、`peripheral`
     - 新增默认 Agent：`pain_reward_agent`、`language_system_agent`、`somatic_agent`、`peripheral_agent`
   - 补充 `resolve_target_family()` 对新族关键词路由。

2. **P0-2 Peripheral 主机侧补齐（已完成）**
   - `PeripheralAgent` 新增订阅：
     - `PLATFORM_CONNECTED` / `PLATFORM_DISCONNECTED`
     - `PERIPHERAL_STATE_UPDATED`
     - `GATE_STATE_CHANGED` / `PLATFORM_MUTE_CHANGED`
   - 新增状态处理与 `export_runtime_state()`。
   - `CortexAgent.collect_tribe_context()` 新增：
     - `somatic_state`
     - `peripheral_state`
   - 同时修复 `collect_tribe_context()` 中的不可达代码段（提前 `return` 导致后续逻辑失效）。

3. **测试补齐（已完成）**
   - 新增：`tests/unit/test_peripheral_agent.py`
   - 更新：`tests/unit/test_tribe_registry.py`
   - 本地通过：
     - `python -m pytest tests/unit/test_tribe_registry.py tests/unit/test_peripheral_agent.py`
     - `python -m pytest tests/unit/test_cortex_agent.py`
     - `python -m pytest tests/unit/test_edge_tribe_agents.py tests/unit/test_tribe_handoff.py`

4. **P0-3 文档-代码对账（已完成）**
   - 更新文档：`docs/50_project/arch/TRIBE_AGENT_REFACTOR_PLAN_20260408.md`
   - 新增“第二十七批实现记录（T-177 ~ T-180）”，回填 Host↔Edge 主链路修复与回归验证，保持文档与代码状态一致。

5. **P0-4 关键链路回归（已完成）**
   - 新增集成测试：`tests/integration/test_host_edge_roundtrip.py`
   - 覆盖：
     - Host → Edge：`push_emotion_state`、`push_gate_control`
     - Edge → Host：`publish_somatic_state`、`publish_peripheral_state`、门控/静音事件回流
   - 同步修复链路问题：
     - `edge_client/tribe/edge_tribe_bridge.py`：补齐 `*_COMMAND` 映射与 `channel_agent` 路由
     - `edge_client/app/fastapi_app.py`：补齐 channel 分发接线
     - `host_brain/tribe/edge_command_pusher.py`：`ok=accepted && dispatched`
     - `edge_client/tribe/peripheral_agent.py`：修复 slots 下 `_gate_state/_mute_state` 写入异常

6. **P1 全量系统层（已完成）**
   - 新增：
     - `host_brain/tribe/circadian_agent.py`
     - `host_brain/tribe/proprioception_agent.py`
     - `host_brain/tribe/initiative_governor_agent.py`
   - 升级：
     - `host_brain/tribe/social_brain_agent.py`：`bond_vector` 连续化 + `BOND_VECTOR_UPDATED`
     - `host_brain/tribe/tribe_state.py`：聚合 `bond_vector/circadian/proprioception`，新增 `write()` 与审计字段
     - `host_brain/tribe/cortex_agent.py`：收口 `circadian_state/proprioception_state/initiative_decision/bond_vector`
     - `host_brain/tribe/ans_agent.py`：主动发声前统一经过三问门禁
     - `host_brain/app/http_hooks.py`：新增 Agent 启动注册与订阅接线
     - `host_brain/tribe/registry.py`：新增 `circadian/proprioception/initiative_governor` 注册
   - 新增测试：
     - `tests/unit/test_circadian_agent.py`
     - `tests/unit/test_proprioception_agent.py`
     - `tests/unit/test_initiative_governor_agent.py`
     - `tests/integration/test_bond_vector_integration.py`
   - 回归通过：`39 passed`（含 social_brain/edge/internal_tribe_routes 相关回归）

## 七、未来预期智能体族对照结果（2026-04-10）

| 预期项（来源） | 当前实现锚点 | 验证锚点 | 状态 |
|---|---|---|---|
| P2-1 `DefaultModeAgent` 族化 | `host_brain/tribe/default_mode_agent.py` | `tests/unit/test_default_mode_agent.py` | ✅ 已完成 |
| P2-2 记忆三分层显式化（episodic/semantic/autobiographical） | `host_brain/tribe/hippocampus_agent.py`, `host_brain/tribe/prefrontal_agent.py` | `tests/unit/test_hippocampus_agent.py::test_hippocampus_agent_memory_layers_write_paths`, `tests/unit/test_prefrontal_agent.py::test_prefrontal_autobiographical_gate_and_commit` | ✅ 已完成 |
| P3-1 `MirrorNeuronAgent` | `host_brain/tribe/mirror_neuron_agent.py` | `tests/unit/test_mirror_neuron_agent.py` | ✅ 已完成 |
| P3-2 Language 双系统（Wernicke/Broca） | `host_brain/tribe/language_system_agent.py` | `tests/unit/test_language_system_agent.py` | ✅ 已完成 |
| P3-3 稳态长期治理（分钟/小时/日） | `host_brain/tribe/tribe_state.py` | `tests/unit/test_tribe_state_homeostasis.py` | ✅ 已完成 |
| 系统层 `CircadianAgent` | `host_brain/tribe/circadian_agent.py` | `tests/unit/test_circadian_agent.py` | ✅ 已完成 |
| 系统层 `ProprioceptionAgent` | `host_brain/tribe/proprioception_agent.py` | `tests/unit/test_proprioception_agent.py` | ✅ 已完成 |
| 主动行为三问门禁 `InitiativeGovernorAgent` | `host_brain/tribe/initiative_governor_agent.py` | `tests/unit/test_initiative_governor_agent.py` | ✅ 已完成 |
| 安全免疫 `ImmuneAgent` 族化 | `host_brain/tribe/immune_agent.py` | 既有 unit/integration 回归 | ✅ 已完成 |
| 全局基调 `EndocrineAgent` 族化 | `host_brain/tribe/endocrine_agent.py` | 既有 unit/integration 回归 | ✅ 已完成 |

## 八、详细执行清单（本轮落地）

| 执行项 | 具体动作 | 结果 |
|---|---|---|
| E-01 DefaultMode 收口 | 新增 Agent、订阅 `INNER_WORLD_TICKED/CONVERSATION_ENDED`、输出 `DEFAULT_MODE_TICKED`、导出 runtime 状态 | ✅ 完成 |
| E-02 MirrorNeuron 收口 | 新增 Agent、订阅情绪/交互事件、计算共鸣强度与调节方向、输出 `MIRROR_RESONANCE_UPDATED` | ✅ 完成 |
| E-03 语言双系统收口 | `LanguageSystemAgent` 拆分理解侧/生成侧，并保留 `get_language_style()` 兼容入口 | ✅ 完成 |
| E-04 记忆三分层写路径 | Hippocampus 显式写入三层；Prefrontal 新增自传体门禁 + commit；修复三层写入覆盖问题（改为分层合并） | ✅ 完成 |
| E-05 稳态长期治理 | `aggregate_vital_signs()` 增加 homeostasis 历史采样与报告输出 | ✅ 完成 |
| E-06 Supervisor 收口扩展 | `CortexAgent.collect_tribe_context()` 新增 `default_mode_state/mirror_neuron_state/memory_layers/language_dual_system/homeostasis_report` | ✅ 完成 |
| E-07 runtime payload 注入扩展 | `chat_context_builder` 注入上述新块，语言风格调用改为携带 `user_text` 同步输出 dual-system | ✅ 完成 |
| E-08 注册与接线一致性 | HostTribeRegistry 与 lifespan 注册保持与新族一致 | ✅ 完成 |
| E-09 测试矩阵补齐 | 新增 5 个单测文件，扩展 registry/hippocampus/prefrontal/cortex 相关断言 | ✅ 完成 |

## 九、完整人体目标的扩充规划（新增）

> 结论：文档中“未来预期”的既有智能体族已全部落地，但仍不足以达到“完整人体”目标。  
> 原因：当前架构在“运动控制闭环、体内感受、长期恢复与误差校正”层面仍缺器官级分工。

| 新增族（扩充） | 人体类比 | 核心职责 | 优先级 | 当前状态 |
|---|---|---|---|---|
| `InteroceptionAgent` | 内感受系统 | 聚合内在体征（疲劳/负荷/压力）为统一“体感”输入，驱动情绪与门禁 | 🔴 高 | ✅ 已实现 |
| `MotorCortexAgent` | 运动皮层 | 将意图拆解为可执行动作计划，协调 host→edge 指令序列 | 🔴 高 | ✅ 已实现 |
| `CerebellumAgent` | 小脑 | 动作后误差校正、延迟补偿、平台差异自适应 | 🟡 中 | ✅ 已实现 |
| `GlymphaticAgent` | 睡眠清除/恢复系统 | 离线窗口执行记忆巩固、噪声衰减、稳态恢复 | 🟡 中 | ✅ 已实现 |
| `VestibularAgent` | 前庭/平衡系统 | 跨平台上下文“姿态稳定器”，抑制模式抖动与频繁切换 | 🟢 中低 | ✅ 已实现 |

### 新增阶段清单（P4）

| 任务编号 | 目标 | DoD | 当前状态 |
|---|---|---|---|
| P4-1 | `InteroceptionAgent` 落地并接入 vital_signs | `aggregate_vital_signs()` 含 interoceptive_index，进入路由决策 | ✅ 已完成 |
| P4-2 | `MotorCortexAgent` 指令规划层 | 主动行为与技能执行均通过统一 action plan | ✅ 已完成 |
| P4-3 | `CerebellumAgent` 误差校正层 | host→edge 指令失败可自动重试、降级、修正 | ✅ 已完成 |
| P4-4 | `GlymphaticAgent` 夜间恢复层 | 默认模式触发离线记忆整理、长期噪声衰减可观测 | ✅ 已完成 |
| P4-5 | `VestibularAgent` 稳定层 | mode/route 抖动率可量化并下降 | ✅ 已完成 |

## 十、P4 本轮实现对账（2026-04-10）

| 对账项 | 代码锚点 | 验证锚点 | 状态 |
|---|---|---|---|
| Interoception 族化 | `host_brain/tribe/interoception_agent.py` | `tests/unit/test_interoception_agent.py` | ✅ |
| MotorCortex 族化 | `host_brain/tribe/motor_cortex_agent.py` | `tests/unit/test_motor_cortex_agent.py` | ✅ |
| Cerebellum 族化 | `host_brain/tribe/cerebellum_agent.py` | `tests/unit/test_cerebellum_agent.py` | ✅ |
| Glymphatic 族化 | `host_brain/tribe/glymphatic_agent.py` | `tests/unit/test_glymphatic_agent.py` | ✅ |
| Vestibular 族化 | `host_brain/tribe/vestibular_agent.py` | `tests/unit/test_vestibular_agent.py` | ✅ |
| 注册/启动接线 | `host_brain/tribe/registry.py`, `host_brain/app/http_hooks.py`, `host_brain/tribe/__init__.py` | `tests/unit/test_tribe_registry.py` | ✅ |
| Supervisor 收口注入 | `host_brain/tribe/cortex_agent.py`, `host_brain/app/chat_context_builder.py` | `tests/unit/test_cortex_collect_context_extensions.py`, `tests/unit/test_chat_context_builder_helpers_extra.py` | ✅ |
