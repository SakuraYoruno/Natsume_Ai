受众：MASTER
标题：需求可追溯性矩阵（Requirements Trace）

版本：v1.3.1

定位：以“条目式矩阵”维护 PRD（FR）→ DevSpec（章节/契约点）→ DevTasks（TASK）→ Tests（T-xx）的覆盖关系；requirements_trace.csv 可继续承载表格视图（若未维护或滞后，以本 md 为准）。

---
## R-01
- 系统级需求：单向链执行：LLM（语义内核草案）→Governor→Skills→Audit
- PRD 需求编号（FR）：FR-CORE-001, FR-AUDIT-001, FR-SKILL-001
- 来源：whitepaper 5/10/13
- DevSpec 落点：DevSpec 0/6/8.1/9
- DevTasks（TASK）：TASK-0010,TASK-0011,TASK-0012
- 验收测试（T-xx）：T-14,T-15,T-34,T-67
- 覆盖状态：TASK_READY

## R-02
- 系统级需求：统一拒绝码 reason_code 且可统计
- PRD 需求编号（FR）：FR-GOV-001~003
- 来源：whitepaper 7
- DevSpec 落点：DevSpec 4/8.1/9
- DevTasks（TASK）：TASK-0007,TASK-0008,TASK-0012
- 验收测试（T-xx）：T-14,T-16,T-17
- 覆盖状态：TASK_READY

## R-03
- 系统级需求：Intent 解析/校验/重放保护
- PRD 需求编号（FR）：FR-INTENT-001~003
- 来源：whitepaper 8/13(I7)
- DevSpec 落点：DevSpec 3
- DevTasks（TASK）：TASK-0005
- 验收测试（T-xx）：T-18~T-22
- 覆盖状态：TASK_READY

## R-04
- 系统级需求：票据化执行：approval/confirm/undo（语言不构成授权）
- PRD 需求编号（FR）：FR-TOKEN-001~003
- 来源：whitepaper 9/13(I6,I11)
- DevSpec 落点：DevSpec 5/6
- DevTasks（TASK）：TASK-0009,TASK-0010,TASK-0015
- 验收测试（T-xx）：T-34~T-38
- 覆盖状态：TASK_READY

## R-05
- 系统级需求：ClawdBot 视觉熔断：红线/置信度/审计证据链
- PRD 需求编号（FR）：FR-VISION-001~002, FR-AUDIT-002
- 来源：PRD 4.9/4.7
- DevSpec 落点：DevSpec 6.3/8.1/8.6
- DevTasks（TASK）：TASK-0022
- 验收测试（T-xx）：T-41~T-43
- 覆盖状态：TASK_READY

## R-06
- 系统级需求：SSOT：Neo4j Bond 单写者；summary 仅派生
- PRD 需求编号（FR）：FR-BOND-001~003
- 来源：PRD 4.10
- DevSpec 落点：DevSpec 8.2
- DevTasks（TASK）：TASK-0023（服务健康注册表承接）,TASK-0035,TASK-0036
- 验收测试（T-xx）：T-50~T-54,T-74~T-75,T-SSOT-01
- 覆盖状态：TASK_READY

## R-07
- 系统级需求：气闸：出站脱敏/入站隔离，不得直通执行
- PRD 需求编号（FR）：FR-AIRLOCK-001~002
- 来源：PRD 4.8
- DevSpec 落点：DevSpec 8.7
- DevTasks（TASK）：TASK-0016
- 验收测试（T-xx）：T-39,T-40
- 覆盖状态：TASK_READY

## R-08
- 系统级需求：输出结构稳定：成功/拒绝/降级都可解析
- PRD 需求编号（FR）：FR-CORE-002, FR-GOV-001
- 来源：whitepaper 4.2/13(I13)
- DevSpec 落点：DevSpec 2/9
- DevTasks（TASK）：TASK-0001,TASK-0002
- 验收测试（T-xx）：T-14,T-15
- 覆盖状态：TASK_READY

## R-09
- 系统级需求：Canon 不得当事实；引用口径固定
- PRD 需求编号（FR）：FR-CANON-001~002, FR-WCANON-001
- 来源：Theory 8 / PRD 4.12
- DevSpec 落点：DevSpec 8.4/8.5
- DevTasks（TASK）：TASK-0013,TASK-0014
- 验收测试（T-xx）：T-Canon-01~03
- 覆盖状态：TASK_READY

## R-10
- 系统级需求：静态扫描断言：旁路与单写者边界可回归
- PRD 需求编号（FR）：NFR-MAINT-001
- 来源：DevSpec 8.8
- DevSpec 落点：DevSpec 8.8
- DevTasks（TASK）：TASK-0012
- 验收测试（T-xx）：T-SSOT-01
- 覆盖状态：TASK_READY

## R-11
- 系统级需求：审计链三表最小闭环 + 可回放
- PRD 需求编号（FR）：FR-AUDIT-001~003
- 来源：whitepaper 10 / PRD 4.7
- DevSpec 落点：DevSpec 8.1/9
- DevTasks（TASK）：TASK-0011
- 验收测试（T-xx）：T-Audit-01~03
- 覆盖状态：TASK_READY

## R-12
- 系统级需求：Intent responsibility：proposal/decision/fact 分离
- PRD 需求编号（FR）：FR-AUDIT-001（及 PRD 中 intent responsibility 条目）
- 来源：whitepaper 10 / PRD
- DevSpec 落点：DevSpec audit 契约
- DevTasks（TASK）：TASK-0006,TASK-0011
- 验收测试（T-xx）：T-Intent-01,T-Intent-02
- 覆盖状态：TASK_READY

## R-13
- 系统级需求：工具平面渐进式加载：仅元数据 Top-K 暴露；WRITE 默认不可见；ToolBroker 单入口 + evidence_ref
- PRD 需求编号（FR）：FR-TOOL-001~003
- 来源：PRD 4.6（TOOL）
- DevSpec 落点：DevSpec 8.9
- DevTasks（TASK）：TASK-0017,TASK-0018
- 验收测试（T-xx）：T-Tool-01~03
- 覆盖状态：TASK_READY

## R-14
- 系统级需求：通道与外显受治理：微信 I/O 审计 + VTS 确定性映射
- PRD 需求编号（FR）：FR-CHANNEL-001, FR-AVATAR-001
- 来源：PRD 4.17
- DevSpec 落点：DevSpec 8.11
- DevTasks（TASK）：TASK-0020,TASK-0021
- 验收测试（T-xx）：T-Channel-01,T-Avatar-01
- 覆盖状态：TASK_READY

## R-15
- 系统级需求：Vault：manifest/undo/GC 审计闭环（不破坏 undo 窗口）
- PRD 需求编号（FR）：FR-LSP-001~002
- 来源：PRD 4.14
- DevSpec 落点：DevSpec 8.6
- DevTasks（TASK）：TASK-0015
- 验收测试（T-xx）：T-Vault-01~02
- 覆盖状态：TASK_READY

## R-16
- 系统级需求：MCP 受治理接入：provider 元数据同步 + READ/WRITE 分级 + 审计串联
- PRD 需求编号（FR）：FR-MCP-001
- 来源：PRD 4.18
- DevSpec 落点：DevSpec 8.10
- DevTasks（TASK）：TASK-0019
- 验收测试（T-xx）：T-MCP-01
- 覆盖状态：TASK_READY

【文件：docs/60_trace/requirements_trace.md】
【替换条目】R-17（用以下内容整体替换原 R-17）

## R-17
- 系统级需求：冷启动/依赖不可用的闭环可用性：结构稳定 + 可观测降级 + 可回放审计（不得伪执行）
- PRD 需求编号（FR）：FR-CORE-004, FR-OBS-001
- 来源：PRD 3.3 / 本次架构决策（2026-02-28）
- DevSpec 落点：DevSpec 1.2.1/2.1
- DevTasks（TASK）：TASK-0000,TASK-0001,TASK-0023
- 验收测试（T-xx）：T-LLM-01,T-14,T-15,T-67~T-71,T-Svc-01
- 覆盖状态：TASK_READY

## R-18
- 系统级需求：语音主链（ASR→Host编排→TTS）事件化、可打断、可回放
- PRD 需求编号（FR）：FR-VOICE-001~003, FR-AUDIT-001
- DevSpec 落点：DevSpec 8.11.3
- DevTasks（TASK）：TASK-0024~TASK-0027
- 验收测试（T-xx）：T-Voice-01~T-Voice-03
- 覆盖状态：TASK_READY

## R-19
- 系统级需求：Edge 兜底 LLM 默认卸载、draft-only、快速失败回退
- PRD 需求编号（FR）：FR-VOICE-004~005, FR-CORE-004
- DevSpec 落点：DevSpec 8.11.3
- DevTasks（TASK）：TASK-0028~TASK-0029
- 验收测试（T-xx）：T-Voice-04~T-Voice-05
- 覆盖状态：TASK_READY

## R-20
- 系统级需求：检索链（Embedding/Rerank/Evidence Pack）接口稳定与降级可观测
- PRD 需求编号（FR）：FR-MEM-003~005, FR-OBS-001
- DevSpec 落点：DevSpec 8.3.1
- DevTasks（TASK）：TASK-0030~TASK-0032
- 验收测试（T-xx）：T-Ret-01~T-Ret-03
- 覆盖状态：TASK_READY

## R-21
- 系统级需求：屏幕监控/OCR Gaming 门控 + 摄像头识别事件化与证据引用
- PRD 需求编号（FR）：FR-VISION-003~004, FR-MODE-001~002
- DevSpec 落点：DevSpec 8.12
- DevTasks（TASK）：TASK-0033~TASK-0034
- 验收测试（T-xx）：T-VisionGate-01,T-Vision-01~02
- 覆盖状态：TASK_READY

---

覆盖状态枚举建议：
- PRD_READY：PRD 已完备（FR + 验收已清晰），但 DevSpec 尚未给出可审阅的工程契约与测试编号。
- SPEC_READY：DevSpec 已给出工程契约与测试编号（T-xx）且可被拆解为任务；但 DevTasks 可能尚未拆齐。
- TASK_READY：DevTasks 已拆齐并引用 FR/T/章节，具备可执行验收断言口径；实现与测试落地仍可能未完成。
- TEST_READY：对应测试用例与 fixtures 已具备可回归形态（编号与断言点明确），可用于实现完成后的回归判定。
- DONE：实现已落地且在回归集上满足验收断言（以审计回放与测试报告为准）。

差距类状态（用于明确“缺在哪里”）：
- SPEC_GAP：DevSpec 仍缺关键契约点或测试编号（T-xx）口径，暂不具备可拆任务条件。
- TASK_GAP：DevSpec 已闭合，但 DevTasks 未拆或拆分不完整（缺输入/输出/验收/路径/引用）。
- TEST_GAP：任务已具备，但缺关键测试点/fixtures/日志断言，导致无法形成稳定回归。