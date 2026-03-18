受众：MASTER
标题：需求可追溯性矩阵（Requirements Trace）

版本：v1.6

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

---

## R-73
- 系统级需求：可观测指标面板——Governor 裁决计数、伪执行拦截、检索 p50/p95、Hard Snapshot staleness、最近链路摘要，所有指标可通过 request_id 串联到 Ledger
- PRD 需求编号（FR）：FR-OBS-001
- 来源：PRD §4.16
- DevSpec 落点：DevSpec §17.1.1
- DevTasks（TASK）：TASK-0099
- 验收测试（T-xx）：T-OBS-01
- 当前进展（2026-03-03）：host_brain/obs/metrics.py（MetricsCollector）+ routes.py 已实现，T-OBS-01 全部通过（11 tests），914 passed。
- 覆盖状态：DONE

## R-74
- 系统级需求：可观测模式差异——dev 模式返回 debug_trace（LLM intent_draft/Governor 规则命中/Skill 参数），prod 模式不返回；切换不影响裁决与审计事实
- PRD 需求编号（FR）：FR-OBS-002
- 来源：PRD §4.16
- DevSpec 落点：DevSpec §17.1.2
- DevTasks（TASK）：TASK-0100
- 验收测试（T-xx）：T-OBS-02
- 当前进展（2026-03-03）：ObsMode 枚举 + get_obs_mode() + dev/prod 模式切换已实现，T-OBS-02 全部通过，切换不影响裁决与审计事实。
- 覆盖状态：DONE

## R-75
- 系统级需求：MCP ToolMeta 注册表——provider 工具先同步元数据（access_level/risk_level/reversible），变更写 mcp_sync_log 可回放，不得直接调用未注册工具
- PRD 需求编号（FR）：FR-MCP-001
- 来源：PRD §4.18
- DevSpec 落点：DevSpec §17.2.1
- DevTasks（TASK）：TASK-0101
- 验收测试（T-xx）：T-MCP-01~03
- 当前进展（2026-03-03）：host_brain/mcp/tool_registry.py（MCPToolRegistry + mcp_sync_log diff）已实现，T-MCP-01 全部通过，变更留痕可回放。
- 覆盖状态：DONE

## R-76
- 系统级需求：MCP ToolBroker 单入口——WRITE 无 token 拒绝，provider 不可达降级，调用链完整审计（mcp_exec_log）
- PRD 需求编号（FR）：FR-MCP-001
- 来源：PRD §4.18
- DevSpec 落点：DevSpec §17.2.2
- DevTasks（TASK）：TASK-0102
- 验收测试（T-xx）：T-MCP-01~03
- 当前进展（2026-03-03）：host_brain/mcp/tool_broker.py（MCPToolBroker 单入口）已实现，WRITE 无 token 拒绝/provider 不可达降级/完整审计，T-MCP-02~03 全部通过。
- 覆盖状态：DONE

## R-77
- 系统级需求：ASR 事件化上送——confidence/is_final/alternates 字段，低置信度（<0.6）标注 low_confidence=true，事件写 voice_event_log
- PRD 需求编号（FR）：FR-VOICE-001
- 来源：PRD §4.19
- DevSpec 落点：DevSpec §17.3.1
- DevTasks（TASK）：TASK-0103
- 验收测试（T-xx）：T-VOICE-01
- 当前进展（2026-03-03）：VoiceRpcContract.asr_chunk() 已实现低置信度标注，T-VOICE-01 全部通过（6 tests），voice_event_log 审计可回放。
- 覆盖状态：DONE

## R-78
- 系统级需求：TTS 合成播报队列——按 priority 排序，interruptible=true 可被 Barge-in 打断，播报完成/打断后写 voice_event_log
- PRD 需求编号（FR）：FR-VOICE-002
- 来源：PRD §4.19
- DevSpec 落点：DevSpec §17.3.2
- DevTasks（TASK）：TASK-0104
- 验收测试（T-xx）：T-VOICE-02
- 当前进展（2026-03-03）：SpeechPlaybackQueue 优先级队列 + 打断逻辑已实现，T-VOICE-02 全部通过（5 tests），TTS_INTERRUPTED 审计可回放。
- 覆盖状态：DONE

## R-79
- 系统级需求：Barge-in 打断（ASR 开口→tts_interrupt，不构成执行事实）+ Edge 兜底小模型（Host 不可用时 draft_only 输出，默认卸载按需加载）
- PRD 需求编号（FR）：FR-VOICE-003,FR-VOICE-004
- 来源：PRD §4.19
- DevSpec 落点：DevSpec §17.3.3~17.3.4
- DevTasks（TASK）：TASK-0105
- 验收测试（T-xx）：T-VOICE-03,T-VOICE-04
- 当前进展（2026-03-03）：SpeechPlaybackQueue.handle_barge_in() + EdgeFallbackRpcContract（draft_only/快速失败）已实现，T-VOICE-03/04 全部通过（8 tests）。
- 覆盖状态：DONE

## R-80
- 系统级需求：Edge 资源闸门——P1（ASR）> P2（播放）> P3（TTS 合成）> P4（兜底 LLM）优先级矩阵，低优先级被抢占写 RESOURCE_GATE_PREEMPTED，健康快照可观测
- PRD 需求编号（FR）：FR-VOICE-005
- 来源：PRD §4.19
- DevSpec 落点：DevSpec §17.3.5
- DevTasks（TASK）：TASK-0106
- 验收测试（T-xx）：T-VOICE-05
- 当前进展（2026-03-03）：VoiceResourceGate P1>P2>P3>P4 优先级矩阵 + 抢占写 RESOURCE_GATE_PREEMPTED + 健康快照已实现，T-VOICE-05 全部通过（6 tests）。
- 覆盖状态：DONE

---

## M8 运行时集成闭环（R-81~R-84）

## R-81
- 系统级需求：运行时脚本集成——start_host_side.ps1 启动后探测 /obs/metrics 与 MCPToolRegistry 健康状态，结果写入启动 JSON（obs_status/mcp_registry_status/startup_warnings）
- PRD 需求编号（FR）：NFR-REL-001
- 来源：PRD §5（NFR）；PRD M8
- DevSpec 落点：DevSpec §18.1.1
- DevTasks（TASK）：TASK-0107
- 验收测试（T-xx）：T-RT-01
- 当前进展（2026-03-03）：start_host_side.ps1 新增 /obs/metrics + /health/mcp 探测，obs_status/mcp_registry_status/startup_warnings 写入启动 JSON；errors.py 新增 STARTUP_OBS_DEGRADED/STARTUP_MCP_DEGRADED/E2E_SMOKE_FAILED/PERF_BASELINE_STALE；T-RT-01 全部通过（26 tests）。
- 覆盖状态：DONE

## R-82
- 系统级需求：端到端 Smoke Test——冷启动最小闭环（无外部依赖）+ 执行链路（approval_token+undo）+ 故障注入矩阵（Neo4j/Chroma/LLM/Edge 各自不可用时输出结构稳定）
- PRD 需求编号（FR）：FR-CORE-004；NFR-REL-001
- 来源：PRD §4.1；PRD §5；PRD M8
- DevSpec 落点：DevSpec §18.2.1
- DevTasks（TASK）：TASK-0108
- 验收测试（T-xx）：T-E2E-01~03
- 当前进展（2026-03-03）：T-E2E-01（冷启动最小闭环）、T-E2E-02（approval_token+undo 执行链路）、T-E2E-03（故障注入矩阵 4 场景）全部通过（22 tests）；fixtures: cold_start_input.json / exec_chain_input.json / fault_injection_matrix.json。
- 覆盖状态：DONE

## R-83
- 系统级需求：性能基线采集——/obs/metrics 新增 latency_baseline 字段（retrieval/governor/skill_exec 的 p50/p95），采样数据过期时标注 PERF_BASELINE_STALE
- PRD 需求编号（FR）：NFR-PERF-001
- 来源：PRD §5（NFR）；PRD M8
- DevSpec 落点：DevSpec §18.3.1
- DevTasks（TASK）：TASK-0109
- 验收测试（T-xx）：T-PERF-01
- 当前进展（2026-03-03）：MetricsCollector.collect_latency_baseline() 实现 retrieval/governor/skill_exec p50/p95 采集；collect() 返回值新增 latency_baseline 字段；T-PERF-01 全部通过（12 tests）。
- 覆盖状态：DONE

## R-84
- 系统级需求：发布清单锁定——生成 M8 release manifest（git hash + migration hashes + 测试通过数 + 性能基线快照 + 服务注册表快照）
- PRD 需求编号（FR）：NFR-MAINT-001
- 来源：PRD §5（NFR）；PRD M8
- DevSpec 落点：DevSpec §18.4.1
- DevTasks（TASK）：TASK-0110
- 验收测试（T-xx）：T-REL-01
- 当前进展（2026-03-03）：generate_m8_manifest() 实现 git hash + migration hashes + 测试通过数 + 性能基线快照 + 服务注册表快照；release/manifests/m8_release.json 已生成（15 migration 文件）；T-REL-01 全部通过（17 tests）。
- 覆盖状态：DONE

## R-85
- 系统级需求：World Canon 文件 SSOT + 最小种子条目——在 host_brain/canon/world/ 下建立 World Canon 文件 SSOT，写入系统术语/架构不变量/人格核心设定各 1 条种子条目，所有条目 evidence_level=CANON_REFERENCE_ONLY，body 不含完成性表述
- PRD 需求编号（FR）：FR-WCANON-001
- 来源：PRD §4（FR）；PRD M9
- DevSpec 落点：DevSpec §19.1.1
- DevTasks（TASK）：TASK-0111
- 验收测试（T-xx）：T-WCanon-04（前置）
- 当前进展（2026-03-03）：wc-001-system-terms.json / wc-002-architecture-invariants.json / wc-003-persona-core.json 三个种子条目已创建，evidence_level=CANON_REFERENCE_ONLY，body QA 校验通过；tests/fixtures/canon/world/ fixture 副本已就绪；T-WCanon-04 前置条件满足。
- 覆盖状态：DONE

## R-86
- 系统级需求：world_canon_doc 表 + canon_sync 幂等同步——SQLite Ledger 新增 world_canon_doc 表与 canon_proposal_log 表，canon_sync.py 实现幂等扫描（相同 hash 不重复写 change_log，失败记录 CANON_SYNC_FAILED）
- PRD 需求编号（FR）：FR-WCANON-002
- 来源：PRD §4（FR）；PRD M9
- DevSpec 落点：DevSpec §19.1.2；DevSpec §19.1.4
- DevTasks（TASK）：TASK-0112
- 验收测试（T-xx）：T-WCanon-04
- 当前进展（2026-03-03）：host_brain/audit/migrations/0015_world_canon_doc.sql + 0016_canon_proposal_log.sql 已创建；canon_sync.py 扩展 sync_world_canon() 幂等扫描逻辑；errors.py 新增 CANON_SYNC_FAILED / WCANON_SLOT_VIOLATION / WCANON_PROPOSAL_INVALID；T-WCanon-04 全部通过。
- 覆盖状态：DONE

## R-87
- 系统级需求：context_pack 分槽 + QA 拦截 Canon-as-fact——context_pack.py 新增 authoritative.world_canon 独立槽位（Top-K，evidence_level=CANON_REFERENCE_ONLY），qa.py 拦截"World Canon 内容 + 完成性表述"组合并记录 CANON_AS_FACT_VIOLATION
- PRD 需求编号（FR）：FR-WCANON-003；FR-WCANON-001；FR-CANON-001
- 来源：PRD §4（FR）；PRD M9
- DevSpec 落点：DevSpec §19.1.3
- DevTasks（TASK）：TASK-0113
- 验收测试（T-xx）：T-WCanon-05；T-WCanon-06
- 当前进展（2026-03-03）：context_pack.py 扩展 build_world_canon_slot()，World Canon 条目仅注入 authoritative.world_canon 槽位；qa.py 扩展 check_canon_as_fact()，叙事含"Canon 内容 + 完成性表述"时拦截并记录 CANON_AS_FACT_VIOLATION，重写失败降级为 Host 最小模板；T-WCanon-05/T-WCanon-06 全部通过。
- 覆盖状态：DONE

## R-88
- 系统级需求：World Canon Evolution 提案审核流程——提案写入 canon_proposal_log（status=pending），审核通过后触发 canon_sync.py 更新文件 SSOT，所有状态变更写 canon_change_log（含变更前后 hash）
- PRD 需求编号（FR）：FR-WCANON-002
- 来源：PRD §4（FR）；PRD M9
- DevSpec 落点：DevSpec §19.1.4
- DevTasks（TASK）：TASK-0114
- 验收测试（T-xx）：T-WCanon-07
- 当前进展（2026-03-03）：canon_world_store.py 扩展 submit_proposal()（写入 canon_proposal_log，status=pending）与 approve_proposal()（状态改为 approved，触发 sync，写 change_log）；非白名单流程不得直接修改 status（contract 断言）；T-WCanon-07 全部通过。
- 覆盖状态：DONE

## R-89
- 系统级需求：日记 drift_suggestion 关联——diary_writer.py 提取 LLM 输出中的可选 drift_suggestion 字段，经 QA 校验后写入 canon_proposal_log（proposer=diary_writer，source_intent_id=日记 intent_id）；校验失败则丢弃并记录 WCANON_PROPOSAL_INVALID，不阻塞日记写入
- PRD 需求编号（FR）：FR-DIARY-002
- 来源：PRD §4（FR）；PRD M9
- DevSpec 落点：DevSpec §19.2
- DevTasks（TASK）：TASK-0115
- 验收测试（T-xx）：T-Diary-03；T-Diary-04
- 当前进展（2026-03-03）：diary_writer.py 扩展 _extract_drift_suggestion() + _submit_evolution_proposal()；errors.py 新增 DIARY_EVOLUTION_PROPOSAL_SKIPPED；drift_suggestion 合规时 canon_proposal_log 写入成功，source_intent_id 可追溯；含伪执行句时提案丢弃，日记正常写入；T-Diary-03/T-Diary-04 全部通过。
- 覆盖状态：DONE

---

## M10-A Canon 治理 API（R-90~R-91）

## R-90
- 系统级需求：Canon 提案审核 REST API——将 M9 已落地的 submit_proposal() / approve_proposal_with_content() 暴露为 REST 端点（GET/POST /canon/proposals + approve/reject + GET /canon/documents），支持 Master 通过 HTTP 接口查询、提交、审核、拒绝 World Canon Evolution 提案；所有写操作审计串联（proposer_node_id 含 request_id）；幂等性（重复 proposal_id 返回 409 + WCANON_PROPOSAL_DUPLICATE）；结构稳定（含 reason_code）
- PRD 需求编号（FR）：FR-WCANON-002
- 来源：PRD §4（FR）；PRD M10-A
- DevSpec 落点：DevSpec §20.2~20.4
- DevTasks（TASK）：TASK-0117；TASK-0118；TASK-0119
- 验收测试（T-xx）：T-Canon-API-01~08
- 当前进展（2026-03-03）：host_brain/app/canon_routes.py 新增（7 个端点）；host_brain/memory/canon_world_store.py 扩展 reject_proposal()；host_brain/audit/sqlite_ledger.py 扩展 list_proposals()；host_brain/app/fastapi_app.py 注册 canon_router；host_brain/core/errors.py 新增 WCANON_PROPOSAL_NOT_FOUND / WCANON_DOC_NOT_FOUND / WCANON_PROPOSAL_DUPLICATE / WCANON_PROPOSAL_STATUS_INVALID；tests/contract/test_canon_api_contract.py 新增（10 tests，T-Canon-API-01~08 全部通过）；1041 tests passed。
- 覆盖状态：DONE

## R-91
- 系统级需求：M10-A 发布清单锁定——生成 M10-A release manifest（git hash + migration hashes + world_canon_asset_hashes + test_passed_count=1041 + generated_at）
- PRD 需求编号（FR）：NFR-MAINT-001
- 来源：PRD §5（NFR）；PRD M10-A
- DevSpec 落点：DevSpec §20
- DevTasks（TASK）：TASK-0120
- 验收测试（T-xx）：T-REL-01（复用）
- 当前进展（2026-03-03）：requirements_trace.md 补写 R-90~R-91；DevTasks.md M10-A 状态更新为 DONE；PRD.md M10-A 状态更新为 DONE；1041 tests passed。
- 覆盖状态：DONE

| R-92 | FR-WCANON-001 | WorldCanonStore.extract_hotwords() | host_brain/memory/canon_world_store.py | T-Canon-HW-01~03 | DONE | M10-B |
| R-93 | NFR-PERF-001 | GET /canon/hotwords 延迟 < 200ms | host_brain/app/canon_routes.py | T-Canon-HW-API-01~02 | DONE | M10-B |
| R-94 | FR-ASR-001 | inject_canon_hotwords() 热词注入 | edge_client/senses/asr.py | T-ASR-HW-01~03 | DONE | M10-B |

| R-95 | FR-MEM-001 | MemoryDistillationEngine.distill_top_k() | host_brain/memory/distillation.py | T-Distill-01~03 | DONE | M10-C |
| R-96 | FR-MEM-002 | semantic_memory 表 + CRUD（log/get/list） | host_brain/audit/sqlite_ledger.py | T-Distill-04 | DONE | M10-C |
| R-97 | FR-MEM-003 | context_pack.semantic_slot 可选分槽 + build_semantic_slot() | host_brain/core/context_pack.py | T-Distill-05~06 | DONE | M10-C |

| R-98 | FR-MEM-001 | distill_top_k() 并发保护（蒸馏级别锁 + 锁内幂等复检） | host_brain/memory/distillation.py | T-Distill-07 | DONE | M10-D |
| R-99 | NFR-PERF-001 | _table_has_column() 线程安全缓存（减少 PRAGMA） | host_brain/audit/sqlite_ledger.py | T-Quality-01(F-06) | DONE | M10-D |
| R-100 | FR-WCANON-002 | approve/reject 审计原子性增强（reject 审计失败硬失败） | host_brain/app/canon_routes.py | T-Canon-API-04~06 | DONE | M10-D |
| R-101 | NFR-MAINT-001 | m10a_release 清单补锁 canon_world_store/sqlite_ledger hash | host_brain/release/release_manifest.py; release/manifests/m10a_release.json | T-REL-01(扩展) | DONE | M10-D |
| R-102 | FR-DIARY-002 | source_intent_id 端到端完整性落库与回归断言 | host_brain/memory/canon_world_store.py; host_brain/audit/sqlite_ledger.py; host_brain/skills/impl/diary_writer.py | T-Diary-03~04 | DONE | M10-D |
| R-103 | NFR-MAINT-001 | requirements.txt Redis 可选依赖声明 | requirements.txt | T-Quality-01(F-12) | DONE | M10-D |
| R-104 | NFR-MAINT-001 | M10-D 发布收口（v6 审查报告 + m10d_release.json） | docs/40_agents/full_code_review_report_2026-03-03-v6.md; release/manifests/m10d_release.json | T-REL-01(扩展) | DONE | M10-D |
| R-105 | FR-GOV-001 | Redis RateLimit 后端真实集成验证（环境变量启用） | tests/integration/test_rate_limit_redis_integration.py | T-Gov-R4-Redis-01 | TEST_READY | M10-D |
| R-106 | NFR-REL-001 | SQLite WAL/NFS 部署约束文档化（本地磁盘强约束） | docs/50_project/ExternalServices_Config.md; docs/50_project/ENV_EXTERNAL_SERVICES_CHECKLIST.md | T-Deploy-WAL-01(文档验收) | DONE | M10-D |
| R-107 | FR-AIRLOCK-002 | Sandbox 容器硬隔离 + 跨平台超时一致化（F-01/F-02） | host_brain/airlock/sandbox.py; tests/contract/test_sandbox_contract.py; tests/contract/test_sandbox_docker_contract.py | T-Sandbox-01; T-Sandbox-Docker-01 | DONE | M10-D |
| R-108 | FR-CHANNEL-001, FR-VOICE-001 | WeChat→Edge 真实 RPC 端到端事件链路（F-11） | tests/e2e/test_wechat_vts_voice_rpc_e2e.py | T-E2E-RPC-01 | DONE | M10-D |
| R-109 | FR-ASR-001, NFR-PERF-001 | _CANON_HOTWORDS_CACHE TTL 自动失效（F-42）——_HotwordsCache 类（线程安全，TTL=300s）+ inject_canon_hotwords 更新缓存 + get_canon_hotwords 返回副本 | edge_client/senses/asr.py; tests/unit/test_hotwords_cache_ttl.py | T-HW-TTL-01~05 | DONE | M10-E |
| R-110 | NFR-SEC-001 | F-16 _fetch_by_request 表名白名单校验补全（P2 收口） | host_brain/audit/sqlite_ledger.py | T-Quality-01(F-16扩展) | DONE | P2-收口 |
| R-111 | NFR-MAINT-001 | F-13/F-14/F-15/F-25/F-38 P2 已修复核查确认 | host_brain/app/main.py; host_brain/integrations/graph_executor.py; host_brain/app/fastapi_app.py; edge_client/actuators/vts_driver.py; host_brain/audit/sqlite_ledger.py | — | DONE | P2-收口 |
| R-112 | NFR-REL-001, NFR-MAINT-001 | F-04/F-07/F-08 设备验证包标记 BLOCKED_ENV（单机 SQLite 部署暂缓；多实例/真机部署前执行） | docs/30_devspec/DevTasks.md | — | BLOCKED_ENV | 设备验证包 |
| R-113 | FR-SCHED-001/002 | M11-A DevSpec §21~§23 工程契约拆解（调度器/桌面留言/梦境/日记/主动对话/Focus Mode） | docs/30_devspec/DevSpec.md §21~§23 | T-SCHED-01~07；T-DESK-01~08；T-DREAM-01~05；T-DIARY-01~05；T-PROACT-01~06；T-FOCUS-01~04 | DONE | M11-A |
| R-114 | FR-SCHED-001/002 | M11-A T-SCHED-01~07 单元测试（NatsumeScheduler：触发/sys_mode 门控/连续失败暂停/健康快照/注销/intent 字段） | tests/unit/test_scheduler_engine.py | T-SCHED-01~07 | DONE | M11-A |
| R-115 | FR-SCHED-001/002 | M11-A Scheduler REST API（8 个端点：/scheduler/tasks CRUD + enable/disable + /scheduler/health） | host_brain/scheduler/scheduler_routes.py；host_brain/app/fastapi_app.py | T-SCHED-05 | DONE | M11-A |
| R-116 | FR-DESKTOP-001/002 | M11-B T-DESK-01~08 单元测试（桌面留言：create/undo/路径穿越/Gaming/IO错误/QA/quarantine/审计） | tests/unit/test_desktop_note_skill.py | T-DESK-01~08 | DONE | M11-B |
| R-117 | FR-DREAM-001/002 | M11-C T-DREAM-01~05 单元测试（梦境：Sleep触发/Normal无空闲/Episodic降级/NARRATIVE_ONLY/Gaming） | tests/unit/test_dream_skill.py | T-DREAM-01~05 | DONE | M11-C |
| R-118 | FR-DIARY-001/002 | M11-D T-DIARY-01~05 单元测试（日记：文件+Episodic双写/无内容/IO降级/Episodic降级/Evolution提案） | tests/unit/test_diary_skill.py | T-DIARY-01~05 | DONE | M11-D |
| R-119 | FR-PROACTIVE-001/002 | M11-E T-PROACT-01~06 单元测试（主动对话：空闲触发/Gaming/频率/QA/Edge降级/Sleep） | tests/unit/test_proactive_skill.py | T-PROACT-01~06 | DONE | M11-E |
| R-120 | FR-FOCUS-001 | M11-F T-FOCUS-01~04 单元测试（Focus Mode：主动对话拦截/enter/exit/审计字段） | tests/unit/test_focus_mode.py | T-FOCUS-01~04 | DONE | M11-F |

## M12 ToolBroker + ContextCompressor + Reranker + SkillHotLoader（R-121~R-125）

| R-121 | FR-TOOL-001/002/003 | ToolBroker 单入口（broker.py/catalog.py/selector.py/mcp_adapter.py）+ tool_visibility_hash 审计串联 + WRITE Token gating（无 token 返回 403 + TOOL_TOKEN_MISSING） | host_brain/tools/broker.py; host_brain/tools/catalog.py; host_brain/tools/selector.py; host_brain/tools/mcp_adapter.py | T-TOOL-01~06 | DONE | M12-A |
| R-122 | FR-TOOL-003 | ToolBroker REST API（GET /tools/meta-pack + POST /tools/execute）注册到 fastapi_app.py | host_brain/app/tool_routes.py; host_brain/app/fastapi_app.py | T-TOOL-API-01~05 | DONE | M12-A |
| R-123 | FR-MEM-005 | ContextCompressor（sliding_window/summary/hybrid 三策略）+ EvidencePack 构建；LLM 不可用时降级为 sliding_window | host_brain/memory/context_compressor.py | T-CTX-01~05 | DONE | M12-B |
| R-124 | FR-MEM-004 | RerankerService 强化（health() + 降级策略 + evidence_pack rerank_stage 字段）；不可用时降级且 stage 标注 | host_brain/retrieval/reranker.py; host_brain/retrieval/evidence_pack.py | T-RERANK-01~04 | DONE | M12-C |
| R-125 | FR-SKILL-001/002 | SkillHotLoader（热加载/回滚/版本注册）+ SkillVersionRegistry；热加载失败时旧版本保持不变 | host_brain/skills/hot_loader.py | T-HOTLOAD-01~05 | DONE | M12-D |

## M13 Bond 阶段自动晋级 + 长期记忆蒸馏强化（R-126~R-130）

| R-126 | FR-BOND-001/002/003 | BondStageEngine：积分累积/阶段晋级/降级/趋势/审计串联（晋级/降级写入审计链）；BondScoreState + StageTransitionResult | host_brain/bond/bond_stage_engine.py | T-BOND-STAGE-01~09 | DONE | M13-A |
| R-127 | FR-BOND-001/002/003 | Bond REST API 5 端点（/bond/stage /bond/summary /bond/event /bond/users /bond/health）；Bond Summary 不含数值（FR-BOND-003）；evidence_refs 为空时 422（FR-BOND-002） | host_brain/app/bond_routes.py | T-BOND-API-01~08 | DONE | M13-A |
| R-128 | FR-MEM-DISTILL-001/002 | LongTermMemoryDistiller（EpisodicMemory→语义记忆蒸馏→Canon pending 提案）；蒸馏结果只生成 pending 提案不直接写 SSOT；每次循环最多 5 个提案；幂等保护 | host_brain/memory/long_term_distiller.py | T-DISTILL-B-01~10 | DONE | M13-B |
| R-129 | FR-MEM-DISTILL-001/002 | make_distillation_task_config()：调度器 CRON 任务工厂（每日 03:00）；配置合法性验证 | host_brain/memory/long_term_distiller.py | T-DISTILL-B-09~10 | DONE | M13-B |
| R-130 | FR-BOND-001/002 | BondDynamicsWriter.apply_update 接入 BondStageEngine.apply_event（可选注入 stage_engine=None 向后兼容）；stage_engine 异常非致命；event 新增 stage_transition 字段 | host_brain/bond/bond_dynamics.py | T-BOND-INTEG-01~08 | DONE | M13-C |

## M14 通道 + 外显 + MCP 治理收口（R-131~R-135）

| R-131 | FR-CHANNEL-001 | WeChatChannelEvent（inbound/outbound）+ receive_wechat_event_v2()：inbound 建模为确定性通道事件，写入审计链（channel_io_log），含 thread_key/request_id 可追溯；reason_code 可统计 | host_brain/channels/wechat.py | T-CHAN-01~02；T-CHAN-AUDIT-01~02 | DONE | M14-A |
| R-132 | FR-CHANNEL-001 | send_wechat_message_authorized()：outbound 三重校验（thread_key/intent_id/approval_token）；失败返回 reason_code + stage；写入审计链；LLM 不得自行决定发送 | host_brain/channels/wechat.py；host_brain/core/errors.py（CHANNEL_SEND_UNAUTHORIZED/CHANNEL_INTENT_MISSING/CHANNEL_THREAD_KEY_MISSING） | T-CHAN-03~05；T-CHAN-AUDIT-03 | DONE | M14-A |
| R-133 | FR-AVATAR-001 | VTSExpressionMap.resolve()：emotion_tag→expression_id 固定枚举映射（EmotionTag + EXPRESSION_MAP）；非法 tag → VTS_TAG_UNKNOWN；自由动作参数 → VTS_EXPRESSION_INVALID；同版本同输入输出一致（可回放） | edge_client/actuators/vts_expression_map.py；host_brain/core/errors.py（VTS_TAG_UNKNOWN/VTS_EXPRESSION_INVALID/VTS_INTENSITY_OUT_OF_RANGE/VTS_DURATION_INVALID） | T-VTS-01~05 | DONE | M14-B |
| R-134 | FR-MCP-001 | MCPAdapter.sync_provider_tools()：同步 MCP provider 工具为 ToolMeta 列表（含 READ/WRITE 分级）；MCPAdapter.execute_mcp_tool()：WRITE 必须有 approval_token_ref（MCP_WRITE_UNAUTHORIZED）；出站经 AIRLOCK 净化（SanitizationReport）；缺 sanitization_report → AIRLOCK_BLOCKED_EGRESS；执行无 evidence_ref → MCP_AUDIT_REF_MISSING | host_brain/tools/mcp_adapter.py（ToolMeta + SanitizationReport + sync_provider_tools + execute_mcp_tool + _airlock_sanitize） | T-MCP-GOV-01~05；T-MCP-AUDIT-01~03 | DONE | M14-C |
| R-135 | NFR-REL-001；FR-CHANNEL-001；FR-AVATAR-001；FR-MCP-001 | M14 发布清单锁定（m14_release.json）：4 个核心文件 hash；79 个 M14 测试通过；1421 全量通过；release_ready=true；audit_chain_verified=true | configs/release/ | T-REL-01（复用） | DONE | M14-D |

## M15 语音链路深化收口（R-136~R-140）

| R-136 | FR-VOICE-001 | ASRTranscriptEvent（frozen dataclass）：text/confidence/ts_start_ms/ts_end_ms/is_final/alternates（最多3个）/evidence_ref（必填）/low_confidence（confidence<0.6时True）/reason_code（ASR_LOW_CONFIDENCE）；build_asr_transcript_event 工厂函数：缺 evidence_ref 拒绝构建；alternates 超出截断 | edge_client/senses/asr.py（ASRTranscriptEvent + build_asr_transcript_event + LOW_CONFIDENCE_THRESHOLD + MAX_ALTERNATES） | T-ASR-EVT-01~05 | DONE | M15-A |
| R-137 | FR-VOICE-002 | TTSPlaybackEvent（frozen dataclass）：request_id（必填）/intent_id/text/emotion_tag/status（queued/playing/done/interrupted/failed）/reason_code/stage/ts_ms/evidence_ref（必填）；build_tts_playback_event 工厂函数：缺 request_id/evidence_ref 或 status 非法 → VOICE_SCHEMA_INVALID；Host 只下发最终口径文本与情绪标签 | edge_client/actuators/tts_engine.py（TTSPlaybackEvent + build_tts_playback_event） | T-TTS-QUEUE-01~05 | DONE | M15-B |
| R-138 | FR-VOICE-003 | BargeInEvent（frozen dataclass）：session_id/interrupted_request_id（必填）/trigger_ts_ms/asr_continued（默认True）/evidence_ref（必填）；打断不构成执行事实（无 intent_draft/execution_claim/decision 字段）；ASR 不被阻塞（asr_continued=True）；缺 interrupted_request_id/evidence_ref → VOICE_SCHEMA_INVALID | edge_client/actuators/tts_engine.py（BargeInEvent + build_barge_in_event） | T-BARGE-01~04 | DONE | M15-C |
| R-139 | FR-VOICE-004 | FallbackModelGate：默认卸载（is_available=False）/按需加载（load()/unload()）/generate() 输出永远 draft_only=True；不可用时快速失败（FALLBACK_MODEL_UNAVAILABLE）；task_type 非法 → VOICE_SCHEMA_INVALID；不得包含裁决或执行事实宣告（as_dict 无 decision/execution_claim/intent_draft 字段） | edge_client/fallback_model.py（FallbackModelGate + FallbackOutput + VALID_TASK_TYPES） | T-FALLBACK-01~05 | DONE | M15-D |
| R-140 | FR-VOICE-005；NFR-REL-001 | ResourcePreemptionEvent（frozen dataclass）：preemptor/preempted/reason_code=RESOURCE_GATE_PREEMPTED/ts_ms/evidence_ref（必填）；build_resource_preemption_event 工厂函数：preemptor/preempted 必须是合法 TASK_* 常量；VoiceResourceGate 优先级 ASR>PLAYBACK>TTS_SYNTH>FALLBACK_LLM；压力下 ASR 始终保证；M15 发布清单（m15_release.json）：1534 全量通过；release_ready=true | edge_client/rpc/resource_gate.py（ResourcePreemptionEvent + build_resource_preemption_event）；configs/release/ | T-RGATE-01~05 | DONE | M15-E/F |

## M16 Canon 治理强化 + 记忆检索优化 + 调度器持久化（R-141~R-145）

| 编号 | 需求来源 | 实现描述 | 实现文件 | 测试编号 | 状态 | 里程碑 |
|------|---------|---------|---------|---------|------|--------|
| R-141 | FR-CANON-GOV-001（F-28/F-35/F-36 修复） | Canon 治理 API 强化：`_get_canon_store()` 加写锁防竞态（F-28）；`approve_proposal` 两次 DB 操作包裹在 `ledger.transaction()` 事务中（F-35）；`reject_proposal` 审计写入失败降级为 warning 不阻断主流程（F-36）；`SQLiteLedger.transaction()` 新增上下文管理器 | host_brain/app/canon_routes.py；host_brain/audit/sqlite_ledger.py | T-CANON-TX-01~06 | DONE | M16-A |
| R-142 | FR-CANON-GOV-001（F-29 修复） | Canon 路由依赖注入强化：`_ledger_provider` 可注入替换，降级路径 `:memory:` 仅用于测试场景；`_get_ledger_from_app()` 延迟导入循环引用风险通过 provider 模式消除 | host_brain/app/canon_routes.py | T-CANON-TX-01~06（复用） | DONE | M16-A |
| R-143 | FR-MEM-PAGE-001（F-38 修复） | 长期记忆检索强化：`list_proposals()` cursor-based 分页（`after_id` 参数）已在 SQLiteLedger 实现；`canon_routes.py` `/canon/proposals` 端点暴露 `after_id` 查询参数；深页码性能从 O(N) 降为 O(log N) | host_brain/app/canon_routes.py；host_brain/audit/sqlite_ledger.py | T-CURSOR-01~05 | DONE | M16-B |
| R-144 | FR-SCHED-PERSIST-001（F-40 修复） | 调度器任务持久化：`NatsumeScheduler.register_task()` 将任务序列化写入 `scheduler_tasks` 表；`start()` 启动时调用 `_load_persisted_tasks()` 自动恢复；`unregister_task()` 同步删除持久化记录；新增 migration `0018_scheduler_tasks.sql`；`SQLiteLedger` 新增 `save_scheduler_task / load_scheduler_tasks / delete_scheduler_task` 三个 CRUD 方法 | host_brain/scheduler/scheduler.py；host_brain/audit/sqlite_ledger.py；host_brain/audit/migrations/0018_scheduler_tasks.sql | T-SCHED-PERSIST-01~05 | DONE | M16-C |
| R-145 | NFR-REL-001 | M16 发布清单锁定（m16_release.json）：7 个核心文件 hash；1564 全量通过（+31 vs M15）；release_ready=true；F-28/F-29/F-35/F-36/F-38/F-40 全部修复标注 | configs/release/ | T-REL-01（复用） | DONE | M16-D |

## M17 监控面板 v3 前端重建（R-146~R-150）

| 编号 | 需求来源 | 实现描述 | 实现文件 | 测试编号 | 状态 | 里程碑 |
|------|---------|---------|---------|---------|------|--------|
| R-146 | FR-OBS-003 | React 18 + Vite 5 + Tailwind 3 + React Router 6 SPA 架构；Dify 简约白色风格（主色 #2970FF）；Zustand 全局状态（轮询 + 时间范围过滤）；Lucide React 图标；`npm run build` 产出到 `static/monitor/`；`npm run dev` 代理 `/obs/*` 到 FastAPI；Layout 组件（顶部导航栏 + 页面容器）；StatusBadge / ServiceCard 公共组件；API 请求层 `src/api/obs.js` | host_brain/app/monitor_ui/package.json; host_brain/app/monitor_ui/vite.config.js; host_brain/app/monitor_ui/tailwind.config.js; host_brain/app/monitor_ui/src/main.jsx; host_brain/app/monitor_ui/src/App.jsx; host_brain/app/monitor_ui/src/store/useAppStore.js; host_brain/app/monitor_ui/src/api/obs.js; host_brain/app/monitor_ui/src/components/Layout.jsx; host_brain/app/monitor_ui/src/components/StatusBadge.jsx; host_brain/app/monitor_ui/src/components/ServiceCard.jsx | T-OBS-V3-ARCH-01~03 | DONE | M17-A |
| R-147 | FR-OBS-004 | Overview 页面：双端所有服务健康状态卡片网格（≥12 个服务）；顶部汇总统计行（总服务数/健康/降级/故障）；每个服务卡片含服务名/侧（Host/Edge）/状态徽章/响应延迟/最后检查时间/错误摘要（DOWN 时展示）；30 秒自动轮询 `/obs/services/health`；轮询失败时显示"上次已知状态 HH:MM:SS" | host_brain/app/monitor_ui/src/pages/Overview.jsx | T-OBS-V3-OVW-01~05 | DONE | M17-B |
| R-148 | FR-OBS-005 | Sessions 页面：历史会话表格（request_id/时间/状态/耗时/通道/reason_code）；顶部过滤栏（时间范围选择器 + 状态下拉 + 关键词搜索）；底部分页控件（每页 20 条）；点击行跳转 Trace 页并传递 request_id；URL 参数传递 `?rid=xxx`；空结果友好空状态 | host_brain/app/monitor_ui/src/pages/Sessions.jsx | T-OBS-V3-SESS-01~05 | DONE | M17-C |
| R-149 | FR-OBS-006 | Trace 页面：React Flow + Dagre 自动布局（从左到右，支持分支/并行）；CustomNode 组件（圆角卡片 + Lucide 图标 + 状态颜色 + 耗时标签 + Provider 徽章）；有向连线（箭头样式）；NodeDetailPanel 侧边面板（input/output JSON 格式化 + 折叠/展开/复制 + status/reason_code/时间戳/side/model_id）；顶部工具栏（request_id 输入 + 查询 + 全屏切换）；对接 `/obs/trace/{request_id}` API；mock 数据降级 | host_brain/app/monitor_ui/src/pages/Trace.jsx; host_brain/app/monitor_ui/src/components/trace/CustomNode.jsx; host_brain/app/monitor_ui/src/components/trace/NodeDetailPanel.jsx; host_brain/app/monitor_ui/src/utils/traceLayout.js | T-OBS-V3-TRACE-01~08 | DONE | M17-D |
| R-150 | FR-OBS-007 | Metrics 页面：时间范围选择器（1h/6h/24h/7d）；LineChart 封装组件（ECharts 折线图，可复用）；BarChart 封装组件（ECharts 柱状/条形图，可复用）；4 种图表（请求量趋势/拒绝率趋势/reason_code 分布/服务延迟 P50/P95）；对接 `/obs/metrics/timeseries` API；无数据时显示"数据采集中"占位；HiDPI 支持（ECharts 内置 devicePixelRatio）；mock 数据降级 | host_brain/app/monitor_ui/src/pages/Metrics.jsx; host_brain/app/monitor_ui/src/components/charts/LineChart.jsx; host_brain/app/monitor_ui/src/components/charts/BarChart.jsx | T-OBS-V3-METRICS-01~05 | DONE | M17-E |

## M18 监控面板 v3 后端 API 扩展（R-151~R-155）

| 编号 | 需求来源 | 实现描述 | 实现文件 | 测试编号 | 状态 | 里程碑 |
|------|---------|---------|---------|---------|------|--------|
| R-151 | FR-OBS-008 | ServiceHealthAggregator：asyncio 并发探测双端所有已注册服务（Host 侧：LLM Gateway/Neo4j/SQLite/Chroma/Scheduler/Bond Engine/Memory Distiller/MCP Provider；Edge 侧：ASR/TTS/VTS/Audio Playback/Edge RPC）；单服务超时（≤200ms）不阻塞其他服务；支持 `?side=host\|edge` 过滤；响应结构稳定（字段不随服务状态变化而缺失）；`GET /obs/services/health` 端点注册到 obs_routes.py | host_brain/obs/service_health_aggregator.py; host_brain/app/obs_routes.py | T-OBS-HEALTH-01~06 | DONE | M18-A |
| R-152 | FR-OBS-009 | SessionQueryService：从 SQLite Ledger 查询会话列表；支持过滤参数（start_time/end_time/status/channel/keyword 模糊匹配）；分页参数（page/page_size，默认 20，最大 100）；响应含 total_count；空结果返回 results=[] 而非 404；`GET /obs/sessions` 端点注册到 obs_routes.py | host_brain/obs/session_query.py; host_brain/app/obs_routes.py | T-OBS-SESS-01~06 | DONE | M18-B |
| R-153 | FR-OBS-010 | TraceNodeBuilder：从多源审计日志（intent_log/governor_log/skill_exec_log/asr_log/tts_log/vts_log 等）聚合构建结构化节点列表；每节点含 node_id/node_type/node_name/status/start_ts_ms/end_ts_ms/duration_ms/input/output/reason_code/parent_node_id/side；节点按 start_ts_ms 排序；input/output 超 64KB 截断并标注 truncated=true；支持 `?include_io=false` 参数；不存在的 request_id 返回 404 + 稳定错误结构；`GET /obs/trace/{request_id}` 端点注册到 obs_routes.py | host_brain/obs/trace_node_builder.py; host_brain/app/obs_routes.py | T-OBS-TRACE-01~08 | DONE | M18-C |
| R-154 | FR-OBS-011 | MetricsTimeseriesService：多维度时序数据查询（request_count/reject_rate/latency_p50/latency_p95/reason_code_dist）；支持 interval（1m/5m/1h）、start_time、end_time；数据点上限 1000（超出自动降采样，步长 = ceil(total/1000)）；无数据区间返回 null 值点保证时序连续性；支持多 metric 批量查询（返回 {metrics: {metric_name: {data_points: [...]}}} 结构）；`GET /obs/metrics/timeseries` 端点注册到 obs_routes.py | host_brain/obs/metrics_timeseries.py; host_brain/app/obs_routes.py | T-OBS-TS-01~06 | DONE | M18-D |
| R-155 | FR-OBS-003~011；NFR-REL-001；NFR-MAINT-001 | M17+M18 发布清单锁定：m17_release.json（前端 SPA 构建成功；四页面全量可用；zero_js_errors=true；design_review_passed=true）+ m18_release.json（26 个新增测试全部通过；1577 全量通过，2 skipped，0 failed；release_ready=true；all_api_endpoints_tested=true）；UI_UPGRADE_CHANGELOG.md v3.0 条目更新为已完成 | configs/release/; configs/release/; docs/50_project/UI_UPGRADE_CHANGELOG.md | T-REL-01（复用） | DONE | M18-E |


## M21 测试基线修复 + Canon 向量投影追踪（R-156~R-157）

## R-156
- 系统级需求：Canon 向量投影核心契约——投影文档 canon_kind 必须为 style/world；向量 ID 格式确定性（canon_style::{canon_id} / canon_world::{doc_id}）；metadata tags 为逗号分隔字符串（非 list），tags_count 为整数；Chroma 不可用时不抛出未捕获异常；投影文档不含 Canon SSOT 写路径
- PRD 需求编号（FR）：FR-CANON-001；NFR-REL-001
- 来源：PRD §4.12；DevSpec §19
- DevSpec 落点：DevSpec §19.1
- DevTasks（TASK）：TASK-0205
- 验收测试（T-xx）：T-CVP-CONTRACT-01~05
- 当前进展（2026-03-05）：新增 `tests/contract/test_canon_vector_projection_contract.py`，T-CVP-CONTRACT-01~05（15 个测试）全部通过；canon_kind/vector_id/tags/Chroma降级/SSOT写路径五个核心契约点均已覆盖。
- 覆盖状态：DONE

## R-157
- 系统级需求：Canon 向量投影运行时脚本与降级策略——dry-run 模式（include_style=False/include_world=False）不写入 Chroma，仅返回 docs_collected 统计；collect_canon_projection_documents 不依赖 Chroma，始终可正常返回；style-only/world-only 模式正确隔离写入
- PRD 需求编号（FR）：FR-CANON-001；NFR-REL-001
- 来源：PRD §4.12；DevSpec §19
- DevSpec 落点：DevSpec §19.1
- DevTasks（TASK）：TASK-0205
- 验收测试（T-xx）：T-CVP-CONTRACT-06
- 当前进展（2026-03-05）：T-CVP-CONTRACT-06（5 个测试）全部通过；dry-run/style-only/world-only 三种模式均已验证；collect_canon_projection_documents 无 Chroma 依赖已确认。
- 覆盖状态：DONE


## M23 FTS5 中文检索修复 + Edge 心跳探针集成（R-158~R-159）

## R-158
- 系统级需求：FTS5 中文词组检索修复——FullTextSearcher 新增 _split_chinese_query 静态方法，对中文字符进行字符级分割（CJK 统一汉字范围判断）；index_memory 写入时对中文内容同步分割；search 查询时对中文查询词同步分割；fts_score 归一化 bug 修复（单条命中时 score_range=0 返回 1.0）；修复后 FTS5 中文词组（如"猫咪"、"工作压力"）可正常命中，混合检索（向量 70% + FTS5 30%）FTS5 权重真正生效
- PRD 需求编号（FR）：FR-MEM-003；FR-MEM-005
- 来源：PRD §4.11；试运行报告 §10.4/10.13 P2-FTS
- DevSpec 落点：DevSpec §15（Episodic 混合检索）
- DevTasks（TASK）：TASK-0210
- 验收测试（T-xx）：T-FTS-01~06
- 当前进展（2026-03-05）：新增 `tests/unit/test_full_text_searcher.py`，T-FTS-01~06（21 个测试）全部通过；中文词组查询命中、单字查询、英文不受影响、混合中英文、FTS5 无结果降级、hybrid_fused 验证均已覆盖；全量测试 1731 passed，0 failed，零回归。
- 覆盖状态：DONE

## R-159
- 系统级需求：Edge 心跳探针集成——build_edge_transport_probe_statuses 从"定义但未被调用"的孤立函数，集成到 /health 端点的 runtime_statuses 构建流程（fastapi_app.py）和 AgentHealthCheck 心跳任务（scheduler.py）中；新增 _check_edge_transport_probes 方法（调用探针、写入 EDGE_PROBE_ALERT 审计事件）和 _log_edge_probe_alert 方法；探针异常时降级处理不崩溃心跳任务
- PRD 需求编号（FR）：FR-OBS-001；NFR-REL-001
- 来源：PRD §4.1（FR-OBS-001）；试运行报告 §D.3 遗留问题
- DevSpec 落点：DevSpec §20（AgentHealthCheck 心跳检测）
- DevTasks（TASK）：TASK-0211
- 验收测试（T-xx）：T-HB-07~09；T-HEALTH-EP-01~02
- 当前进展（2026-03-05）：T-HB-07~09（4 个测试）+ T-HEALTH-EP-01~02（3 个测试）全部通过；edge 全部 UP 不写审计、edge DOWN 写 EDGE_PROBE_ALERT、探针异常降级、/health 端点集成验证均已覆盖；全量测试 1731 passed，0 failed，零回归。
- 覆盖状态：DONE


## M24 LLM 网关真实接入 + VTS 表情驱动 + 微信通道完整链路（R-160~R-162）

## R-160
- 系统级需求：LLM 网关真实接入——新增 LLMHealthProbeResult/LLMRouteResult 数据类；health_probe() 方法（探测主模型可用性，写 llm_call_log）；route_call() 方法（多模型路由：主模型→备用→本地兜底，写 llm_route_log）；_load_api_key_from_env() 从环境变量读取 API Key（不硬编码）；fallback_providers 配置支持；所有模型不可用时稳定降级（LLM_ROUTE_ALL_FAILED）
- PRD 需求编号（FR）：FR-LLM-GW-001；FR-LLM-GW-002；FR-LLM-GW-003
- 来源：PRD §4.26；DevSpec §21
- DevSpec 落点：DevSpec §21.1
- DevTasks（TASK）：TASK-0213
- 验收测试（T-xx）：T-LLM-GW-01~15
- 当前进展（2026-03-05）：T-LLM-GW-01~15（16 个测试）全部通过；API Key 环境变量读取、健康探测、多模型路由降级、AIRLOCK 出站净化完整链路均已验证；全量测试 1782 passed，0 failed，零回归。
- 覆盖状态：DONE

## R-161
- 系统级需求：VTS 表情驱动完整链路——VTSMappingStore（映射表持久化到 SQLite、版本化管理、vts_mapping_change_log 变更前后 hash/version）；VTSRateLimiter（令牌桶算法、热更新频率配置、vts_rate_limit_log）；emotion_tag 非法时拒绝（AVATAR_INVALID_EMOTION_TAG）；映射表加载失败时抛出（VTS_MAPPING_LOAD_FAILED）；频率超限时写审计（VTS_RATE_LIMIT_EXCEEDED）
- PRD 需求编号（FR）：FR-VTS-DRIVE-001；FR-VTS-DRIVE-002；FR-VTS-DRIVE-003
- 来源：PRD §4.27；DevSpec §22
- DevSpec 落点：DevSpec §22.1
- DevTasks（TASK）：TASK-0214
- 验收测试（T-xx）：T-VTS-DRIVE-01~14
- 当前进展（2026-03-05）：T-VTS-DRIVE-01~14（16 个测试）全部通过；映射表持久化/版本化、频率限制令牌桶、热更新、审计串联均已验证；全量测试 1782 passed，0 failed，零回归。
- 覆盖状态：DONE

## R-162
- 系统级需求：微信通道完整链路——WeChatChannelHealth（健康状态管理：last_recv_ts/last_send_ts/status/error_count；连续失败 N 次自动降级 WECHAT_CHANNEL_DEGRADED；降级时 inbound 正常/outbound 阻断；恢复时写 wechat_channel_recovery_log）；每个 thread_key 每分钟最多 N 条频率限制（WECHAT_RATE_LIMIT_EXCEEDED）；手动重置降级状态（reset_for_recovery）
- PRD 需求编号（FR）：FR-WECHAT-001；FR-WECHAT-002；FR-WECHAT-003
- 来源：PRD §4.28；DevSpec §23
- DevSpec 落点：DevSpec §23.1
- DevTasks（TASK）：TASK-0215
- 验收测试（T-xx）：T-WECHAT-01~16
- 当前进展（2026-03-05）：T-WECHAT-01~16（19 个测试）全部通过；inbound 解析（文本/图片/语音）、outbound 频率限制、降级策略、恢复日志均已验证；全量测试 1782 passed，0 failed，零回归。
- 覆盖状态：DONE


## M25 生产就绪加固 + SQLiteLedger 扩展 + 代码质量收口（R-163~R-166）

## R-163
- 系统级需求：SQLiteLedger LLM 路由审计扩展——新增 `log_llm_route()` 正式方法（写入 `llm_route_log` 表）；新增 `query_llm_route_log()` 时间窗口查询方法；新增迁移脚本 `0039_llm_route_log.sql`（幂等）；`LlmGatewayContract._log_route_decision()` 直接调用 `ledger.log_llm_route()`，去掉 `hasattr` 检查；`ledger=None` 时不崩溃
- PRD 需求编号（FR）：FR-AUDIT-LLM-001；FR-AUDIT-LLM-002
- 来源：PRD §4.29；DevSpec §25
- DevSpec 落点：DevSpec §25.1
- DevTasks（TASK）：TASK-0217
- 验收测试（T-xx）：T-AUDIT-LLM-01~05
- 当前进展（2026-03-05）：T-AUDIT-LLM-01~05（7 个测试）全部通过；log_llm_route() 写入/读取、fallback_used 记录、null ledger 安全、迁移脚本幂等、时间窗口查询均已验证；全量测试 1802 passed，0 failed，零回归。
- 覆盖状态：DONE

## R-164
- 系统级需求：WeChatChannelHealth GC 机制——新增 `gc_inactive_threads()` 方法（清理超过 `inactive_threshold_s` 无活动的 `thread_key`，写 `wechat_gc_log`）；新增 `_gc_interval_s` / `_inactive_threshold_s` 配置参数；新增 `_thread_last_active_ms` 活跃时间戳追踪；VTSMappingStore `db_path=":memory:"` 时写 `logging.warning()`（生产可观测）
- PRD 需求编号（FR）：FR-CHANNEL-GC-001；FR-CHANNEL-GC-002
- 来源：PRD §4.30；DevSpec §26
- DevSpec 落点：DevSpec §26.1
- DevTasks（TASK）：TASK-0218
- 验收测试（T-xx）：T-CHANNEL-GC-01~03；T-VTS-STORE-01
- 当前进展（2026-03-05）：T-CHANNEL-GC-01~03（3 个测试）+ T-VTS-STORE-01（2 个测试）全部通过；GC 清理过期 thread_key、保留活跃 thread_key、GC 审计日志、内存模式 WARNING 均已验证；全量测试 1802 passed，0 failed，零回归。
- 覆盖状态：DONE

## R-165
- 系统级需求：SQLiteLedger 表结构缓存加固——新增 `_invalidate_schema_cache(table=None)` 方法（迁移后清空缓存，支持指定表或全量清空）；`_table_has_column()` 缓存机制已存在（`_column_cache`），本次补充失效接口；`main.py` 裸 `print()` 已在 M24 修复为 `logger.info()`
- PRD 需求编号（FR）：FR-PERF-AUDIT-001
- 来源：PRD §4.31；DevSpec §27
- DevSpec 落点：DevSpec §27.1
- DevTasks（TASK）：TASK-0219
- 验收测试（T-xx）：T-PERF-AUDIT-01~02
- 当前进展（2026-03-05）：T-PERF-AUDIT-01~02（5 个测试）全部通过；缓存命中验证、缓存失效后重新填充、指定表/全量清空均已验证；全量测试 1802 passed，0 failed，零回归。
- 覆盖状态：DONE

## R-166
- 系统级需求：asyncio 兼容性加固——`edge_client/actuators/vts_driver.py` 无 `asyncio.get_event_loop()` 调用（Python 3.10+ DeprecationWarning）；已使用 `asyncio.new_event_loop()` + `asyncio.run_coroutine_threadsafe()` 替代；全量测试 0 DeprecationWarning
- PRD 需求编号（FR）：FR-COMPAT-001
- 来源：PRD §4.32；DevSpec §28
- DevSpec 落点：DevSpec §28.1
- DevTasks（TASK）：TASK-0220
- 验收测试（T-xx）：T-COMPAT-01
- 当前进展（2026-03-05）：T-COMPAT-01（3 个测试）全部通过；vts_driver.py 无 get_event_loop() 调用、使用推荐 API、整个 edge_client 目录无 DeprecationWarning 来源均已验证；全量测试 1802 passed，0 failed，零回归。
- 覆盖状态：DONE

## R-166（M27 情感链路端到端连通）
- 系统级需求：M27 P0 情感链路修复——GateSnapshot 情感字段、extract_emotion_tag mood_state 约束、build_governance_snapshot 情感透传、EpisodicRecord 情感标注、BondDynamicsWriter 情感加权、derive_bond_summary Bug 修复、RecallFusion 情感路由、IntentDecisionEngine 情感门控、SkillLoader/ToolBroker 情感上下文、NatsumeScheduler 情感意图（11项P0修复）
- PRD 需求编号（FR）：FR-GOV-EMOTION-001；FR-QA-EMOTION-001；FR-GOV-EMOTION-002；FR-MEM-EMOTION-001；FR-BOND-EMOTION-001；FR-BOND-SUMMARY-001；FR-RECALL-EMOTION-001；FR-GOV-EMOTION-003；FR-SKILL-EMOTION-001；FR-TOOL-EMOTION-001；FR-SCHED-EMOTION-001
- 来源：SOUL-OPT-2026-03-05 优化19/20/22/25/26/27/31/32/37/38/43
- DevSpec 落点：DevSpec §M27
- DevTasks（TASK）：TASK-0232~0243
- 验收测试（T-xx）：T-GATE-SNAP-01~03；T-EXTRACT-TAG-01~03；T-GOV-SNAP-01~02；T-EPISODIC-EMO-01~04；T-BOND-DYN-EMO-01~03；T-BOND-SUM-01~03；T-RECALL-EMO-01~03；T-INTENT-EMO-01~03；T-SKILL-EMO-01~02；T-TOOL-EMO-01~02；T-SCHED-EMO-01~02
- 当前进展（2026-03-05）：M27 全部 35 个新增测试通过；全量测试 884 passed，0 failed，零回归；情感链路 P0 断路点全部修复，情感状态从感知层→记忆层→决策层→执行层完整流通。
- 覆盖状态：DONE

## R-167（M28 核心情感架构深化）
- 系统级需求：M28 P1 优化——EmotionVector 情绪连续体、Bond 积分分级、Governor R8 情感门控、VTS 表情映射16种、TTS intensity 修复、全链路情感上下文透传（19项P1优化）
- PRD 需求编号（FR）：FR-EMOTION-CONTINUUM-001；FR-BOND-GRADE-001；FR-GOV-R8-001；FR-VTS-EXPRESSION-FULL-001；FR-TTS-INTENSITY-001；FR-CONTEXT-SLOTS-001；FR-COMPRESS-EMOTION-001；FR-DISTILL-EMOTION-001；FR-TIMELINE-EMOTION-001；FR-CAMERA-EMOTION-001；FR-TOOL-EMOTION-FILTER-001；FR-RERANK-EMOTION-001；FR-PIPELINE-EMOTION-001；FR-LLM-EMOTION-001；FR-GRAPH-EMOTION-001
- 来源：SOUL-OPT-2026-03-05 优化1/6/7/8/13/14/16/21/28/29/33/34/35/39/40/41/44/45/47
- DevSpec 落点：DevSpec §M28
- DevTasks（TASK）：TASK-0244~0262
- 验收测试（T-xx）：T-EMO-VEC-01~06；T-EMO-TAG-STR-01~03；T-BOND-GRADE-01~04；T-GOV-R8-01~04；T-EVOLVE-EMO-01~03；T-BOND-TREND-01~03；T-OPT-SLOTS-01~04；T-BOND-SUM-CN-01~02；T-COMPRESS-USER-01~02；T-DISTILL-EMO-01~02；T-TIMELINE-EMO-01~02；T-VTS-MAP-01~03；T-CAM-EMO-01~02；T-TOOL-META-EMO-01~02；T-RERANK-EMO-01~02；T-PIPELINE-EMO-01~02；T-LLM-GW-EMO-01~02；T-GRAPH-EMO-01~02；T-TTS-INTENSITY-FIX-01~02
- 当前进展（2026-03-05）：M28 全部 82 个新增测试通过；全量测试 838 passed，0 failed，零回归；修复 BondDynamicsWriter._stage_engine 属性缺失 Bug；修复旧测试中英文枚举兼容性问题。
- 覆盖状态：DONE

## R-168（M29 情感体验增强）
- 系统级需求：M29 情感体验增强——BondEmotionalTexture 情感质感层、情感上下文加权召回、动态槽位注入、主动触发器、时间线重建、LLM 感知参数调整、裂痕修复机制、情感回声、三档衰减配置、payload 语义标准化、bond_summary 语义校验、情感感知降级、调度心跳情感门控、融合策略情感加权、ASR 情感推断字段、指标情感维度、Edge 事件情感字段（19项增强）
- PRD 需求编号（FR）：FR-BOND-TEXTURE-001；FR-EPISODIC-EMOTION-RECALL-001；FR-EMOTION-PULSE-SLOT-001；FR-EMOTION-TRIGGER-001；FR-EMOTION-TIMELINE-001；FR-LLM-GEN-PARAM-001；FR-BOND-RIFT-001；FR-EMOTION-ECHO-001；FR-EPISODIC-DECAY-001；FR-BOND-PAYLOAD-STD-001；FR-EMOTION-TS-001；FR-BOND-SUMMARY-SEMANTIC-001；FR-FALLBACK-EMOTION-001；FR-SCHED-EMOTION-001；FR-FUSION-EMOTION-001；FR-ASR-EMOTION-001；FR-METRICS-EMOTION-001；FR-EDGE-REPORTER-EMOTION-001
- 来源：SOUL-OPT-2026-03-05 M29 情感体验增强任务集
- DevSpec 落点：DevSpec §M29
- DevTasks（TASK）：TASK-0264~0282
- 验收测试（T-xx）：T-BOND-TEXTURE-01~03；T-EPISODIC-EMOTION-01~02；T-EMOTION-PULSE-01~02；T-EMOTION-TRIGGER-01~02；T-EMOTION-TIMELINE-01~02；T-LLM-GEN-PARAM-01~03；T-BOND-RIFT-01~02；T-EMOTION-ECHO-01~02；T-EPISODIC-DECAY-01~02；T-BOND-PAYLOAD-STD-01~02；T-EMOTION-TS-01；T-CP-BOND-STAGE-01~02；T-FALLBACK-EMOTION-01~03；T-SCHED-EMOTION-01~02；T-FUSION-EMOTION-01~02；T-ASR-EMOTION-01~02；T-METRICS-EMOTION-01~02；T-EDGE-REPORTER-EMOTION-01~02
- 当前进展（2026-03-05）：M29 全部 46 个新增测试通过；全量测试 884 passed，0 failed，零回归；情感体验增强系统覆盖 Edge→Host 完整链路。
- 覆盖状态：DONE


## M41 Evidence Plane 写入压力评估与分级写入优化（R-169~R-175）

## R-169（M41-A Evidence Plane 写入延迟基准测量）
- 系统级需求：Evidence Plane 写入延迟基准测量工具——`EvidenceBench` 类提供 `measure_write_latency(method, n=1000)` 方法，返回 `{p50, p95, p99, mean, max}` 延迟统计，覆盖 `write_route_token`、`write_branch_manifest`、`write_barrier_join`、`write_render_event` 四个核心写入方法，为分级写入策略提供基准数据
- PRD 需求编号（FR）：FR-EVP-BENCH-001
- 来源：LangGraph_Governed_Runtime_Solution §11.1；DevTasks TASK-0417
- DevSpec 落点：DevSpec §M41
- DevTasks（TASK）：TASK-0417
- 验收测试（T-xx）：T-EVP-BENCH-01~04
- 当前进展（2026-03-06）：`host_brain/integrations/evidence_bench.py`（EvidenceBench 类）已落地；T-EVP-BENCH-01~04 全部通过。
- 覆盖状态：DONE

## R-170（M41-B Evidence Plane 写入优先级分级）
- 系统级需求：Evidence Plane 写入优先级分级枚举——`WritePriority(Enum)` 包含 `CRITICAL`（同步必写）和 `NON_CRITICAL`（可异步）两级；分级规则：`write_route_token`/`write_branch_manifest`/`write_barrier_join` → `CRITICAL`；`write_llm_call`/`write_subgraph_trace`/`write_render_event` → `NON_CRITICAL`；各 `write_*` 方法新增 `priority` 参数
- PRD 需求编号（FR）：FR-EVP-PRIORITY-001
- 来源：LangGraph_Governed_Runtime_Solution §11.1；ModelPlane.md §6；DevTasks TASK-0418
- DevSpec 落点：DevSpec §M41
- DevTasks（TASK）：TASK-0418
- 验收测试（T-xx）：T-EVP-PRI-01~06
- 当前进展（2026-03-06）：`graph_evidence.py` 新增 `WritePriority` 枚举及各方法 `priority` 参数；T-EVP-PRI-01~06 全部通过。
- 覆盖状态：DONE

## R-171（M41-C Evidence Plane 异步写入队列）
- 系统级需求：Evidence Plane 异步写入队列——`EvidenceWriteQueue` 类提供 `enqueue(write_fn, *args, **kwargs)`、`flush(timeout_ms=500)`、`queue_size()` 方法；内部使用 `collections.deque` 缓冲；`NON_CRITICAL` 写入自动路由到队列，`CRITICAL` 写入保持同步直写；队列满时（超过 `max_size`）触发 `flush()` 后再入队
- PRD 需求编号（FR）：FR-EVP-QUEUE-001
- 来源：LangGraph_Governed_Runtime_Solution §11.1；ModelPlane.md §6；DevTasks TASK-0419
- DevSpec 落点：DevSpec §M41
- DevTasks（TASK）：TASK-0419
- 验收测试（T-xx）：T-EVP-QUEUE-01~06
- 当前进展（2026-03-06）：`host_brain/integrations/evidence_write_queue.py`（EvidenceWriteQueue 类）已落地；`graph_evidence.py` 集成异步路由；T-EVP-QUEUE-01~06 全部通过。
- 覆盖状态：DONE

## R-172（M41-D Evidence Plane 写入失败熔断机制）
- 系统级需求：Evidence Plane 写入失败熔断机制——`EvidenceCircuitBreaker` 类实现 `CLOSED`/`OPEN`/`HALF_OPEN` 三态状态机；`failure_threshold`（默认 5）、`recovery_timeout_ms`（默认 10000）可配置；熔断时写入转入内存缓冲 `_memory_buffer`；`HALF_OPEN` 状态允许单次探测写入；`CRITICAL` 写入在 `OPEN` 状态下仍写入内存缓冲不丢失；`get_buffer_snapshot()` 返回缓冲记录
- PRD 需求编号（FR）：FR-EVP-CB-001
- 来源：LangGraph_Governed_Runtime_Solution §11.1；ModelPlane.md §6；DevTasks TASK-0420
- DevSpec 落点：DevSpec §M41
- DevTasks（TASK）：TASK-0420
- 验收测试（T-xx）：T-EVP-CB-01~08
- 当前进展（2026-03-06）：`host_brain/integrations/evidence_circuit_breaker.py`（EvidenceCircuitBreaker 类）已落地；`graph_evidence.py` 集成熔断保护；T-EVP-CB-01~08 全部通过。
- 覆盖状态：DONE

## R-173（M41-E Evidence Plane 分区索引优化）
- 系统级需求：Evidence Plane 分区索引优化——为 `barrier_join_log`（`idx_bjl_run_id`/`idx_bjl_ts`）、`subgraph_trace`（`idx_sgt_run_id`/`idx_sgt_parent_path`）、`llm_call_log`（`idx_lcl_run_id`/`idx_lcl_node_id`）三张高写入量表新增 6 个分区索引；`_ensure_tables()` 重复调用时索引创建幂等（`IF NOT EXISTS`）
- PRD 需求编号（FR）：FR-EVP-INDEX-001
- 来源：LangGraph_Governed_Runtime_Solution §11.1；ModelPlane.md §6；DevTasks TASK-0421
- DevSpec 落点：DevSpec §M41
- DevTasks（TASK）：TASK-0421
- 验收测试（T-xx）：T-EVP-IDX-01~06
- 当前进展（2026-03-06）：`graph_evidence.py` `_ensure_tables()` 新增 6 个分区索引；T-EVP-IDX-01~06 全部通过。
- 覆盖状态：DONE

## R-174（M41-F Evidence Plane 写入压力专项测试）
- 系统级需求：Evidence Plane 写入压力专项测试——验证分级写入策略、熔断机制、异步队列在高并发写入场景下的正确性（mock 模拟，不依赖真实环境）；覆盖 100 次 CRITICAL 同步写入、100 次 NON_CRITICAL 队列写入、混合写入、失败注入熔断、熔断恢复后内存缓冲重放五个场景
- PRD 需求编号（FR）：FR-EVP-PERF-001
- 来源：LangGraph_Governed_Runtime_Solution §11.1；ModelPlane.md §6；DevTasks TASK-0422
- DevSpec 落点：DevSpec §M41
- DevTasks（TASK）：TASK-0422
- 验收测试（T-xx）：T-EVP-PERF-01~05
- 当前进展（2026-03-06）：`tests/unit/test_evidence_perf_m41.py` 已落地；T-EVP-PERF-01~05 全部通过。
- 覆盖状态：DONE

## R-175（M41-G/H 契约测试更新 + 代码审查报告）
- 系统级需求：M41 收口——`graph_evidence.py` 行数超出后契约测试行数限制更新（TASK-0423）；M41 代码审查报告生成（TASK-0424），包含 TASK-0417~0423 全部 7 个任务变更摘要、≥29 个新增测试全部通过、`WritePriority`/`EvidenceWriteQueue`/`EvidenceCircuitBreaker`/分区索引完整落地、全量测试无回归
- PRD 需求编号（FR）：NFR-MAINT-001
- 来源：LangGraph_Governed_Runtime_Solution §11.1；DevTasks TASK-0423~0424
- DevSpec 落点：DevSpec §M41
- DevTasks（TASK）：TASK-0423；TASK-0424
- 验收测试（T-xx）：T-EVP-BENCH-01~04；T-EVP-PRI-01~06；T-EVP-QUEUE-01~06；T-EVP-CB-01~08；T-EVP-IDX-01~06；T-EVP-PERF-01~05（全量 ≥29 项）
- 当前进展（2026-03-06）：契约测试行数限制已更新；`docs/35_quality/code_reviews/code_review_m41_2026-03-06.md` 已生成；全量测试无回归。
- 覆盖状态：DONE

## R-176（2026-03-18 白皮书口径联动：主链路优先 + 受控多 Agent）
- 系统级需求：系统默认“主链路优先”，多 Agent 仅可作为受治理约束的受控分支，不得替代 Host 终审与统一审计链。
- PRD 需求编号（FR）：FR-CORE-001；FR-GOV-001；FR-MODE-001
- 来源：whitepaper 0/5/6（v1.5）
- DevSpec 落点：DevSpec 2.1；DevSpec 14.4
- DevTasks（TASK）：待补（当前为文档口径对齐，未新增实现任务）
- 验收测试（T-xx）：T-Gov-01；T-Orch-01
- 当前进展（2026-03-18）：PRD/DevSpec 已同步“主链路优先 + 受控多 Agent”口径，等待后续任务卡补齐编号映射。
- 覆盖状态：SPEC_READY

## R-177（2026-03-18 白皮书口径联动：R1~R8 与六模式 sys_mode）
- 系统级需求：Governor 门禁规则对齐为 R1~R8（新增 Emotion Gate）；sys_mode 对齐为 normal/focus/gaming/maintenance/locked/sleep 六模式，并映射到调度与路由门控。
- PRD 需求编号（FR）：FR-GOV-001；FR-MODE-001~003
- 来源：whitepaper 6/11/13（v1.5）
- DevSpec 落点：DevSpec 4；DevSpec 14.4；DevSpec 15.1.3
- DevTasks（TASK）：待补（当前为文档口径对齐，未新增实现任务）
- 验收测试（T-xx）：T-Gov-01；T-GOV-R8-01~04；T-SCHED-02
- 当前进展（2026-03-18）：PRD/DevSpec 已同步 R1~R8 与六模式 sys_mode 门控矩阵，测试映射已登记。
- 覆盖状态：SPEC_READY

