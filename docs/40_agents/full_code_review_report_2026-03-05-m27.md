# M27 代码审查报告

**里程碑**：M27 — 情感链路端到端连通（P0 基础设施修复）
**日期**：2026-03-05
**审查人**：AI CodeBuddy
**状态**：✅ PASSED

---

## 一、里程碑概述

M27 修复了情感信息在全链路中的 11 个 P0 级断路点（OPT-19/20/22/25/26/27/31/32/37/38/43），使情感状态能从感知层流通到决策层、执行层、记忆层，形成完整的情感闭环。共完成 12 项子任务（TASK-0232~0243，M27-A~L）。

---

## 二、测试结果

| 指标 | 数值 |
|------|------|
| 全量测试通过 | **884 passed** |
| 全量测试失败 | **0 failed** |
| M27 新增测试 | **35 个** |
| M27 测试通过率 | **100%** |

---

## 三、子任务审查

### M27-A：GateSnapshot 增加情感字段（OPT-19）
- **文件**：`host_brain/governor/rules.py`
- **实现**：`GateSnapshot` dataclass 新增 `mood_state: str = "steady"`、`emotion_tag: str = "NEUTRAL"`、`emotion_intensity: float = 0.5` 三个情感字段
- **评审**：✅ 字段有默认值，向后兼容，frozen=True 保持不变性

### M27-B：extract_emotion_tag 传入 mood_state（OPT-20）
- **文件**：`host_brain/core/qa.py`
- **实现**：`extract_emotion_tag()` 新增 `mood_state: str | None = None` 参数，传入时启用 `MOOD_EMOTION_CONSTRAINTS` 约束校验
- **评审**：✅ 参数可选，向后兼容；`tired` 状态下 `HAPPY` 被约束为 `CALM` 系列，逻辑正确

### M27-C：build_governance_snapshot 传递 mood_state 给 GateSnapshot（OPT-22）
- **文件**：`host_brain/core/metabolism.py`
- **实现**：`build_governance_snapshot()` 从 `HardState.mood_state` 提取情感字段，注入返回 dict 的 `mood_state/emotion_tag/emotion_intensity` 字段
- **评审**：✅ 情感字段透传链路打通，`overrides` 优先级正确

### M27-D：EpisodicRecord 增加情感标注字段（OPT-25）
- **文件**：`host_brain/memory/episodic.py`
- **实现**：`EpisodicRecord` 新增 `emotion_tag: str = "NEUTRAL"`、`emotion_valence: float = 0.0`、`emotion_arousal: float = 0.3`；`to_dict()` 包含新字段；`recall()` 支持 `emotion_filter` 过滤
- **评审**：✅ 默认值安全，`emotion_valence` 超出 [-1.0, 1.0] 时被截断，旧记录兼容

### M27-E：BondDynamicsWriter.apply_update 增加情感字段（OPT-26）
- **文件**：`host_brain/bond/bond_dynamics.py`
- **实现**：`apply_update()` 新增 `emotion_tag: str = "NEUTRAL"`、`emotion_intensity: float = 0.5` 参数；`emotion_intensity > 0.7` 的互动通过 `BondStageEngine.apply_event()` 传递加权
- **评审**：✅ 情感强度加权逻辑正确，默认值安全，向后兼容

### M27-F：derive_bond_summary 修复 stage/trend 字段读取（OPT-27）
- **文件**：`host_brain/bond/summarizer.py`
- **实现**：`derive_bond_summary()` 新增 `bond_score_state` 参数，优先从 `BondScoreState.current_stage` 和 `BondScoreState.trend()` 读取，修复从 `event["stage"]` 读取 `"S_BOND_UPDATE"` 的 Bug
- **评审**：✅ Bug 修复正确，向后兼容（`bond_score_state=None` 时降级为旧版行为）

### M27-G：RecallFusionServiceContract 增加情感维度路由（OPT-31）
- **文件**：`host_brain/retrieval/recall_fusion.py`
- **实现**：`recall()` 新增 `emotion_filter: str | None = None` 参数；非空时将匹配情感标签的候选项排在前列，不匹配的降级排序
- **评审**：✅ 情感路由逻辑正确，无匹配时不返回空列表（降级为普通语义召回），向后兼容

### M27-H：IntentDecisionEngine 的 governance_snapshot 携带情感状态（OPT-32）
- **文件**：`host_brain/governor/decision.py`
- **实现**：`evaluate_intent()` 读取 `governance_snapshot.mood_state`，`critical` 状态下拒绝 `TOOL_CALL`/`SKILL_EXEC` 类意图，返回 `reason_code="EMOTION_GATE_BLOCKED"`
- **评审**：✅ 情感门控逻辑正确，`mood_state` 缺失时不触发门控，向后兼容

### M27-I：SkillLoader.execute 传递情感状态（OPT-37）
- **文件**：`host_brain/skills/loader.py`
- **实现**：`execute()` 中构建 `exec_context` 时注入 `emotion_tag/mood_state` 默认值（若 context 未提供）
- **评审**：✅ 注入逻辑正确，不覆盖已有字段，向后兼容

### M27-J：ToolBroker.execute 传递情感状态（OPT-38）
- **文件**：`host_brain/tools/broker.py`
- **实现**：`execute()` 中构建 `exec_context` 时注入 `emotion_tag/mood_state` 默认值（若 context 未提供）
- **评审**：✅ 与 M27-I 实现一致，向后兼容

### M27-K：NatsumeScheduler._build_system_intent 携带情感状态（OPT-43）
- **文件**：`host_brain/scheduler/scheduler.py`
- **实现**：`_build_system_intent()` 新增 `emotion_tag: str = "NEUTRAL"`、`mood_state: str = "steady"` 参数，注入调度意图 dict
- **评审**：✅ 情感字段正确注入，默认值安全，调度意图结构完整

### M27-L：M27 发布清单
- **文件**：`docs/50_release/m27_release.json`
- **评审**：✅ 发布清单完整，包含所有 12 项子任务

---

## 四、风险与注意事项

| 风险 | 级别 | 说明 |
|------|------|------|
| `derive_bond_summary()` 旧版行为（从 event 读取）仍保留 | 低 | `bond_score_state=None` 时降级为旧版，兼容现有调用方 |
| `emotion_intensity > 0.7` 加权阈值硬编码 | 低 | 阈值在 `BondStageEngine.apply_event()` 中处理，可配置化 |
| `EMOTION_GATE_BLOCKED` 仅阻断 `TOOL_CALL`/`SKILL_EXEC` | 低 | `NOOP`/`CHAT` 类意图不受影响，符合设计意图 |

---

## 五、总结

M27 里程碑全部 12 项子任务均已完成，884 个测试全部通过（0 失败）。情感链路 P0 断路点全部修复，情感状态现在能从感知层（ASR/Camera）→ 记忆层（EpisodicRecord）→ 决策层（GateSnapshot/IntentDecisionEngine）→ 执行层（SkillLoader/ToolBroker/Scheduler）完整流通，形成情感闭环。
