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

## R-28
- 系统级需求：Embedding 多提供方路由与版本化配置可解析、可降级、可审计；节点选择与向量维度漂移口径稳定
- PRD 需求编号（FR）：FR-MEM-003, FR-OBS-001
- DevSpec 落点：DevSpec 8.3.1/13
- DevTasks（TASK）：TASK-0054
- 验收测试（T-xx）：T-Ret-01, T-Embed-01
- 当前进展（2026-03-01）：已落地 embedding runtime config（provider/model_id/dim/timeout_ms/node_selector）加载与节点路由选择，覆盖 unavailable/timeout/dim drift 三类失败映射（`RETR_EMBED_*`）并将 expected_dim/actual_dim 写入 retrieval_event_log 便于审计回放。
- 覆盖状态：DONE

## R-29
- 系统级需求：Reranker 多提供方路由与分数稳定化可解析、可降级、可审计；异常分数范围必须稳定回退
- PRD 需求编号（FR）：FR-MEM-004, FR-OBS-001
- DevSpec 落点：DevSpec 8.3.1/13
- DevTasks（TASK）：TASK-0055
- 验收测试（T-xx）：T-Ret-02, T-Rerank-01
- 当前进展（2026-03-01）：已落地 reranker runtime config（provider/model_id/top_k/timeout_ms/node_selector）与节点路由选择，覆盖 model_down/timeout/invalid_score_range 三类失败映射（`RETR_RERANK_*`），并固化分数归一化 + fallback 规则及 `expected_top_k` 审计字段。
- 覆盖状态：DONE

## R-30
- 系统级需求：外部依赖联调就绪报告必须统一输出可审计基线（节点/密钥/服务/图编排/演练摘要），并对缺依赖保持稳定降级分类
- PRD 需求编号（FR）：FR-CORE-004, FR-OBS-001, NFR-MAINT-001
- DevSpec 落点：DevSpec 10/11/14.5
- DevTasks（TASK）：TASK-0056
- 验收测试（T-xx）：T-Svc-01, T-Boot-01, T-Drill-01, T-EnvReady-01
- 当前进展（2026-03-01）：已落地环境联调就绪报告（`env/node_count/key_profiles/services/graph_runtime/readiness_status`），汇总 dependency matrix 与演练摘要（`scenario_id/status/reason_code_chain/replay_ref`），并稳定区分 partial-ready（DEGRADED）与 critical-down（FAILED）场景。
- 覆盖状态：DONE

## R-31
- 系统级需求：HTTP 服务框架落地（FastAPI 路由绑定 + POST /v1/chat 链路入口 + 健康检查 + CORS + 生命周期钩子 + /metrics），所有执行仍经 Governor/Token/Skills，不引入旁路
- PRD 需求编号（FR）：FR-CORE-001, FR-CORE-002, FR-CORE-004, FR-OBS-001
- 来源：审阅报告 2026-03-02 P0-1（HTTP 服务框架缺失）
- DevSpec 落点：DevSpec 1.1/2.1
- DevTasks（TASK）：TASK-0065
- 验收测试（T-xx）：T-HTTP-01, T-HTTP-02
- 当前进展（2026-03-02）：已落地 FastAPI Application（`host_brain/app/fastapi_app.py`）：POST /v1/chat（走 IntentDecisionEngine 全链裁决）、GET /health、GET /ready、GET /metrics、POST /observability/trace；中间件含 request_id 注入 + CORS + 异常兜底；startup/shutdown 生命周期钩子串联服务探针与审计；`python -m host_brain` 可启动 HTTP 服务。T-HTTP-01（12 项路由可达性与响应结构）、T-HTTP-02（异常兜底与降级输出）全部通过。
- 覆盖状态：DONE

## R-32
- 系统级需求：链路可视化面板（Web UI 呈现审计数据、request 级排障、source 过滤、reason_code 统计图表），纯只读不引入执行入口
- PRD 需求编号（FR）：FR-OBS-001, FR-OBS-002
- 来源：审阅报告 2026-03-02 P0-2（链路可视化面板缺失）
- DevSpec 落点：DevSpec 10/11
- DevTasks（TASK）：TASK-0066
- 验收测试（T-xx）：T-UI-01
- 当前进展（2026-03-02）：已落地链路可视化面板：后端 API（`/api/ui/timeline`、`/api/ui/status-distribution`、`/api/ui/request/{id}`、`/api/ui/link-topology`、`/api/ui/search-requests`）提供时间线聚合、状态分布、请求详情、链路拓扑、搜索 5 大数据接口；前端面板（`/panel`）为纯 HTML+CSS+JS 单页面，含 request_id 搜索、时间线可视化、source 过滤、reason_code 统计图表；嵌入 FastAPI static files；面板纯只读无任何执行入口。T-UI-01（13 项：面板渲染、API 联通、空结果处理、只读安全性）全部通过。
- 覆盖状态：DONE

## R-33
- 系统级需求：Edge ASR 设备集成（faster-whisper large-v3 SDK 对接 + 音频采集 + VAD + asr_chunk 事件桥接），GPU 常驻优先、不可用时稳定降级
- PRD 需求编号（FR）：FR-VOICE-001, FR-VOICE-005
- 来源：审阅报告 2026-03-02 P0-3（Edge ASR 设备集成缺失）
- DevSpec 落点：DevSpec 8.11.3
- DevTasks（TASK）：TASK-0067
- 验收测试（T-xx）：T-Voice-01, T-ASR-HW-01
- 当前进展（2026-03-02）：已落地完整 ASR 设备集成：WhisperModelManager（GPU 常驻 + CUDA→CPU 自动降级 + 线程安全加载/卸载）、AudioStreamCapture（sounddevice 麦克风采集 + 分片回调）、AsrService（音频缓冲→VAD→转写→asr_chunk RPC 事件映射编排器）；新增 5 个 ASR 硬件层错误码（ASR_MODEL_UNAVAILABLE/ASR_GPU_OOM/ASR_DEVICE_ERROR/ASR_VAD_NO_SPEECH/ASR_STREAM_INTERRUPTED）；新增 5 个 fixtures（短/长语音 + 静音 + 低信噪比 + GPU OOM）；保持 emit_asr_chunk 向后兼容；pyproject.toml 新增 faster-whisper/sounddevice/numpy 依赖。T-ASR-HW-01（28 项：模型生命周期/音频采集/配置/结果结构/事件映射/向后兼容/错误码稳定性/资源闸门集成/健康快照/fixture 验证）全部通过。
- 覆盖状态：DONE

## R-34
- 系统级需求：Edge TTS/VTS 设备集成（GPT-SoVITS 合成 + 播放队列 + VTube Studio talk gate/表情驱动 + barge-in 恢复策略），GPU 常驻、不可用时稳定降级
- PRD 需求编号（FR）：FR-VOICE-002, FR-AVATAR-001
- 来源：审阅报告 2026-03-02 P0-3（Edge TTS/VTS 设备集成缺失）
- DevSpec 落点：DevSpec 8.11.3, DevSpec 8.11.2
- DevTasks（TASK）：TASK-0068
- 验收测试（T-xx）：T-Voice-02, T-Avatar-01, T-TTS-HW-01, T-VTS-HW-01
- 当前进展（2026-03-02）：已落地完整 TTS/VTS 设备集成：SoVITSModelManager（GPU 常驻 + 线程安全加载/卸载 + GPU OOM 报告）、AudioPlaybackDevice（sounddevice 音频输出 + 打断停止）、make_sovits_synthesizer/make_audio_playback_starter（SpeechPlaybackQueue 兼容回调工厂）；VTubeStudioDriver（WebSocket 连接/认证 + talk gate TALK_START/TALK_END + emotion_tag→expression_id 确定性映射 + barge-in 恢复 reset_to_neutral + 断连降级队列）；新增 6 个 TTS/VTS 硬件层错误码（TTS_MODEL_UNAVAILABLE/TTS_GPU_OOM/TTS_SYNTH_TIMEOUT/VTS_CONNECTION_FAILED/VTS_API_ERROR/VTS_EXPRESSION_TIMEOUT）；新增 4 个 fixtures（合成成功 + 合成失败 + VTS 断连 + barge-in 恢复）；Edge/Host 表情映射表一致性已验证。T-TTS-HW-01（25 项）+ T-VTS-HW-01（30 项）共 55 项全部通过；现有 T-Voice-02/T-Avatar-01（8 项）无回归。pyproject.toml 新增 pyvts 依赖。
- 覆盖状态：DONE

## R-35
- 系统级需求：graph_runtime 拆分为契约层/执行层/降级层/模型解析层，单文件行数≤300，对外接口与输出结构完全不变
- PRD 需求编号（FR）：FR-CORE-001, FR-CORE-004
- 来源：审阅报告 2026-03-02 P1-4（graph_runtime 单文件复杂度过高）
- DevSpec 落点：DevSpec 6/8.9/10
- DevTasks（TASK）：TASK-0069
- 验收测试（T-xx）：T-Orch-01, T-Orch-Refactor-01
- 当前进展（2026-03-02）：已将 graph_runtime.py（943行）拆分为 5 个模块：graph_contract.py（214行，Schema/验证/共享类型）、graph_executor.py（186行，LangGraph 步进执行引擎）、graph_fallback.py（152行，降级/超时/依赖不可用统一处理）、graph_model_resolve.py（170行，Model Plane 配置解析与节点路由）、graph_runtime.py（281行，编排入口壳，串联三层）。所有文件均≤300行。对外接口（GraphRuntimeContract.run_graph/register_node_handler/validate_graph_config）签名完全不变。输出结构（graph_status/reason_code/stage/replay_ref/runtime_backend/model_control/graph_trace/payload）与拆分前完全一致。T-Orch-01~05（13项）无回归；T-Orch-Refactor-01（21项：输出一致性6项+各层独立单测10项+模块行数限制5项）全部通过。新增 fixture graph_refactor_baseline.json。
- 覆盖状态：DONE

## R-36
- 系统级需求：SQLite Ledger WAL 模式优化（推荐 PRAGMA 配置 + 并发写入保护 + 写入重试 + WAL 健康快照 + checkpoint + 降级策略 + 性能基准测试脚本）
- PRD 需求编号（FR）：FR-AUDIT-001, FR-OBS-001
- 来源：审阅报告 2026-03-02 P1-5（SQLite Ledger 并发优化建议）
- DevSpec 落点：DevSpec 8.1/9/10
- DevTasks（TASK）：TASK-0070
- 验收测试（T-xx）：T-Audit-01, T-Audit-WAL-01
- 当前进展（2026-03-02）：已增强 SQLite Ledger WAL 配置：启用 synchronous=NORMAL + wal_autocheckpoint=1000 + busy_timeout=5000ms + cache_size=2000KB；新增 WalConfig 可配置数据类；写入层增加 threading.Lock 写锁 + 3 次重试策略（防 database is locked）；新增 wal_health() 健康快照（wal_status/journal_mode/synchronous/degradation_reason/wal_size_pages）；新增 checkpoint(mode) 手动检查点（PASSIVE/FULL/RESTART/TRUNCATE）；close() 自动执行 PASSIVE checkpoint；WAL 激活失败时降级到 DELETE 模式并记录 AUDIT_WAL_FAILED；新增 2 个错误码（AUDIT_WAL_FAILED/AUDIT_WAL_CHECKPOINT_FAILED）；新增性能基准测试脚本 tools/bench/ledger_bench.py（单线程/多线程/混合读写三场景，输出 JSON 结构化报告含 ops_per_sec/p95_latency_ms/wal_size）；新增 2 个 fixtures（并发写入/降级）。T-Audit-WAL-01（24 项）全部通过；T-Audit-01/T-Audit-Ret-01（4 项）无回归。
- 覆盖状态：DONE

## R-37
- 系统级需求：微信入站→Governor→VTS/语音→审计 一体化 E2E 场景覆盖（正常/拒绝/降级三条路径），审计串联键可回放
- PRD 需求编号（FR）：FR-CHANNEL-001, FR-AVATAR-001, FR-VOICE-001~003, FR-AUDIT-001
- 来源：DevTasks TASK-0071（E2E 补充）
- DevSpec 落点：DevSpec 8.11.1/8.11.2/8.11.3/8.1
- DevTasks（TASK）：TASK-0071
- 验收测试（T-xx）：T-Channel-01, T-Avatar-01, T-Voice-01~03, T-E2E-WeChat-01
- 当前进展（2026-03-02）：已落地"微信→Host编排→Governor→VTS表情+语音→审计"三条路径 E2E 测试：正常路径（微信入站→APPROVE→Skill执行→VTS表情HAPPY→语音TTS_PLAY→审计COMPLETE）、拒绝路径（TRUST_LOW→REJECT→无执行→COMPLETE_REJECTED）、降级路径（VTS_CONNECTION_FAILED+TTS_MODEL_UNAVAILABLE→DEGRADED记录→仅文本回复→COMPLETE）；审计串联键 request_id 从 channel_io_log→intent_log→governor_log→skill_exec_log→avatar_action_log→voice_event_log 完整可回放；重复消息去重、出站失败审计、VTS表情映射全量验证、无效emotion拒绝、三路径fixture结构稳定性均已覆盖。T-E2E-WeChat-01（19项）全部通过；现有 E2E 测试（9项）无回归。
- 覆盖状态：DONE

## R-38
- 系统级需求：跨端事件时间线——Host/Edge 统一 request_id/session_id 关联视图，合并排序+时间漂移容忍+Edge缺失标注
- PRD 需求编号（FR）：FR-OBS-001, FR-AUDIT-001
- 来源：DevTasks TASK-0072（跨端时间线聚合）
- DevSpec 落点：DevSpec 10/11
- DevTasks（TASK）：TASK-0072
- 验收测试（T-xx）：T-Obs-01, T-Obs-03
- 当前进展（2026-03-02）：已落地跨端时间线聚合模块 `host_brain/observability/cross_timeline.py`：EdgeEventReport（Edge 标准化上报结构）、CrossTimelineAggregator（Host 审计+Edge 上报合并、按 ts_ms 排序、source=host/edge 标记）、时间漂移检测（可配置容忍阈值 DEFAULT_CLOCK_DRIFT_TOLERANCE_MS=5000ms）、Edge 缺失时 HOST_ONLY 降级+TIMELINE_EDGE_EVENTS_MISSING 告警、漂移超限时 PARTIAL 状态+TIMELINE_CLOCK_DRIFT_DETECTED 告警、按 session_id 备选关联、无效 Edge 事件静默过滤、跨 request_id 事件隔离。新增 3 个错误码（TIMELINE_EDGE_EVENTS_MISSING/TIMELINE_CLOCK_DRIFT_DETECTED/TIMELINE_SESSION_NOT_FOUND）；新增 3 个 fixtures（混合正常/时间交叉/Edge缺失）。T-Obs-03（27 项）全部通过；T-Obs-01（2 项）无回归。
- 覆盖状态：DONE

---
- PRD_READY：PRD 已完备（FR + 验收已清晰），但 DevSpec 尚未给出可审阅的工程契约与测试编号。
- SPEC_READY：DevSpec 已给出工程契约与测试编号（T-xx）且可被拆解为任务；但 DevTasks 可能尚未拆齐。
- TASK_READY：DevTasks 已拆齐并引用 FR/T/章节，具备可执行验收断言口径；实现与测试落地仍可能未完成。
- TEST_READY：对应测试用例与 fixtures 已具备可回归形态（编号与断言点明确），可用于实现完成后的回归判定。
- DONE：实现已落地且在回归集上满足验收断言（以审计回放与测试报告为准）。

差距类状态（用于明确“缺在哪里”）：
- SPEC_GAP：DevSpec 仍缺关键契约点或测试编号（T-xx）口径，暂不具备可拆任务条件。
- TASK_GAP：DevSpec 已闭合，但 DevTasks 未拆或拆分不完整（缺输入/输出/验收/路径/引用）。
- TEST_GAP：任务已具备，但缺关键测试点/fixtures/日志断言，导致无法形成稳定回归。
