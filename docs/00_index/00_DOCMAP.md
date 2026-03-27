受众：MASTER
标题：文档地图（唯一入口）

版本：v2.1
日期：2026-03-19

本仓库所有"应该读什么、从哪里开工、哪些文档禁止当任务来源"，以本文件为准。

0）总览关系图（唯一口径）

白皮书（不可变法律）
→ PRD（FR-…：做什么+验收）
→ DevSpec（工程契约+T-…：怎么做+如何验证） + ModelPlane（模型平面规格）
→ DevTasks（TASK-…：最小交付单元，引用 FR/T/章节）
→ 测试与审计回放（证据）

使用规则：
- 实现层的任务来源只能是 DevTasks（以及 AGENTS 工作规范）；不得把白皮书/PRD/Theory/Trace 当任务来源。
- Project（文档代理）负责把讨论沉淀到 PRD/DevSpec/DevTasks，并维护 Trace 覆盖关系。

---

1）白皮书（宪法 + 人格/风格设定）
- docs/10_whitepaper/whitepaper_constitution_persona.md
  用途：定义不可变边界、不变量、真相/风格层级、人格表达约束；作为"法律与人设"。含 sys_mode/Focus 子状态、Scheduler 宪法约束、NARRATIVE_ONLY 宪法定位、PERF/SCHED 系列错误码。
  读者：MASTER、Project（文档代理）。实现层不应把它当任务来源。

2）需求文档（PRD）
- docs/20_prd/PRD.md
  用途：定义要做什么（范围、用户故事、验收、非功能指标），不描述实现步骤。
  读者：MASTER、Project（文档代理）。实现层不应把它当任务来源（除非 MASTER 任务卡点名引用）。

3）开发文档（DevSpec + 任务 + 模型平面）
- docs/30_devspec/DevSpec.md
  用途：把 PRD 落到模块、接口、协议、Schema、日志、错误码、状态机；可直接指导实现。
  读者：实现层（主要）、MASTER。

- docs/30_devspec/DevTasks.md
  用途：实现层可逐条执行的任务清单（每条都有输入/输出/验收）。
  读者：实现层（唯一任务入口）。

- docs/30_devspec/ModelPlane.md
  用途：模型平面规格（多模型节点分工、路由、降级、出网最小化、审计字段、回归口径）；用于"可回放、可追责"的模型配置治理。
  读者：MASTER、实现层（按契约实现与验证）。

4）代理行为规范（实现层）
- docs/40_agents/AGENTS.md
  用途：实现层的工作准则：任务来源门禁、交付包格式、不变量优先、测试编号（T-xx）口径。
  读者：实现层（主要）、MASTER。

- docs/40_agents/CURSOR.md
  用途：Cursor 实现层任务看板（TODO 列表、完成标准、进度追踪）；记录各 TODO 的背景、修复方案、改动文件与验证结果。
  读者：实现层（主要）、MASTER。

5）Project 指令（文档生产，不含代码）
- docs/50_project/PROJECT_BRIEF.md
- docs/50_project/PROJECT_TASKS.md
- docs/50_project/arch/ENV_EXTERNAL_SERVICES_CHECKLIST.md
- docs/50_project/arch/ExternalServices_Config.md
- docs/50_project/guides/vts_hotkey_config_guide.md
  用途：给 Project（文档代理）的产出约束、任务拆解、以及 Project×实现层×版本库 的协作工作表；配置指南与外部服务清单；不包含终端指令片段。
  读者：Project（主要）、MASTER。实现层不应把它当任务来源。

- docs/50_project/guides/DUAL_NODE_DEV_WORKFLOW.md
  用途：双节点开发协作工作流规范（2026-03-26）。定义"开发节点 ⇄ 运行节点"闭环机制：开发实测报告→真机验证→运行反馈报告→修复闭环。含报告类型定义、命名规范、流程步骤、闭环判定条件。填补已删除的 WORKSHEET_Project_Codex_Git_Flow.md 空缺。
  读者：MASTER、实现层（查阅遵守）。

- docs/50_project/guides/WXAUTOX4_FULL_API_INTEGRATION_PLAN.md
  用途：wxautox4 完整 API 对接更新方案（2026-03-27）。对比 wxautox4 v40.1.11 全量 API（~30+ 方法）与 Natsume_Ai 已对接的 5 个方法的差距，制定 P1/P2/P3 三期更新计划。涵盖核心 Bridge 扩展、命令队列 Actions 扩展、Gateway 适配器与路由扩展、新增配置项、测试用例与风险注意事项。含详细代码示例和文件修改清单。
  读者：MASTER、实现层（按计划实施）。

- docs/50_project/guides/DEV_TEST_REPORT_TEMPLATE.md
  用途：开发实测报告模板（DEV→RUN）。开发节点完成功能后按此模板产出实测报告，列出本机已验证项与真机实测清单，供运行节点执行。
  读者：实现层（模板使用）。

- docs/50_project/guides/RUNTIME_FEEDBACK_REPORT_TEMPLATE.md
  用途：运行反馈报告模板（RUN→DEV）。运行节点完成真机测试后按此模板产出反馈报告，逐条记录测试结果与新发现问题，供开发节点修复闭环。
  读者：实现层（模板使用）。

- docs/50_project/index.md
  用途：Flow 资产追溯索引（按环境），记录 flows/active.pointer 指向的当前生效 Flow 导出物；支撑 T-Flow-02 回归口径。
  读者：MASTER、Project（文档代理）。

- docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md
  用途：当前代码目录、系统分层、服务请求链路、多 Agent 治理与 Graph 编排落点对齐文档（以代码事实为准）。
  读者：MASTER、Project（文档代理）、实现层（查阅对齐）。

- docs/50_project/arch/dataflow_report.md
  用途：系统数据流全景报告（请求流、回流、模块间数据交换）；与架构文档互补，负责"流向"视角。
  读者：MASTER、Project（文档代理）、实现层（查阅对齐）。

- docs/50_project/arch/UNIFIED_MESSAGE_GATEWAY_DESIGN.md
  用途：外部消息统一网关（Unified Message Gateway）完整设计方案（2026-03-24）。包含当前 UIA 桌面自动化方案的问题诊断、协议级网关架构设计、UnifiedMessage/UnifiedReply 数据模型、各平台适配器设计（微信/QQ/飞书/企微）、与现有 EdgeInputReporter 的集成方案、EdgeConfig 扩展配置、以及分阶段实施路线图。
  读者：MASTER、Project（文档代理）、实现层（查阅对齐）。

- docs/50_project/arch/WECHAT_UIA_HIGH_PRIORITY_SOLUTIONS.md
  用途：微信适配器高优先级问题解决方案（2026-03-26）。H1 多聊天轮询（未读嗅探+按需切换）、H2 群聊发送者识别（位置启发式昵称提取）、H3 多媒体消息支持（类型感知+占位通知+缓存目录监控+send_image 发图），含方案细节、代码、改动范围与风险缓解。已实施 commit 48c2e02。
  读者：MASTER、实现层（查阅对齐）。

- docs/50_project/phases/MULTI_AGENT_UPGRADE_PLAN.md
  用途：多 Agent 编排升级详细实施方案（2026-03-16）。基于当前单 agent 多节点路由架构，制定分四阶段升级为"Host 主权 + Supervisor 主 agent + 多专家子 agent + Judge 收口"体系的可落地方案。含角色定义、数据契约、各场景链路设计、文件改动清单、验收标准与风险评估。
  读者：MASTER、Project（文档代理）。

- docs/50_project/arch/AGENT_ORCHESTRATION_DETAIL.md
  用途：Agent 编排详情（2026-03-18，当前实现对齐）。记录当前 Supervisor/子 agent/Judge 编排的实际实现口径，含历史草稿留痕。以本文件"当前口径"章节为准，历史草稿仅作参考。
  读者：MASTER、Project（文档代理）、实现层（查阅对齐）。

- docs/50_project/phases/MULTI_AGENT_P4_SCOPE.md
  用途：P4 范围与验收标准（意图子图透明封装）。定义 P4 阶段的交付范围、验收条件与完成标准。
  读者：Project（文档代理）。

- docs/50_project/phases/MULTI_AGENT_P5_SCOPE.md
  docs/50_project/phases/MULTI_AGENT_P5_SEAL_RESULT.md
  用途：P5 范围与验收标准（Supervisor 决策升级）+ P5 验收结论。
  读者：Project（文档代理）。

- docs/50_project/phases/MULTI_AGENT_P6_SCOPE.md
  docs/50_project/phases/MULTI_AGENT_P6_SEAL_RESULT.md
  docs/50_project/phases/MULTI_AGENT_P6_1_LOCAL_LOOP_CONTRACT.md
  docs/50_project/phases/MULTI_AGENT_P6_2_IMPLEMENTATION.md
  docs/50_project/phases/MULTI_AGENT_P6_3_INTEGRATION_ACCEPTANCE.md
  用途：P6 范围与验收标准（Intent 子图局部循环）+ P6 验收结论 + P6 子文档（局部循环契约、最小闭环实现、集成验收补齐）。
  读者：Project（文档代理）。

- docs/50_project/phases/MULTI_AGENT_P7_SCOPE.md
  docs/50_project/phases/MULTI_AGENT_P7_SEAL_RESULT.md
  docs/50_project/phases/MULTI_AGENT_P7_2_FAILURE_MATRIX.md
  docs/50_project/phases/MULTI_AGENT_P7_3_OBSERVABILITY_SCOPE.md
  docs/50_project/phases/MULTI_AGENT_P7_4_FAULT_INJECTION_BASELINE.md
  docs/50_project/phases/MULTI_AGENT_P7_ACCEPTANCE_PREVIEW.md
  用途：P7 范围与验收标准（治理与稳定性基线）+ P7 验收结论 + P7 子文档（三图失败收口矩阵、统一观测面范围、压测与故障注入基线、验收汇总预审）。
  读者：Project（文档代理）。

- docs/50_project/phases/MULTI_AGENT_P8_SCOPE.md
  docs/50_project/phases/MULTI_AGENT_P8_SEAL_RESULT.md
  docs/50_project/phases/MULTI_AGENT_P9_SCOPE.md
  docs/50_project/phases/MULTI_AGENT_P9_SEAL_RESULT.md
  docs/50_project/phases/MULTI_AGENT_P10_SCOPE.md
  docs/50_project/phases/MULTI_AGENT_P10_SEAL_RESULT.md
  docs/50_project/phases/MULTI_AGENT_P11_SCOPE.md
  docs/50_project/phases/MULTI_AGENT_P11_SEAL_RESULT.md
  docs/50_project/phases/MULTI_AGENT_P12_SCOPE.md
  docs/50_project/phases/MULTI_AGENT_P12_SEAL_RESULT.md
  用途：P8–P12 各阶段范围与验收标准 + 对应封板结论（P8–P12 为候选基线迭代阶段）。
  读者：Project（文档代理）。

- docs/50_project/phases/MULTI_AGENT_P13_SCOPE.md
  docs/50_project/phases/MULTI_AGENT_P13_SEAL_RESULT.md
  用途：P13 范围与封板结论（tool_registry 工具实现升级，search_knowledge 真实接入）。
  读者：Project（文档代理）。

- docs/50_project/phases/MULTI_AGENT_P14_SCOPE.md
  docs/50_project/phases/MULTI_AGENT_P14_SEAL_RESULT.md
  用途：P14 范围与封板结论（judge_agent ReAct 化）。
  读者：Project（文档代理）。

- docs/50_project/phases/MULTI_AGENT_P15_SCOPE.md
  docs/50_project/phases/MULTI_AGENT_P15_SEAL_RESULT.md
  用途：P15 范围与封板结论（终点封板：Supervisor LLM 提案 + graph_executor 动态路由）。
  读者：Project（文档代理）。

6）可追溯性（Trace）
- docs/60_trace/requirements_trace.md
  用途：审阅"需求→设计→任务→测试"覆盖情况。
  口径：若 csv 未维护或滞后，以 md 为准。
  读者：MASTER、Project（文档代理）。实现层不应把它当任务来源。

- docs/60_trace/flows/index.md
  用途：Flow 指针与索引的一致性。
  读者：MASTER、Project（文档代理）。

- docs/60_trace/risk_registry.md
  用途：风险登记册，记录已识别风险、缓解措施与状态。
  读者：MASTER、Project（文档代理）。

- docs/45_testing/test_reports/test_report_2026-03-05.md
  用途：全量测试报告（2026-03-05）。
  读者：MASTER、Project（文档代理）。

- docs/45_testing/test_reports/EDGE_OPTIMIZATION_AND_TEST_REPORT_2026-03-04.md
  用途：Edge 优化与测试报告（2026-03-04）。
  读者：MASTER、Project（文档代理）。

- docs/45_testing/test_reports/EMOTION_CLOSED_LOOP_REPORT_20260309_235917.md
  用途：情感闭环测试报告（2026-03-09）。验证情感状态写入、读取与回流的完整闭环链路。
  读者：MASTER、Project（文档代理）。

- docs/45_testing/runtime_feedback_reports/RUNTIME_FEEDBACK_wechat_dual_node_uia_probe_20260326.md
  用途：双节点微信 UIAutomation 探测运行反馈报告（2026-03-26）。记录 DEV/RUN 两端主窗体均仅暴露 2~3 个顶层 UIA 元素、缺失 `mmui::ChatSingleWindow` / `mmui::ChatTextItemView` 的真机证据，用于推动 UIA 树恢复方案修复。
  读者：MASTER、实现层、Project（文档代理）。

- docs/45_testing/runtime_feedback_reports/RUNTIME_FEEDBACK_wechat_uia_tree_recovery_20260326.md
  用途：微信 4.x UIA 树恢复验证脚本运行反馈报告（2026-03-26）。记录 `verify_uia_recovery.py` 在 RUN 节点的真实执行结果、窗口类名不匹配与编码问题，以及 P0 验证未能进入三路方案分支的原因。
  读者：MASTER、实现层、Project（文档代理）。

- docs/45_testing/runtime_feedback_reports/RUNTIME_FEEDBACK_wechat_uia_verify_fix_v2_20260326.md
  用途：微信 4.x UIA 树恢复验证脚本修正（v2）运行反馈报告（2026-03-26）。记录修正版脚本在 RUN 节点已修复窗口发现、编码与 JSON 汇总问题，但三种 P0 方案仍未恢复出聊天 UIA 控件树的真机结果。
  读者：MASTER、实现层、Project（文档代理）。

- docs/45_testing/dev_test_reports/DEV_TEST_REPORT_wechat_uia_tree_recovery_20260326.md
  用途：微信 4.x UIA 树恢复验证开发实测报告（2026-03-26）。含 P0 方案（UIA 客户端冒充）的验证脚本和 7 项真机测试清单（T-01 ~ T-07），需在 RUN 节点执行验证。
  读者：MASTER、实现层、Project（文档代理）。

- docs/45_testing/dev_test_reports/DEV_TEST_REPORT_wechat_uia_verify_fix_v2_20260326.md
  用途：微信 4.x UIA 树恢复验证脚本修正版（v2）实测报告。基于 RUN 节点运行反馈的 4 项修正：窗口多类名兼容、UTF-8 编码兼容、去 emoji、双条件成功判定。含 7 项真机测试清单。
  读者：MASTER、实现层、Project（文档代理）。

- docs/45_testing/dev_test_reports/DEV_TEST_REPORT_p1_uia_strategies_20260326.md
  用途：P1 三方案 UIA 恢复策略开发实测报告（2026-03-26）。在 P0 方案验证均失败后，实现三种更强的恢复策略：S1 持久化 UIA 事件监听器、S2 NVDA/Narrator 屏幕阅读器辅助、S3 Minimal Accessibility Client。含整合对比测试脚本和 8 项真机测试清单。
  读者：MASTER、实现层、Project（文档代理）。

- docs/45_testing/dev_test_reports/DEV_TEST_REPORT_p1_comtypes_fix_20260326.md
  用途：P1 脚本自举修复报告（2026-03-26）。修复 comtypes.gen.UIAutomationClient 类型库缺失导致三个 P1 策略全部无法启动的问题（显式 GetModule），统一对比器 S2 的 Narrator PowerShell fallback 启动逻辑。7 项测试（P0×5 / P1×2）。
  读者：MASTER、实现层、Project（文档代理）。

- docs/45_testing/runtime_feedback_reports/RUNTIME_FEEDBACK_p1_comtypes_fix_20260326.md
  用途：P1 脚本自举修复运行反馈报告（2026-03-26）。确认 `GetModule("UIAutomationCore.dll")` 已修复 `comtypes.gen.UIAutomationClient` 导入失败，S2 对比器已统一 Narrator PowerShell fallback；同时记录三种策略虽能启动但仍未拉起微信 4.x 聊天 UIA 树，以及 S1/S3 长跑脚本独立留证仍需改进。
  读者：MASTER、实现层、Project（文档代理）。

- docs/45_testing/dev_test_reports/DEV_TEST_REPORT_telegram_adapter_20260326.md
  用途：Telegram Userbot 适配器（Phase 6）开发实测报告（2026-03-26）。Telethon MTProto 协议实现"人感"消息收发，含 GatewayConfig 集成、EdgeConfig 环境变量、bootstrap 注册、架构文档更新。10 项真机测试（P0×5 / P1×3 / P2×2），涵盖依赖安装、首次登录、消息收发、打字模拟、延迟抖动、长文分段、代理、断线重连、多用户、群聊。
  读者：MASTER、实现层、Project（文档代理）。

- docs/45_testing/dev_test_reports/DEV_TEST_REPORT_qq_adapter_enhancement_20260326.md
  用途：QQ 适配器增强版开发实测报告（2026-03-26）。含默认平台配置调整（wechat,qq→qq）、QQ 适配器 6 项功能增强（VOICE 发送、@提醒检测、URL/base64 图片、notice/request 事件日志、消息段构造重构）、新建单元测试 test_qq_adapter.py（27 个测试用例）。9 项真机测试（P0×5 / P1×3 / P2×1）。
  读者：MASTER、实现层、Project（文档代理）。

- docs/45_testing/dev_test_reports/DEV_TEST_REPORT_line_adapter_20260326.md
  用途：LINE Messaging API 适配器开发实测报告（2026-03-26）。基于飞书 Webhook+REST 架构模式实现 LINE 官方 Messaging API 接入，含 HMAC-SHA256 签名验证、7 种消息类型解析、Reply/Push 双通道发送、EdgeConfig/GatewayConfig/bootstrap 全链路集成。11 项真机测试清单（Webhook 接收、消息解析、回复发送、签名验证等）。
  读者：MASTER、实现层、Project（文档代理）。

- docs/45_testing/dev_test_reports/DEV_TEST_REPORT_emotion_system_fixes_2026-03-26.md
  用途：情感系统 6 项 Bug 修复开发实测报告（2026-03-26）。覆盖线程安全（threading.Lock）、JSON 文件持久化、标签集统一（16 种）、VAD 映射唯一权威来源（emotion_constants.py）、EmotionTimeline/compute_emotion_echo 代码去重（~250 行）、衰减参数环境变量可配置化（9 个环境变量）。7 个文件修改，含 6 项待 RUN 节点验证测试。
  读者：MASTER、实现层、Project（文档代理）。

- docs/45_testing/dev_test_reports/SOUL_SYSTEM_COMPLETENESS_AUDIT_20260326.md
  用途：灵魂系统完整性审计报告（2026-03-26）。对 Phase I-VIII 全部 8 阶段的代码实体、测试覆盖、设计文档对齐、里程碑追溯、模块导出 6 个维度进行交叉验证。结论：灵魂系统 8 阶段全部实现完毕，12 个 persona 文件（~130KB）+ 14 个 bond 文件（~165KB），14 个测试文件覆盖全阶段，M27/M28/M29/M32 里程碑均 DONE。
  读者：MASTER、实现层、Project（文档代理）。

- docs/45_testing/dev_test_reports/DEV_TEST_REPORT_wxautox4_bridge_20260327.md
- docs/45_testing/dev_test_reports/DEV_TEST_REPORT_wxautox4_phase1_20260327.md
  用途：wxautox4 Bridge Phase 1 开发实测报告（2026-03-27）。记录 Phase 1 新增 9 个 actions（IsOnline/GetMyInfo/好友群列表/历史消息/媒体下载/监听管理/ChatWith）的实现，含 10 条待真机验证测试用例（T-P1-01~T-P1-10）。
  读者：MASTER、运行节点（按用例验证）。
- docs/45_testing/dev_test_reports/DEV_TEST_REPORT_wxautox4_phase2_20260327.md
  用途：wxautox4 Bridge Phase 2 开发实测报告（2026-03-27）。记录 Phase 2 新增 8 个 actions（GetSubWindow/ChatInfo/GetDialog/SendUrlCard/GetAllMessage/GetNewFriends/GetFriendDetails）+ LinkMessage.get_url() 解析 + Gateway HTTP 查询端点（POST /v1/gateway/wechat/query），含 11 条待真机验证测试用例（T-P2-01~T-P2-11）。
  读者：MASTER、运行节点（按用例验证）。
  用途：wxautox4 Bridge 集成开发实测报告（2026-03-27）。新增 wxautox4 作为微信自动化 bridge 后端（替代自研 UIA Bridge），外部协议完全兼容（JSONL 命令队列 + webhook POST）。改动 5 个文件，含 8 项真机测试清单（P0×4 / P1×2 / P2×2），涵盖环境安装、独立启动、inbound 消息转发、outbound 命令执行、热切换回退、图片发送、消息去重、群聊识别。
  读者：MASTER、实现层、Project（文档代理）。

- docs/50_release/changelog/UI_UPGRADE_CHANGELOG.md
  用途：UI 升级变更日志。
  读者：MASTER、Project（文档代理）。

- docs/50_release/changelog/MODEL_LOCAL_CLOUD_CHANGELOG_2026-03-05.md
  用途：模型本地/云端切换变更日志（2026-03-05）。
  读者：MASTER、Project（文档代理）。

- docs/50_release/release_seal_test_plan_20260313.md
  用途：封板测试计划（2026-03-13）。定义封板前必须通过的测试项目、验收标准与执行顺序。
  读者：MASTER、Project（文档代理）。

- docs/50_release/release_seal_test_plan_supplement_20260315.md
  用途：封板测试计划补充（2026-03-15）。对 20260313 测试计划的补充与修订。
  读者：MASTER、Project（文档代理）。

- docs/50_release/release_seal_execution_report_20260316.md
  用途：封板执行报告（2026-03-16）。记录封板测试的实际执行结果、通过/失败项与最终封板决策。
  读者：MASTER、Project（文档代理）。

- docs/50_release/reports/release_seal_20260316/00_index/BATCH1_20260316_p0_entry.md
  docs/50_release/reports/release_seal_20260316/00_index/BATCH2_20260316_p1_p2_formal.md
  docs/50_release/reports/release_seal_20260316/00_index/BATCH3_20260316_automated.md
  docs/50_release/reports/release_seal_20260316/00_index/BATCH4_20260316_full_auto.md
  docs/50_release/reports/release_seal_20260316/00_index/MULTI_AGENT_SEAL_RESULT.md
  docs/50_release/reports/release_seal_20260316/00_index/README.md
  用途：2026-03-16 封板批次报告（P0 入口、P1/P2 正式、自动化、全自动、多 Agent 封板结果与索引）。
  读者：MASTER、Project（文档代理）。

- docs/50_release/rollback/ROLLBACK_RUNBOOK_2026-03-10.md
  用途：回滚操作手册（2026-03-10）。定义各里程碑的回滚步骤、前置条件与验证口径。
  读者：MASTER、Project（文档代理）。

- docs/50_release/rollback/RELEASE_ROLLBACK_GATE_20260310_010648.md
  用途：发布回滚门控记录（2026-03-10）。记录回滚决策触发条件与实际执行情况。
  读者：MASTER、Project（文档代理）。

- docs/50_release/rollback/POST_RELEASE_REVIEW_20260310_010918.md
  用途：发布后复盘报告（2026-03-10）。记录发布后问题、根因分析与改进措施。
  读者：MASTER、Project（文档代理）。

- docs/60_trace/ENV_SINGLE_SOURCE_MIGRATION_2026-03-04.md
  用途：环境变量单一来源迁移记录（2026-03-04）。
  读者：MASTER、Project（文档代理）。

- docs/60_trace/CANON_VECTOR_PROJECTION_2026-03-04.md
  用途：Canon 向量投影分析（2026-03-04）。
  读者：MASTER、Project（文档代理）。

- docs/60_trace/ARCH_REVIEW_AND_ROADMAP.md
  用途：架构评审与路线图（2026-03-09）。对当前架构的评审结论与后续演进路线规划。
  读者：MASTER、Project（文档代理）。

- docs/60_trace/RETRIEVAL_ORCHESTRATOR_DESIGN.md
  用途：检索编排器设计文档（2026-03-09）。记录检索层编排逻辑、优先级策略与接口设计。
  读者：MASTER、Project（文档代理）。

- docs/60_trace/README_trial_run_reports.md
  用途：试运行报告目录说明（README）。解释 artifacts/trial_run_reports/ 下各报告的命名规范与阅读方式。
  读者：MASTER、Project（文档代理）。

- docs/60_trace/VTS_VOICE_TROUBLESHOOTING.md
  用途：VTS 语音故障排查手册。记录语音链路常见问题、排查步骤与已知解决方案。
  读者：MASTER、Project（文档代理）。

- docs/60_trace/WECHAT_4X_UIA_FEASIBILITY_ANALYSIS.md
  用途：微信 4.x PC 版 UIAutomation 方案可行性分析报告（2026-03-25）。三种技术路线对比（纯 UIAutomation / 视觉方案 / Hook 注入），4.1.5+ "UI 树消失"问题与恢复方案，已验证开源项目汇总，与 Natsume_Ai Edge Client 架构的适配分析，风险清单与实施路径。
  读者：MASTER、实现层（查阅对齐）。


- artifacts/trace/HOST_CHAT_*_req_*.json
  artifacts/trace/CANON_STYLE_SYNC_*.json
  artifacts/trace/canon_sync_summary_*.json
  用途：运行时原始 JSON 追踪数据（模型控制、检索事件、Canon 同步等）。
  读者：MASTER（调试/回放用）。

- docs/35_quality/audits/audit_report_20260311.md
  用途：Natsume AI 深度审计报告（2026-03-11）。覆盖意图识别、模型编排控制、Agent 路由、SSE 双端对齐、情感层接入、Bond 优化六大维度。
  读者：MASTER、Project（文档代理）。

- artifacts/trial_run_reports/
  用途：自动化试运行报告（T1-x 系列，按时间戳归档）。
  读者：MASTER、Project（文档代理）。

- docs/35_quality/code_reviews/
  用途：代码审查报告归档目录（含全量审查报告与各里程碑专项审查报告）。
  命名规范：
    - 全量审查：full_code_review_report_YYYY-MM-DD[-vN|-mXX].md
    - 里程碑专项：code_review_mXX_YYYY-MM-DD.md
    - 仓库分析：repo_code_analysis_report_YYYY-MM-DD.md
  读者：MASTER、Project（文档代理）。

7）提示词入口
- prompts/CODEX_PROMPT.md
- prompts/PROJECT_PROMPT.md
- prompts/llm_contract/*
  用途：分别作为实现层与 Project 的入口提示词；以及 LLM 输出契约/拒绝范式等（作为契约资产，不作为需求来源）。
  读者：按文件受众标注执行。

8）理论文档（Theory：论证/原则推导/边界解释）
- docs/15_theory/Theory.md
  用途：解释"为什么这样设计"、关键机制的理论依据与判定口径；不作为实现任务清单。
  读者：MASTER、Project（文档代理）。实现层不应把它当任务来源（除非 MASTER 任务卡点名引用）。

- docs/15_theory/soul_emotion_optimization.md
  用途：灵魂情感架构优化设计留痕文档（2026-03-05）。记录五大优化方向：①情绪连续体 EmotionVector、②Bond 情感质感层、③情感记忆标注、④emotion_pulse 动态槽位、⑤主动情感触发器。含完整伪代码设计方案、优先级排序与安全约束说明。待实施，不作为当前任务来源。
  读者：MASTER、Project（文档代理）。

- docs/15_theory/LANGGRAPH_CONSTRAINT_CONFLICT_ANALYSIS.md
  用途：LangGraph 高级能力与 Host 治理约束冲突分析（2026-03-06）。分析五项冲突点（条件边、并行分支、子图、断点续跑、流式输出）与当前系统约束的关系。
  读者：MASTER、Project（文档代理）。

- docs/15_theory/LangGraph_Governed_Runtime_Solution.md
  用途：LangGraph Governed Runtime 更优解法方案（2026-03-06）。提出三层架构（Policy Plane / Execution Plane / Evidence Plane）、Host Governance Overlay 方案、五阶段能力恢复路径与三句话架构结论。
  读者：MASTER、Project（文档代理）。

- docs/15_theory/architecture_overview.md
  用途：Natsume AI 整体架构全景文档（2026-03-10）。含双端总体架构、Host Brain 各模块、Edge 各模块、数据流、记忆层、治理层的完整 Mermaid 图与说明。
  读者：MASTER、Project（文档代理）。

- docs/15_theory/CORE_LAYER_MODULE_REFERENCE.md
  用途：host_brain/core/ 层模块参考（2026-03-19）。覆盖 prompt_context 槽位契约与验证规则、metabolism 硬状态与情感向量机制、emotion_timeline 情感时间线、services 服务注册表与健康报告的完整接口语义与约束说明。
  读者：MASTER、Project（文档代理）、实现层（查阅对齐）。

- docs/15_theory/INTEGRATIONS_LAYER_CONTRACT.md
  用途：host_brain/integrations/ 层接口契约（2026-03-19）。覆盖 GraphState 全字段语义、graph_contract 核心函数、Evidence Plane 写入机制（CRITICAL/NON_CRITICAL/EvidenceWriteQueue）、治理约束规则（Rule 1–6）与 Multi-Agent 契约（P4–P7）。
  读者：MASTER、Project（文档代理）、实现层（查阅对齐）。

- docs/15_theory/RETRIEVAL_LAYER_CONTRACT.md
  用途：host_brain/retrieval/ 层接口契约（2026-03-19）。覆盖检索四阶段（Embed→RecallFusion→Rerank→EvidencePack）、RetrievalOrchestrator 入口契约与缓存机制、路径分类（success/partial_failure/all_failure）、降级摘要、情感上下文透传（OPT-41）与工厂函数 build_retrieval_pipeline_from_env。
  读者：MASTER、Project（文档代理）、实现层（查阅对齐）。

- docs/15_theory/GOVERNOR_SCHEDULER_CONTRACT.md
  用途：host_brain/governor/ + scheduler/ 层接口契约（2026-03-19）。覆盖 R1–R8 八条门控规则（含 R8 情感感知门控）、GateSnapshot 快照归一化、TokenService WRITE 令牌验证；NatsumeScheduler 三种触发模式、任务持久化（F-40）、心跳健康检测（TASK-0200）、Edge 探针检查（TASK-0211）与情感状态门控（M29-N）。
  读者：MASTER、Project（文档代理）、实现层（查阅对齐）。

- docs/15_theory/TOOLS_MEMORY_LAYER_CONTRACT.md
  用途：host_brain/tools/ + memory/ 层接口契约（2026-03-19）。覆盖 ToolMeta 元数据契约、ToolCatalog 注册表、ToolBroker WRITE 令牌门控与情感上下文注入（M27-J）、MCP 适配器；Canon 同步机制（sync_world_canon_index/detect_world_version_drift）、情节记忆、蒸馏与上下文压缩。
  读者：MASTER、Project（文档代理）、实现层（查阅对齐）。

- docs/15_theory/PERIPHERAL_LAYERS_CONTRACT.md
  用途：host_brain 外围层接口契约（2026-03-19）。覆盖 airlock 入站隔离分类（quarantine_ingress/SandboxExecutor）、bond 情感关系图谱写入（BondDynamicsWriter/BondStageEngine 六阶段晋级）、channels 双通道 I/O（VTS 16 种情感表情映射/WeChat 幂等去重）、vision 多检测器并行聚合（MultiDetectorAggregator）、lsp/ArtifactVault 可逆工件库（commit/undo/gc）、mcp/MCPToolRegistry 工具元数据注册与变更追踪。
  读者：MASTER、Project（文档代理）、实现层（查阅对齐）。

- docs/15_theory/APP_AUDIT_SKILLS_CONTRACT.md
  用途：host_brain 核心应用层契约（2026-03-19）。覆盖 app 层场景分类（CHAT/EXEC_INTENT/WORK_CODE/WORK_TRANSLATE/VISION_UI）、聊天处理链路（Governor→场景分类→SceneRouter）、Prompt 构建与注入检测；audit/SQLiteLedger 全系统唯一审计账本（26 张表、WAL 模式、append-only 约束）；skills/SkillRegistry 技能元数据契约（clearance/risk_level/scope/reversible）与 17 个技能实现；observability 指标采集（ObsMode PROD/DEV、debug_trace 安全约束）。
  读者：MASTER、Project（文档代理）、实现层（查阅对齐）。

- docs/15_theory/SOUL_SYSTEM_FULL_UPGRADE_PATH.md
  用途：灵魂系统全面升级路径（2026-03-23）。覆盖 8 大升级阶段：Phase I 人格引擎基座（PersonaEngine + 7 维特质向量）、Phase II 情感-人格耦合（EmotionReactivity + ExpressionAdapter）、Phase III 自我意识模型（SelfModel + 自省日记）、Phase IV 人格成长系统（PersonaGrowth + TraitDriftGuard 安全机制）、Phase V Bond 深化（SharedMemoryIndex + MilestoneTracker + BondNarrative）、Phase VI 人格漂移检测（PersonaDriftDetector + 告警校正）、Phase VII 跨会话情感连续性（EmotionPersistence + 衰减恢复模型）、Phase VIII 场景化主动表达（SceneAwareTrigger + PersonaProactiveVoice）。含系统级全景诊断、新增/改动文件清单、风险缓解与实施路线图。待实施，不作为当前任务来源。
  读者：MASTER、Project（文档代理）。

9）性能与稳定性监控（Metrics & Stability）
- docs/55_testing/metrics/baseline_stream_metrics_2026-03-09.md
  用途：流式响应基线性能指标（2026-03-09）。记录 SSE 流式输出的延迟、吞吐量与稳定性基线数据。
  读者：MASTER、Project（文档代理）。

- docs/55_testing/metrics/LONGTERM_METRIC_DASHBOARD_20260310_014526.md
  用途：长期指标看板（2026-03-10）。汇总多维度性能指标趋势，用于长期健康度追踪。
  读者：MASTER、Project（文档代理）。

- docs/55_testing/stability_weekly/WEEKLY_STABILITY_REPORT_20260310_005912.md
  用途：周度稳定性报告（2026-03-10）。记录一周内系统稳定性数据、异常事件与改进建议。
  读者：MASTER、Project（文档代理）。

10）协作与运行手册（Worksheet：分工/流程/落地节奏）

（注：协作工作表 WORKSHEET_Project_Codex_Git_Flow.md 已删除，由 docs/50_project/guides/DUAL_NODE_DEV_WORKFLOW.md（双节点开发协作工作流）替代，已在第 5 节登记。docs/50_project/ 下的 PROJECT_BRIEF.md、PROJECT_TASKS.md、arch/ENV_EXTERNAL_SERVICES_CHECKLIST.md、arch/ExternalServices_Config.md、guides/vts_hotkey_config_guide.md、index.md、arch/CURRENT_CODE_ARCHITECTURE.md、arch/dataflow_report.md、phases/MULTI_AGENT_UPGRADE_PLAN.md 已在第 5 节登记。）

11）配置资产入口（非文档，但属于“可部署/可回放”真相源的一部分）
- flows/
  用途：Flow 导出物与环境指针（exports/ + active.pointer）；由 Trace（docs/60_trace/flows/index.md）指向当前生效版本。
  读者：MASTER、实现层（按 DevSpec/DevTasks 约束维护与回归）。

- 根目录 .env / .env.example
  用途：运行时单一配置源（`.env`）与示例/降级模板（`.env.example`）；用于 Host/Edge/Neo4j/Chroma/Retrieval 统一配置。
  读者：MASTER、实现层（按 DevSpec 与运行脚本约束实现与回归）。

12）发布目录（Release）
- configs/release/mXX_release.json
  用途：各里程碑发布清单（JSON 格式），记录发布版本、变更摘要、测试通过情况。
  读者：MASTER、Project（文档代理）。

- configs/release/release_catalog.json
  用途：发布资产目录（M0~M3 里程碑发布清单，含资产路径、依赖服务、配置快照引用）。
  读者：MASTER、Project（文档代理）。

- configs/release/release_rollback_map.json
  用途：发布回滚映射表，记录各里程碑的回滚目标版本与回滚步骤引用。
  读者：MASTER、Project（文档代理）。

- artifacts/release/reports/
  用途：发布报告（T1-x 系列，按时间戳归档）。
  读者：MASTER、Project（文档代理）。

---

附：实现层任务来源白名单（强约束）
- 仅允许将以下目录作为"任务来源"读取并据此行动：
  - docs/30_devspec/**
  - docs/40_agents/**
- 若需引用其它文档，必须由 MASTER 在任务卡中明确点名路径与引用范围。

附：受众门禁（文档头部强制）
- 所有 .md 文件必须以两行头部开场：
  - `受众：MASTER|CODEX|PROJECT`
  - `标题：...`

附：文档归类规则（v1.2 新增，v1.5 补充）
- 代码审查报告（code_review_*.md、full_code_review_report_*.md）→ docs/35_quality/code_reviews/
- 测试报告、运行追踪报告、变更日志、审计报告、数据流报告 → docs/60_trace/
- 架构全景、架构分析、理论推导、设计方案 → docs/15_theory/
- 配置指南 → docs/50_project/guides/
- 运行手册、项目协作文档、Flow 资产索引 → docs/50_project/
- 架构文档（代码架构、数据流、Agent 编排、外部服务配置）→ docs/50_project/arch/
- Multi-Agent 各阶段范围文档（MULTI_AGENT_Pxx_SCOPE.md）、封板结论（MULTI_AGENT_Pxx_SEAL_RESULT.md）、子阶段文档（MULTI_AGENT_Pxx_y_*.md）→ docs/50_project/phases/
- 发布清单（JSON）、发布目录、回滚映射 → configs/release/
- 封板测试计划、封板执行报告、回滚手册、发布后复盘 → docs/50_release/
- 性能基线指标、长期指标看板、周度稳定性报告 → docs/55_testing/
- 开发规范、任务清单、模型平面 → docs/30_devspec/
- 任务看板、TODO 追踪 → docs/40_agents/
