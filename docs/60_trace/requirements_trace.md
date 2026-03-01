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
- DevTasks（TASK）：TASK-0010,TASK-0011,TASK-0012,TASK-0041
- 验收测试（T-xx）：T-14,T-15,T-34,T-67
- 当前进展（2026-03-01）：主链 contract + e2e 已落地（APPROVE 执行闭环、门禁拒绝、降级路径），并通过回归。
- 覆盖状态：DONE

## R-02
- 系统级需求：统一拒绝码 reason_code 且可统计
- PRD 需求编号（FR）：FR-GOV-001~003
- 来源：whitepaper 7
- DevSpec 落点：DevSpec 4/8.1/9
- DevTasks（TASK）：TASK-0007,TASK-0008,TASK-0012,TASK-0041
- 验收测试（T-xx）：T-14,T-16,T-17
- 当前进展（2026-03-01）：Governor 输出契约与拒绝码稳定性已由 contract/e2e 回归覆盖并通过。
- 覆盖状态：DONE

## R-03
- 系统级需求：Intent 解析/校验/重放保护
- PRD 需求编号（FR）：FR-INTENT-001~003
- 来源：whitepaper 8/13(I7)
- DevSpec 落点：DevSpec 3
- DevTasks（TASK）：TASK-0005,TASK-0041
- 验收测试（T-xx）：T-18~T-22
- 当前进展（2026-03-01）：T-18~T-22（宽松解析/严格校验/重放/顺序/过期）与 e2e 重放/过期场景均已通过。
- 覆盖状态：DONE

## R-04
- 系统级需求：票据化执行：approval/confirm/undo（语言不构成授权）
- PRD 需求编号（FR）：FR-TOKEN-001~003
- 来源：whitepaper 9/13(I6,I11)
- DevSpec 落点：DevSpec 5/6
- DevTasks（TASK）：TASK-0009,TASK-0010,TASK-0015,TASK-0041
- 验收测试（T-xx）：T-34~T-38
- 当前进展（2026-03-01）：Token 绑定与缺票据拒绝路径已由 contract/e2e 覆盖并通过。
- 覆盖状态：DONE

## R-05
- 系统级需求：ClawdBot 视觉熔断：红线/置信度/审计证据链
- PRD 需求编号（FR）：FR-VISION-001~002, FR-AUDIT-002
- 来源：PRD 4.9/4.7
- DevSpec 落点：DevSpec 6.3/8.1/8.6
- DevTasks（TASK）：TASK-0022
- 验收测试（T-xx）：T-41~T-43
- 覆盖状态：DONE

## R-06
- 系统级需求：SSOT：Neo4j Bond 单写者；summary 仅派生
- PRD 需求编号（FR）：FR-BOND-001~003
- 来源：PRD 4.10
- DevSpec 落点：DevSpec 8.2
- DevTasks（TASK）：TASK-0035,TASK-0036
- 验收测试（T-xx）：T-50~T-54,T-74~T-75,T-SSOT-01
- 当前进展（2026-03-01）：TASK-0035/TASK-0036 已完成，Bond 单写者、summary 派生与 context_pack 注入口径已落地，并通过 T-50~T-54、T-74~T-75、T-SSOT-01 回归。
- 覆盖状态：DONE

## R-07
- 系统级需求：气闸：出站脱敏/入站隔离，不得直通执行
- PRD 需求编号（FR）：FR-AIRLOCK-001~002
- 来源：PRD 4.8
- DevSpec 落点：DevSpec 8.7
- DevTasks（TASK）：TASK-0016,TASK-0038
- 验收测试（T-xx）：T-39,T-40
- 当前进展（2026-03-01）：AIRLOCK 出站净化/入站隔离与 gateway 绑定已完成，T-39/T-40 回归通过。
- 覆盖状态：DONE

## R-08
- 系统级需求：输出结构稳定：成功/拒绝/降级都可解析
- PRD 需求编号（FR）：FR-CORE-002, FR-GOV-001
- 来源：whitepaper 4.2/13(I13)
- DevSpec 落点：DevSpec 2/9
- DevTasks（TASK）：TASK-0001,TASK-0002,TASK-0038,TASK-0041
- 验收测试（T-xx）：T-14,T-15
- 当前进展（2026-03-01）：成功/拒绝/降级输出均保持单 JSON，可由 QA 与 e2e 降级路径验证。
- 覆盖状态：DONE

## R-09
- 系统级需求：Canon 不得当事实；引用口径固定
- PRD 需求编号（FR）：FR-CANON-001~002, FR-WCANON-001
- 来源：Theory 8 / PRD 4.12
- DevSpec 落点：DevSpec 8.4/8.5
- DevTasks（TASK）：TASK-0013,TASK-0014
- 验收测试（T-xx）：T-Canon-01~03
- 当前进展（2026-03-01）：Style/World Canon 契约、索引同步、Canon-as-fact 拦截回归通过。
- 覆盖状态：DONE

## R-10
- 系统级需求：静态扫描断言：旁路与单写者边界可回归
- PRD 需求编号（FR）：NFR-MAINT-001
- 来源：DevSpec 8.8
- DevSpec 落点：DevSpec 8.8
- DevTasks（TASK）：TASK-0035
- 验收测试（T-xx）：T-SSOT-01
- 当前进展（2026-03-01）：bond 单写者静态扫描已落地（tools/static_scan/bond_ssot_scan.py），并通过 T-SSOT-01 回归。
- 覆盖状态：DONE

## R-11
- 系统级需求：审计链三表最小闭环 + 可回放
- PRD 需求编号（FR）：FR-AUDIT-001~003
- 来源：whitepaper 10 / PRD 4.7
- DevSpec 落点：DevSpec 8.1/9
- DevTasks（TASK）：TASK-0011,TASK-0041
- 验收测试（T-xx）：T-Audit-01~03
- 当前进展（2026-03-01）：Triptych 串联、请求回放与 retention 保护口径已实现并通过回归。
- 覆盖状态：DONE

## R-12
- 系统级需求：Intent responsibility：proposal/decision/fact 分离
- PRD 需求编号（FR）：FR-AUDIT-001（及 PRD 中 intent responsibility 条目）
- 来源：whitepaper 10 / PRD
- DevSpec 落点：DevSpec audit 契约
- DevTasks（TASK）：TASK-0006,TASK-0011,TASK-0041
- 验收测试（T-xx）：T-Intent-01,T-Intent-02
- 当前进展（2026-03-01）：intent proposal/decision/exec 三类记录已分离并可串联回放，契约回归通过。
- 覆盖状态：DONE

## R-13
- 系统级需求：工具平面渐进式加载：仅元数据 Top-K 暴露；WRITE 默认不可见；ToolBroker 单入口 + evidence_ref
- PRD 需求编号（FR）：FR-TOOL-001~003
- 来源：PRD 4.6（TOOL）
- DevSpec 落点：DevSpec 8.9
- DevTasks（TASK）：TASK-0017,TASK-0018
- 验收测试（T-xx）：T-Tool-01~03
- 覆盖状态：DONE

## R-14
- 系统级需求：通道与外显受治理：微信 I/O 审计 + VTS 确定性映射
- PRD 需求编号（FR）：FR-CHANNEL-001, FR-AVATAR-001
- 来源：PRD 4.17
- DevSpec 落点：DevSpec 8.11
- DevTasks（TASK）：TASK-0020,TASK-0021
- 验收测试（T-xx）：T-Channel-01,T-Avatar-01
- 覆盖状态：DONE

## R-15
- 系统级需求：Vault：manifest/undo/GC 审计闭环（不破坏 undo 窗口）
- PRD 需求编号（FR）：FR-LSP-001~002
- 来源：PRD 4.14
- DevSpec 落点：DevSpec 8.6
- DevTasks（TASK）：TASK-0015,TASK-0040
- 验收测试（T-xx）：T-Vault-01~02
- 当前进展（2026-03-01）：Vault manifest/undo/GC 与 retention 联动保护已完成，T-Vault-01~02 回归通过。
- 覆盖状态：DONE

## R-16
- 系统级需求：MCP 受治理接入：provider 元数据同步 + READ/WRITE 分级 + 审计串联
- PRD 需求编号（FR）：FR-MCP-001
- 来源：PRD 4.18
- DevSpec 落点：DevSpec 8.10
- DevTasks（TASK）：TASK-0019
- 验收测试（T-xx）：T-MCP-01
- 覆盖状态：DONE

## R-17
- 系统级需求：冷启动/依赖不可用的闭环可用性：结构稳定 + 可观测降级 + 可回放审计（不得伪执行）
- PRD 需求编号（FR）：FR-CORE-004, FR-OBS-001
- 来源：PRD 3.3 / 本次架构决策（2026-02-28）
- DevSpec 落点：DevSpec 1.2.1/2.1
- DevTasks（TASK）：TASK-0000,TASK-0001,TASK-0023
- 验收测试（T-xx）：T-LLM-01,T-14,T-15,T-67~T-71,T-Svc-01
- 覆盖状态：DONE

## R-18
- 系统级需求：语音主链（ASR→Host编排→TTS）事件化、可打断、可回放
- PRD 需求编号（FR）：FR-VOICE-001~003, FR-AUDIT-001
- DevSpec 落点：DevSpec 8.11.3
- DevTasks（TASK）：TASK-0024~TASK-0027
- 验收测试（T-xx）：T-Voice-01~T-Voice-03
- 当前进展（2026-03-01）：TASK-0024~TASK-0027 已完成；T-Voice-01~T-Voice-03 已通过回归。
- 覆盖状态：DONE

## R-19
- 系统级需求：Edge 兜底 LLM 默认卸载、draft-only、快速失败回退
- PRD 需求编号（FR）：FR-VOICE-004~005, FR-CORE-004
- DevSpec 落点：DevSpec 8.11.3
- DevTasks（TASK）：TASK-0028~TASK-0029
- 验收测试（T-xx）：T-Voice-04~T-Voice-05
- 当前进展（2026-03-01）：TASK-0028/TASK-0029 已完成并通过 T-Voice-04/T-Voice-05 契约回归。
- 覆盖状态：DONE

## R-20
- 系统级需求：检索链（Embedding/Rerank/Evidence Pack）接口稳定与降级可观测
- PRD 需求编号（FR）：FR-MEM-003~005, FR-OBS-001
- DevSpec 落点：DevSpec 8.3.1
- DevTasks（TASK）：TASK-0030~TASK-0032
- 验收测试（T-xx）：T-Ret-01~T-Ret-03
- 当前进展（2026-03-01）：TASK-0030/TASK-0031/TASK-0032 已完成，并通过 T-Ret-01/T-Ret-02/T-Ret-03 契约回归。
- 覆盖状态：DONE

## R-21
- 系统级需求：屏幕监控/OCR Gaming 门控 + 摄像头识别事件化与证据引用
- PRD 需求编号（FR）：FR-VISION-003~004, FR-MODE-001~002
- DevSpec 落点：DevSpec 8.12
- DevTasks（TASK）：TASK-0033~TASK-0034
- 验收测试（T-xx）：T-VisionGate-01,T-Vision-01~02
- 当前进展（2026-03-01）：TASK-0033/TASK-0034 已完成，并通过 T-VisionGate-01/T-Vision-01/T-Vision-02 契约回归。
- 覆盖状态：DONE

## R-22
- 系统级需求：生产演练包覆盖冷启动/依赖不可用/回滚冲突，故障态输出与审计链保持可回放和可统计
- PRD 需求编号（FR）：FR-CORE-004, FR-AUDIT-001, FR-OBS-001
- DevSpec 落点：DevSpec 1.2.1/10/14.5
- DevTasks（TASK）：TASK-0048
- 验收测试（T-xx）：T-67~T-71, T-Drill-01
- 当前进展（2026-03-01）：已落地三类演练场景（NoKey/provider down/rollback conflict）与统一演练报告结构（scenario_id/status/reason_code_chain/replay_ref）；故障分类与处置口径已固化，回放链路可由 request_id 稳定复盘。
- 覆盖状态：DONE

## R-23
- 系统级需求：九节点部署编排可审计，节点-服务覆盖完整，节点探针故障映射稳定可统计
- PRD 需求编号（FR）：FR-CORE-004, FR-OBS-001
- DevSpec 落点：DevSpec 10/13
- DevTasks（TASK）：TASK-0049
- 验收测试（T-xx）：T-Infra-01
- 当前进展（2026-03-01）：已落地九节点清单契约（node_id/region/role/services/health_endpoint/key_profile_id）、primary/secondary 服务映射、缺服务/角色冲突拒绝与 probe timeout 稳定映射。
- 覆盖状态：DONE

## R-24
- 系统级需求：多节点 Key Profile 引用化加载与脱敏快照联动（不落盘明文 secret），缺 key 场景维持稳定降级
- PRD 需求编号（FR）：FR-CORE-004, FR-OBS-001, NFR-MAINT-001
- DevSpec 落点：DevSpec 13.1~13.4
- DevTasks（TASK）：TASK-0050
- 验收测试（T-xx）：T-Key-01, T-Config-02
- 当前进展（2026-03-01）：已落地 `provider/node_id/scope/secret_ref/rotation_epoch/status` Key Profile 解析，支持 Host/Edge `*_KEY_REF` 运行时装配，scope mismatch/expired/missing secret 稳定拒绝；快照仅暴露 `*_key_ref` 且密钥值保持 `<redacted>`。
- 覆盖状态：DONE

## R-25
- 系统级需求：LangChain/LangGraph 风格图编排收口，执行结果与失败分类可回放且不绕过治理审计
- PRD 需求编号（FR）：FR-CORE-001, FR-CORE-004, FR-AUDIT-001
- DevSpec 落点：DevSpec 6/8.9/10
- DevTasks（TASK）：TASK-0051
- 验收测试（T-xx）：T-Orch-01, T-Audit-01
- 当前进展（2026-03-01）：已落地图编排运行时契约（配置校验 + 执行 + fallback），输出稳定封装（graph_status/reason_code/stage/replay_ref），并覆盖 missing-node/invalid-edge/timeout/dependency-down 四类失败映射与可回放 trace。
- 覆盖状态：DONE

## R-26
- 系统级需求：Neo4j 外部部署配置与启动探针可解析、可降级、可审计；探针失败与迁移执行具备链路关联
- PRD 需求编号（FR）：FR-BOND-001~003, FR-OBS-001
- DevSpec 落点：DevSpec 8.2/10/13
- DevTasks（TASK）：TASK-0052
- 验收测试（T-xx）：T-50~T-54, T-Migrate-01, T-Neo4j-01
- 当前进展（2026-03-01）：已落地 Neo4j runtime config（uri/database/auth_ref/pool/tls/enabled）与探针输出，auth/schema/leader 三类失败映射稳定；迁移执行新增 probe 前置联动并写入 migration_log（neo4j_probe）。
- 覆盖状态：DONE

## R-27
- 系统级需求：ChromaDB 外部部署配置与集合治理可解析、可降级、可审计；collection 缺失与向量维度漂移可稳定拦截
- PRD 需求编号（FR）：FR-MEM-003~005, FR-OBS-001
- DevSpec 落点：DevSpec 8.3.1/10/13
- DevTasks（TASK）：TASK-0053
- 验收测试（T-xx）：T-Ret-03, T-Chroma-01
- 当前进展（2026-03-01）：已落地 Chroma runtime config（endpoint/tenant/database/collection_profiles/default_collection）与探针输出，endpoint down/collection missing/vector dim mismatch 三类失败映射稳定；集合治理在 upsert/search 维度校验中保持可回放拒绝口径。
- 覆盖状态：DONE

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
