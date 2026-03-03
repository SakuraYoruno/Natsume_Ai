受众：MASTER
标题：需求可追溯性矩阵（Requirements Trace）

版本：v1.4

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
- DevTasks（TASK）：TASK-0067,TASK-0081
- 验收测试（T-xx）：T-Voice-01, T-ASR-HW-01, T-ASR-Recovery-01
- 当前进展（2026-03-02）：已落地完整 ASR 设备集成：WhisperModelManager（GPU 常驻 + CUDA→CPU 自动降级 + 线程安全加载/卸载）、AudioStreamCapture（sounddevice 麦克风采集 + 分片回调）、AsrService（音频缓冲→VAD→转写→asr_chunk RPC 事件映射编排器）；新增 5 个 ASR 硬件层错误码（ASR_MODEL_UNAVAILABLE/ASR_GPU_OOM/ASR_DEVICE_ERROR/ASR_VAD_NO_SPEECH/ASR_STREAM_INTERRUPTED）；新增 5 个 fixtures（短/长语音 + 静音 + 低信噪比 + GPU OOM）；保持 emit_asr_chunk 向后兼容；pyproject.toml 新增 faster-whisper/sounddevice/numpy 依赖。TASK-0081 新增 ASR 麦克风设备自动恢复机制：DeviceRecoveryConfig（max_attempts/base_delay_s/max_delay_s/auto_recover/try_alternate_devices 可配置）、_device_recovery_loop（指数退避 1s→2s→4s→8s 上限，最多 5 次）、trigger_device_recovery（非阻塞后台线程启动）、备用设备自动切换（原始设备失败→enumerate_input_devices→逐一尝试）、恢复成功上报 ASR_DEVICE_RECOVERED、恢复耗尽进入 ERROR 并上报 ASR_DEVICE_RECOVERY_EXHAUSTED、shutdown_event 可中断恢复、health 快照含 recovery 统计；AudioStreamCapture 新增 set_device_index/enumerate_input_devices/original_device_index；新增 2 个恢复错误码（ASR_DEVICE_RECOVERED/ASR_DEVICE_RECOVERY_EXHAUSTED）、3 个 fixtures（成功/耗尽/备用设备切换）。T-ASR-Recovery-01（30+ 项）全部通过；T-ASR-HW-01（28 项）无回归。
- 覆盖状态：DONE

## R-34
- 系统级需求：Edge TTS/VTS 设备集成（GPT-SoVITS 合成 + 播放队列 + VTube Studio talk gate/表情驱动 + barge-in 恢复策略），GPU 常驻、不可用时稳定降级
- PRD 需求编号（FR）：FR-VOICE-002, FR-AVATAR-001
- 来源：审阅报告 2026-03-02 P0-3（Edge TTS/VTS 设备集成缺失）
- DevSpec 落点：DevSpec 8.11.3, DevSpec 8.11.2
- DevTasks（TASK）：TASK-0068,TASK-0080
- 验收测试（T-xx）：T-Voice-02, T-Avatar-01, T-TTS-HW-01, T-VTS-HW-01, T-VTS-Reconnect-01
- 当前进展（2026-03-02）：已落地完整 TTS/VTS 设备集成：SoVITSModelManager（GPU 常驻 + 线程安全加载/卸载 + GPU OOM 报告）、AudioPlaybackDevice（sounddevice 音频输出 + 打断停止）、make_sovits_synthesizer/make_audio_playback_starter（SpeechPlaybackQueue 兼容回调工厂）；VTubeStudioDriver（WebSocket 连接/认证 + talk gate TALK_START/TALK_END + emotion_tag→expression_id 确定性映射 + barge-in 恢复 reset_to_neutral + 断连降级队列）；新增 6 个 TTS/VTS 硬件层错误码（TTS_MODEL_UNAVAILABLE/TTS_GPU_OOM/TTS_SYNTH_TIMEOUT/VTS_CONNECTION_FAILED/VTS_API_ERROR/VTS_EXPRESSION_TIMEOUT）；新增 4 个 fixtures（合成成功 + 合成失败 + VTS 断连 + barge-in 恢复）；Edge/Host 表情映射表一致性已验证。TASK-0080 新增 VTS WebSocket 自动重连机制：ReconnectConfig（max_attempts/base_delay_s/max_delay_s/auto_reconnect 可配置）、_reconnect_loop（指数退避 1s→2s→4s→8s→16s 上限，最多 10 次）、trigger_reconnect（非阻塞后台线程启动）、重连成功自动恢复 neutral 表情并上报 VTS_RECONNECTED、重连耗尽进入 DISCONNECTED 并上报 VTS_RECONNECT_EXHAUSTED、shutdown_event 可中断重连、health 快照含 reconnect 统计；新增 2 个重连错误码（VTS_RECONNECTED/VTS_RECONNECT_EXHAUSTED）、2 个 fixtures（成功/耗尽）。T-VTS-Reconnect-01（30+ 项）全部通过；T-TTS-HW-01（25 项）+ T-VTS-HW-01（30 项）+ T-Voice-02/T-Avatar-01（8 项）无回归。
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
- DevTasks（TASK）：TASK-0072,TASK-0079
- 验收测试（T-xx）：T-Obs-01, T-Obs-03, T-RPC-Event-01
- 当前进展（2026-03-02）：已落地跨端时间线聚合模块 `host_brain/observability/cross_timeline.py`：EdgeEventReport（Edge 标准化上报结构）、CrossTimelineAggregator（Host 审计+Edge 上报合并、按 ts_ms 排序、source=host/edge 标记）、时间漂移检测（可配置容忍阈值 DEFAULT_CLOCK_DRIFT_TOLERANCE_MS=5000ms）、Edge 缺失时 HOST_ONLY 降级+TIMELINE_EDGE_EVENTS_MISSING 告警、漂移超限时 PARTIAL 状态+TIMELINE_CLOCK_DRIFT_DETECTED 告警、按 session_id 备选关联、无效 Edge 事件静默过滤、跨 request_id 事件隔离。TASK-0079 新增 Edge→Host RPC 事件批量上报通道（`edge_client/rpc/event_reporter.py` + `POST /v1/edge/events`），支持事件 Schema 校验、批量写入审计链、网络中断本地缓存（最多 1000 条/10MB）、恢复后自动补发、指数退避重试。新增 3 个错误码（RPC_EVENT_SCHEMA_INVALID/RPC_EVENT_BATCH_PARTIAL_FAIL/RPC_EVENT_NETWORK_UNAVAILABLE）；新增 3 个 fixtures（正常/部分失败/网络中断）。T-RPC-Event-01（30+ 项）全部通过；T-Obs-03（27 项）无回归。
- 覆盖状态：DONE

## R-39
- 系统级需求：Rate Limit 持久化——R4 滑动窗口可插拔存储适配（InMemory/SQLite/Redis），Redis 不可用时稳定降级，rule_trace 标注 backend
- PRD 需求编号（FR）：FR-GOV-001
- 来源：DevTasks TASK-0073（Rate Limit 持久化）
- DevSpec 落点：DevSpec 6
- DevTasks（TASK）：TASK-0073
- 验收测试（T-xx）：T-Gov-01, T-Gov-R4-01
- 当前进展（2026-03-02）：已落地 Rate Limit 可插拔存储层 `host_brain/governor/rate_limit_store.py`：RateLimitStore 抽象接口（incr/get_count/reset/ttl）、InMemoryRateLimitStore（滑动窗口 dict 实现，线程安全）、SQLiteRateLimitStore（WAL 模式持久化）、RedisRateLimitStore（Sorted Set 集群限流）、DegradingRateLimitStore（主后端不可用自动降级到 InMemory）、RateLimitChecker（封装 check/check_only/reset）、create_rate_limit_store 工厂函数。R4 Gate 增强输出 backend/degraded/degraded_from 信息到 threshold_summary/snapshot_summary，向后兼容（不传新字段时行为不变）。新增 HOST_RATE_LIMIT_BACKEND/HOST_RATE_LIMIT_WINDOW_MS/HOST_RATE_LIMIT_MAX_REQUESTS 三个配置项。新增 2 个错误码（RATE_LIMIT_BACKEND_DEGRADED/RATE_LIMIT_BACKEND_UNAVAILABLE）；新增 3 个 fixtures（窗口滑动/窗口过期/并发竞争）。InMemory 与 SQLite 同输入同结果已验证。T-Gov-R4-01（47 项）全部通过；T-Gov-01（2 项）无回归。
- 覆盖状态：DONE

## R-40
- 系统级需求：Camera 多检测器注册与多模态事件聚合——检测器注册表 + 并行执行 + 聚合输出 + 部分降级稳定分类
- PRD 需求编号（FR）：FR-VISION-004
- 来源：DevSpec 8.12 / TASK-0034
- DevSpec 落点：DevSpec 8.12
- DevTasks（TASK）：TASK-0074
- 验收测试（T-xx）：T-Vision-02, T-Vision-03
- 当前进展（2026-03-02）：已落地多检测器注册表（DetectorConfig: detector_id/type/model_id/confidence_threshold/enabled/timeout_ms）、DetectorRegistry（线程安全注册/注销/列表/快照）、MultiDetectorAggregator（并行执行 + 结果聚合：OK/PARTIAL/ALL_FAILED 三态）；CameraRecognitionServiceContract 新增 detect_multi() 方法与原 detect() 完全兼容；新增 3 个错误码（VISION_DETECTOR_UNAVAILABLE/VISION_DETECTOR_TIMEOUT/VISION_DETECTOR_ALL_FAILED）；新增 3 个 fixtures（多检测器混合/部分不可用/全不可用）。T-Vision-03（25 项：注册表 CRUD 7项 + 聚合器 9项 + 集成 6项 + 回归 3项）全部通过；T-Vision-02（原有）无回归。
- 覆盖状态：DONE

## R-41
- 系统级需求：Airlock DRAFT_CODE 沙箱执行——隔离运行环境（subprocess + 临时目录）+ 超时强杀 + 资源限制 + 结构化结果审计
- PRD 需求编号（FR）：FR-AIRLOCK-002
- 来源：DevSpec 8.7 / TASK-0016
- DevSpec 落点：DevSpec 8.7
- DevTasks（TASK）：TASK-0075
- 验收测试（T-xx）：T-39, T-40, T-Sandbox-01
- 当前进展（2026-03-02）：已落地 DRAFT_CODE 沙箱执行器 `host_brain/airlock/sandbox.py`：SandboxConfig（timeout_s/max_stdout_bytes/max_stderr_bytes/max_code_bytes/python_executable/allow_network/allow_fs_write/enabled）、SandboxExecResult（sandbox_id/status/exit_code/stdout/stderr/duration_ms/reason_code/evidence_ref/resource_usage/code_digest/truncated_stdout/truncated_stderr）、SandboxExecutor（execute 方法：subprocess 隔离 + 临时目录 + 超时强杀 + 输出截断 + 审计写入）。新增 sandbox_exec_log 审计表（15 字段 + 4 索引）到 SQLiteLedger。新增 4 个错误码（SANDBOX_UNAVAILABLE/SANDBOX_TIMEOUT/SANDBOX_RESOURCE_EXCEEDED/SANDBOX_EXEC_FAILED）。新增 4 个 fixtures（正常执行/超时/恶意代码/资源超限）。T-Sandbox-01（20 项）全部通过；T-39/T-40（原有）无回归。
- 覆盖状态：DONE

## R-42
- 系统级需求：Host VTS 通道 DEGRADED 状态——Edge 不可达/映射未知时 neutral 降级备选 + 审计可回溯
- PRD 需求编号（FR）：FR-AVATAR-001
- 来源：DevTasks TASK-0082（VTS DEGRADED 扩展）
- DevSpec 落点：DevSpec 8.11.2
- DevTasks（TASK）：TASK-0082
- 验收测试（T-xx）：T-Avatar-01, T-VTS-HW-01, T-E2E-WeChat-01, T-VTS-Degraded-01
- 当前进展（2026-03-02）：已增强 Host VTS 通道 `resolve_vts_expression`：新增 `edge_available` 参数，Edge 不可达时返回 `status=DEGRADED` + neutral 备选表情（exp_neutral_01）+ `VTS_DEGRADED_FALLBACK` reason_code；未知 emotion_tag 映射时同样降级到 neutral（而非原先直接 REJECTED）；`success=True` 表示有有效 fallback 输出；新增 `STATUS_DEGRADED` 常量、`DEGRADED_FALLBACK_EXPRESSION_TAG/ID` 常量；降级详情写入 `avatar_action_log` 可审计回溯；Edge 不可达优先级高于 free action 检查；已知 tag/空 tag/free action 行为完全不变；talk gate 不受影响。新增 1 个错误码（VTS_DEGRADED_FALLBACK）、2 个 fixtures（Edge 不可达/未知 tag 降级）。T-VTS-Degraded-01（30+ 项）全部通过。
- 覆盖状态：DONE

## R-43
- 系统级需求：LangGraph 读取 model_plane 并统一下发 LLM 参数——model_profile_id 绑定、model_node 自动解析、model_control 输出
- PRD 需求编号（FR）：FR-CORE-001, FR-CORE-004
- 来源：DevTasks TASK-0057
- DevSpec 落点：DevSpec 6/8.9
- DevTasks（TASK）：TASK-0057
- 验收测试（T-xx）：T-Orch-01, T-Orch-02
- 当前进展（2026-03-02）：graph_model_resolve.py 已实现 model_plane 配置加载（model_profiles.yaml/node_registry.yaml）→ model_node 自动解析 → model_profile_id 绑定到 graph_config → model_control 输出。T-Orch-02（model_plane profile loading / missing profile rejected）已在 test_orchestration_graph_contract.py 中通过。
- 覆盖状态：DONE

## R-44
- 系统级需求：参数签名防漂移——build_model_param_hash + expected vs actual 校验 + 漂移降级
- PRD 需求编号（FR）：FR-CORE-001, FR-CORE-004
- 来源：DevTasks TASK-0058
- DevSpec 落点：DevSpec 6/8.9
- DevTasks（TASK）：TASK-0058
- 验收测试（T-xx）：T-Orch-03
- 当前进展（2026-03-02）：graph_contract.py 的 build_model_param_hash 基于 provider/model_id/temperature/max_output_tokens/timeout_ms 产出确定性哈希；graph_executor.py 在执行时比对 expected_param_hash vs actual_param_hash，漂移时返回 STATUS_DEGRADED + ORCH_PARAM_DRIFT，并写入 model_control_log。T-Orch-03（param drift degraded）已通过。
- 覆盖状态：DONE

## R-45
- 系统级需求：确定性 profile→node 选择器与环境约束矩阵——node_allowlist + env + 候选筛选
- PRD 需求编号（FR）：FR-CORE-001
- 来源：DevTasks TASK-0059
- DevSpec 落点：DevSpec 6/8.9/13
- DevTasks（TASK）：TASK-0059
- 验收测试（T-xx）：T-Orch-04
- 当前进展（2026-03-02）：graph_model_resolve.py 的 _pick_candidate_node 已实现 env/node_allowlist 过滤 + 确定性选择（按 node_id 排序取首个），无候选时拒绝 ORCH_NODE_MISSING。T-Orch-04（deterministic selector + no candidate rejected）已通过。
- 覆盖状态：DONE

## R-46
- 系统级需求：模型调用与 Governor/Token 审批锚点串联——governance_refs 提取 + require_approval_token 检查
- PRD 需求编号（FR）：FR-CORE-001, FR-TOKEN-001
- 来源：DevTasks TASK-0060
- DevSpec 落点：DevSpec 5/6
- DevTasks（TASK）：TASK-0060
- 验收测试（T-xx）：T-Orch-05
- 当前进展（2026-03-02）：graph_executor.py 的 extract_governance_refs 从 payload 提取 intent_id/approval_token_ref/evidence_ref 并注入 model_control 输出；require_approval_token=True 时检查 approval_token_ref，缺失则返回 SKILL_TOKEN_MISSING 降级。T-Orch-05 已通过。
- 覆盖状态：DONE

## R-47
- 系统级需求：model_control 审计入库与 request 级回放透出——model_control_log 完整性 + trace_request 聚合
- PRD 需求编号（FR）：FR-AUDIT-001
- 来源：DevTasks TASK-0061
- DevSpec 落点：DevSpec 8.1/9
- DevTasks（TASK）：TASK-0061
- 验收测试（T-xx）：T-Audit-06, T-Replay-02
- 当前进展（2026-03-02）：model_control_log 写入 request_id/model_profile_id/provider/model_id/actual_param_hash/expected_param_hash/drift_detected/reason_code/approval_token_ref/evidence_ref；trace_request 聚合 model_control_log 条目支持 request 级回放。新增 T-Audit-06（6 项）+ T-Replay-02（4 项），全部通过。
- 覆盖状态：DONE

## R-48
- 系统级需求：model_plane 版本签名与发布门禁联动——release checklist 含 model_plane digest + 版本号可解析
- PRD 需求编号（FR）：NFR-MAINT-001
- 来源：DevTasks TASK-0062
- DevSpec 落点：DevSpec 6/10
- DevTasks（TASK）：TASK-0062
- 验收测试（T-xx）：T-Flow-03, T-Release-01
- 当前进展（2026-03-02）：release_manifest.py 已支持 include_model_plane_assets=True 时纳入 model_plane 资产并计算 sha256 checksum。新增 T-Flow-03（5 项），全部通过。
- 覆盖状态：DONE

## R-49
- 系统级需求：编排主控全链路演练与故障注入矩阵——五类核心故障场景可复现 + 审计可回放
- PRD 需求编号（FR）：FR-CORE-004, FR-AUDIT-001
- 来源：DevTasks TASK-0063
- DevSpec 落点：DevSpec 6/10/14.5
- DevTasks（TASK）：TASK-0063
- 验收测试（T-xx）：T-Drill-02, T-EnvReady-03, T-Orch-06
- 当前进展（2026-03-02）：覆盖 5 类核心故障场景（profile 缺失/无候选/param drift/token missing/provider down）。新增 T-Drill-02（5 项）+ T-EnvReady-03（2 项）+ T-Orch-06（2 项），全部通过。
- 覆盖状态：DONE

## R-50
- 系统级需求：沙箱安全升级 subprocess→Docker 容器级隔离——Docker 后端 + 自动降级 + 安全标志 + 审计串联
- PRD 需求编号（FR）：FR-AIRLOCK-002
- 来源：DevTasks TASK-0077
- DevSpec 落点：DevSpec 8.7
- DevTasks（TASK）：TASK-0077
- 验收测试（T-xx）：T-39, T-40, T-Sandbox-01, T-Sandbox-Docker-01
- 当前进展（2026-03-02）：新增 docker_sandbox.py（Docker 容器执行）；SandboxConfig 新增 isolation_backend；Docker 不可用自动降级到 subprocess + SANDBOX_CONTAINER_UNAVAILABLE。新增 1 个错误码 + 2 个 fixtures。T-Sandbox-Docker-01（21 项）全部通过。
- 覆盖状态：DONE

## R-51
- 系统级需求：跨端时间线 SQL 优化——按时间窗口过滤 + 分页查询
- PRD 需求编号（FR）：FR-OBS-001
- 来源：DevTasks TASK-0078
- DevSpec 落点：DevSpec 10/11
- DevTasks（TASK）：TASK-0078
- 验收测试（T-xx）：T-Obs-03, T-Timeline-Perf-01
- 当前进展（2026-03-02）：fetch_by_time_range + count_by_time_range 已实现。test_timeline_perf_contract.py 已覆盖。
- 覆盖状态：DONE

## R-52
- 系统级需求：部署文档 + 环境清单补充
- PRD 需求编号（FR）：NFR-MAINT-001
- 来源：DevTasks TASK-0083
- DevSpec 落点：DevSpec 10/13
- DevTasks（TASK）：TASK-0083
- 验收测试（T-xx）：无（文档更新）
- 当前进展（2026-03-02）：ENV_EXTERNAL_SERVICES_CHECKLIST.md v1.1 已更新：新增 Docker 沙箱/VTS DEGRADED/Model Plane 发布门禁/跨端时间线优化。
- 覆盖状态：DONE

## R-53
- 系统级需求：Host 定时调度器——系统发起 Intent 的统一触发框架（cron/interval/once），sys_mode 门控，连续失败自动暂停
- PRD 需求编号（FR）：FR-SCHED-001~002
- 来源：PRD 4.20
- DevSpec 落点：DevSpec 15.1
- DevTasks（TASK）：TASK-0091
- 验收测试（T-xx）：T-Sched-01~03
- 当前进展（2026-03-03）：TASK-0091 已完成：NatsumeScheduler 调度器主体（cron/interval/once 三种触发模式）+ SchedulerTaskRegistry + SchedulerHealthReporter + sys_mode×priority 门控矩阵 + 连续失败自动暂停 + scheduler_event_log 审计表 + T-Sched-01~03 contract 测试 43 项全部通过。
- 覆盖状态：DONE

## R-54
- 系统级需求：桌面留言——安全基目录文件创建/更新/撤销 + 路径穿越拒绝 + 内容 QA
- PRD 需求编号（FR）：FR-DESKTOP-001~002
- 来源：PRD 4.21
- DevSpec 落点：DevSpec 15.2
- DevTasks（TASK）：TASK-0092
- 验收测试（T-xx）：T-Desktop-01~02
- 当前进展（2026-03-03）：TASK-0092 已完成：desktop_note Skill（create/update/delete + quarantine 软删除 + undo 撤销）+ 路径穿越拒绝 + symlink 拦截 + Gaming 门控 + QA 内容检查 + manifest 产出 + T-Desktop-01~02 contract 测试 21 项全部通过。
- 覆盖状态：DONE

## R-55
- 系统级需求：梦境生成——Sleep/空闲触发 + Episodic 当日回顾 + LLM 创造性叙事 + NARRATIVE_ONLY 标签 + 非事实约束
- PRD 需求编号（FR）：FR-DREAM-001~002
- 来源：PRD 4.22
- DevSpec 落点：DevSpec 15.3
- DevTasks（TASK）：TASK-0093
- 验收测试（T-xx）：T-Dream-01~02
- 当前进展（2026-03-03）：TASK-0093 已完成：dream_generator Skill（Sleep/空闲触发 + Episodic 当日回顾 + LLM 梦境叙事生成 + NARRATIVE_ONLY 标签写入 Episodic）+ 条件不满足/无素材跳过/LLM降级 + T-Dream-01~02 contract 测试 12 项全部通过。
- 覆盖状态：DONE

## R-56
- 系统级需求：日记生成——每日定时汇总对话 + 双写文件/Episodic + 无对话不伪造 + 可选 Evolution 提案
- PRD 需求编号（FR）：FR-DIARY-001~002
- 来源：PRD 4.23
- DevSpec 落点：DevSpec 15.4
- DevTasks（TASK）：TASK-0094
- 验收测试（T-xx）：T-Diary-01~02
- 当前进展（2026-03-03）：TASK-0094 已完成：diary_writer Skill（定时汇总对话 + LLM 日记叙事生成 + 文件/Episodic 双写 + 无对话不伪造）+ 日期校验/LLM降级/无素材跳过 + T-Diary-01~02 contract 测试 12 项全部通过。
- 覆盖状态：DONE

## R-57
- 系统级需求：主动对话——条件策略触发（空闲/Bond/时段）+ 频率限制 + 内容 QA + 推送失败静默降级
- PRD 需求编号（FR）：FR-PROACTIVE-001~002
- 来源：PRD 4.24
- DevSpec 落点：DevSpec 15.5
- DevTasks（TASK）：TASK-0095
- 验收测试（T-xx）：T-Proactive-01~02
- 当前进展（2026-03-03）：TASK-0095 已完成：proactive_talk Skill（空闲阈值检查 + 时段检测 + LLM 问候生成 + UI 推送 + 降级模板）+ Gaming/Focus/Maintenance 门控 + 自定义阈值 + T-Proactive-01~02 contract 测试 13 项全部通过。
- 覆盖状态：DONE

## R-58
- 系统级需求：Focus Mode——Normal 子模式状态扩展 + 中断型能力暂停 + 被动响应保留 + 进退可审计
- PRD 需求编号（FR）：FR-FOCUS-001
- 来源：PRD 4.25
- DevSpec 落点：DevSpec 15.6
- DevTasks（TASK）：TASK-0096
- 验收测试（T-xx）：T-Focus-01
- 当前进展（2026-03-03）：TASK-0096 已完成：focus_mode Skill（enter/exit 操作 + sys_mode 切换回调 + 过期时间 + 原因记录）+ 非 Normal 拒绝/重复进入拒绝/未激活退出拒绝 + T-Focus-01 contract 测试 14 项全部通过。
- 覆盖状态：DONE

## R-59
- 系统级需求：M4 体验包 E2E 集成——Scheduler→Skill→Audit 全链闭环覆盖 5 个体验功能
- PRD 需求编号（FR）：FR-SCHED-001~002, FR-DESKTOP-001~002, FR-DREAM-001~002, FR-DIARY-001~002, FR-PROACTIVE-001~002, FR-FOCUS-001
- 来源：PRD 4.20~4.25
- DevSpec 落点：DevSpec 15
- DevTasks（TASK）：TASK-0097
- 验收测试（T-xx）：T-Sched-01~03, T-Desktop-01~02, T-Dream-01~02, T-Diary-01~02, T-Proactive-01~02, T-Focus-01
- 当前进展（2026-03-03）：TASK-0097 已完成：M4 E2E 集成测试（Scheduler→Skill→Audit 完整链路 + 跨 Skill 联动 Focus 门控 + Health 快照反映真实状态）12 项 E2E 测试全部通过；136 项 M4 全量回归 0.34s 内通过。
- 覆盖状态：DONE

## R-60
- 系统级需求：Flow 配置资产版本化与追溯——导出归档 + active.pointer 指针 + Trace 索引一致性
- PRD 需求编号（FR）：FR-FLOW-001
- 来源：PRD 4.2
- DevSpec 落点：DevSpec 1.5
- DevTasks（TASK）：TASK-0000,TASK-0002,TASK-0062
- 验收测试（T-xx）：T-Flow-01,T-Flow-02,T-Flow-03
- 当前进展（2026-03-03）：TASK-0000（目录骨架+指针文件）、TASK-0002（Flow 归档+一致性断言）、TASK-0062（release_manifest 纳入 model_plane 资产）已完成；T-Flow-01~03 回归通过。
- 覆盖状态：DONE

## R-61
- 系统级需求：叙事与事实分离——叙事层不得自造执行事实；执行事实只来自审计链摘要字段
- PRD 需求编号（FR）：FR-CORE-003
- 来源：PRD 4.1
- DevSpec 落点：DevSpec 9（QA 合规）
- DevTasks（TASK）：TASK-0003,TASK-0012
- 验收测试（T-xx）：T-LLM-01,T-67~T-71
- 当前进展（2026-03-03）：TASK-0003（LLM 输出契约 + lint）和 TASK-0012（QA 合规模块：伪执行宣告拦截 + 一次重写/降级）已完成；回归集中完成性表述无证据必拦截，T-67~T-71 通过。
- 覆盖状态：DONE

## R-62
- 系统级需求：Skills 统一执行入口 + 幂等——loader/registry 强制 token 校验 + 同 intent_id 不重复副作用
- PRD 需求编号（FR）：FR-SKILL-002,FR-SKILL-003
- 来源：PRD 4.6.1
- DevSpec 落点：DevSpec 6/8.1
- DevTasks（TASK）：TASK-0010
- 验收测试（T-xx）：T-Skill-01,T-15
- 当前进展（2026-03-03）：TASK-0010 已完成：loader 强制 token 校验、静态扫描+运行时断言双生效、同 intent_id 幂等。T-Skill-01/T-15 回归通过。
- 覆盖状态：DONE

## R-63
- 系统级需求：Episodic 写入门禁（evidence_refs 必需）+ 生命周期审计（merge/decay/delete 可回放）
- PRD 需求编号（FR）：FR-MEM-001,FR-MEM-002
- 来源：PRD 4.11
- DevSpec 落点：DevSpec 8.3
- DevTasks（TASK）：TASK-0014
- 验收测试（T-xx）：T-Mem-01,T-Mem-02
- 当前进展（2026-03-03）：TASK-0014 已完成：无 evidence_refs 写入失败且可解释；merge/decay/delete 审计事件均可回放。T-Mem-01/T-Mem-02 回归通过。
- 覆盖状态：DONE

## R-64
- 系统级需求：阶段化状态机与降级——S0~S8 每阶段有失败分支与 reason_code；降级最多追问 1 个关键问题
- PRD 需求编号（FR）：FR-UX-001
- 来源：PRD 4.15
- DevSpec 落点：DevSpec 2/9
- DevTasks（TASK）：TASK-0012,TASK-0023
- 验收测试（T-xx）：T-67~T-71
- 当前进展（2026-03-03）：TASK-0012（QA 合规）+ TASK-0023（冷启动降级）已完成：注入任意阶段异常输出不崩，quality.stage 反映失败点。T-67~T-71 回归通过。
- 覆盖状态：DONE

## R-65
- 系统级需求：Host 最小叙事模板库——LLM 不可用/不合规时仍输出最低风格且不伪执行的模板
- PRD 需求编号（FR）：FR-UX-002
- 来源：PRD 4.15
- DevSpec 落点：DevSpec 2.1/9
- DevTasks（TASK）：TASK-0003,TASK-0012,TASK-0023
- 验收测试（T-xx）：T-LLM-01,T-67~T-71
- 当前进展（2026-03-03）：关闭 LLM 或强制超时时，Host 输出合规模板（克制且不暴露组件名、不产生执行 intent）。TASK-0003/0012/0023 已覆盖。T-LLM-01/T-67~T-71 回归通过。
- 覆盖状态：DONE

## R-66
- 系统级需求：Gaming 模式视觉链路门控——Gaming 下禁止屏幕监控/OCR/ClawdBot UI 自动化链路
- PRD 需求编号（FR）：FR-MODE-003
- 来源：PRD 4.13
- DevSpec 落点：DevSpec 8.12
- DevTasks（TASK）：TASK-0033,TASK-0034
- 验收测试（T-xx）：T-VisionGate-01
- 当前进展（2026-03-03）：TASK-0033/0034 已完成：进入 Gaming 后屏幕监控/OCR/Clawd UI 动作均被稳定拒绝或降级；退出后恢复且审计可回放。T-VisionGate-01 回归通过。
- 覆盖状态：DONE

## R-67
- 系统级需求：ClawdBot 必须依赖结构化视觉输入——缺结构化视觉输入时不得进入 UI 行为路径；置信度不足必须熔断
- PRD 需求编号（FR）：FR-VISION-005
- 来源：PRD 4.9
- DevSpec 落点：DevSpec 6.3/8.12
- DevTasks（TASK）：TASK-0022
- 验收测试（T-xx）：T-41~T-43
- 当前进展（2026-03-03）：TASK-0022 已完成：ClawdBot 计划草案消费结构化 OCR/检测结果；缺输入时拒绝进入执行路径；置信度不足熔断可审计。T-41~T-43 回归通过。
- 覆盖状态：DONE

## R-68
- 系统级需求：World Canon 版本化、更新可审计、注入分槽隔离（authoritative.world_canon 独立槽位）
- PRD 需求编号（FR）：FR-WCANON-002,FR-WCANON-003
- 来源：PRD 4.12
- DevSpec 落点：DevSpec 8.5/7
- DevTasks（TASK）：TASK-0013,TASK-0004
- 验收测试（T-xx）：T-WCanon-01~03,T-CP-01
- 当前进展（2026-03-03）：TASK-0013（Canon 版本化+变更留痕）+ TASK-0004（ContextPack 分槽隔离）已完成：World Canon 更新可在 canon_change_log 中回放；注入以独立 authoritative.world_canon 槽位、evidence_level=CANON_REFERENCE_ONLY。T-WCanon-01~03/T-CP-01 回归通过。
- 覆盖状态：DONE

## R-69
- 系统级需求：本地优先与最小暴露——默认本地运行；对外通信经 AIRLOCK；敏感字段不出站
- PRD 需求编号（FR）：NFR-SEC-001
- 来源：PRD §5（NFR）
- DevSpec 落点：DevSpec 8.7
- DevTasks（TASK）：TASK-0016,TASK-0038
- 验收测试（T-xx）：T-39,T-40
- 当前进展（2026-03-03）：TASK-0016（AIRLOCK 出站净化/入站隔离）+ TASK-0038 已完成：出站净化报告覆盖率 100%，抽检出站 payload 不含硬状态原值与规则细节。T-39/T-40 回归通过。
- 覆盖状态：DONE

## R-70
- 系统级需求：降级可用性——依赖故障不崩溃；输出结构稳定；关键链路可观测
- PRD 需求编号（FR）：NFR-REL-001
- 来源：PRD §5（NFR）
- DevSpec 落点：DevSpec 1.2.1/2.1/10
- DevTasks（TASK）：TASK-0023,TASK-0048,TASK-0056
- 验收测试（T-xx）：T-67~T-71,T-Drill-01,T-Svc-01,T-EnvReady-01
- 当前进展（2026-03-03）：TASK-0023（冷启动可用性）+ TASK-0048（生产演练包）+ TASK-0056（环境联调就绪报告）已完成：故障注入矩阵（DB/Neo4j/Chroma/Cloud Exocortex/Edge 超时或不可用）全通过。
- 覆盖状态：DONE

## R-71
- 系统级需求：响应时延预算——普通对话可接受；执行链路可解释；超时可控可降级
- PRD 需求编号（FR）：NFR-PERF-001
- 来源：PRD §5（NFR）
- DevSpec 落点：DevSpec 16（性能预算与时延可观测）
- DevTasks（TASK）：TASK-0098
- 验收测试（T-xx）：T-Perf-01
- 当前进展（2026-03-03）：TASK-0098 已完成：性能预算基准测试（Scheduler trigger <5ms + Skill execute <10ms + Health snapshot <2ms/100任务 + Registry ops <1ms）共 9 项性能回归测试全部通过。
- 覆盖状态：DONE

## R-72
- 系统级需求：可回归与可迁移——需求编号→测试→日志字段一致；升级不破坏审计可回放
- PRD 需求编号（FR）：NFR-MAINT-001
- 来源：PRD §5（NFR）
- DevSpec 落点：DevSpec 11/14.6（合规测试与验收测试落地方式）
- DevTasks（TASK）：TASK-0043,TASK-0045,TASK-0047
- 验收测试（T-xx）：T-Migrate-01,T-CI-01,T-Release-01
- 当前进展（2026-03-03）：TASK-0043（迁移执行器+schema 版本守卫）+ TASK-0045（四合一 CI 门禁）+ TASK-0047（发布清单+回滚映射）已完成；T-Migrate-01/T-CI-01/T-Release-01 回归通过；版本升级后回放旧 intent 仍可得到一致裁决或明确迁移提示。
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
