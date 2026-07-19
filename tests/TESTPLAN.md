受众：CODEX
标题：TESTPLAN（测试契约索引）

版本：v0.7
日期：2026-07-16

说明：本文件维护稳定测试编号与断言口径。新增编号只代表契约已定义；只有对应测试代码和执行证据存在时才可标记为已覆盖。

## T-Intent
- T-18：Intent 宽松解析 + 严格校验。
- T-19：重放保护（nonce/request_id）拒绝。
- T-20：顺序校验失败拒绝。
- T-21：过期拒绝。
- T-22：失败映射 reason_code 稳定。
- T-Intent-01：intent_log 具备 proposer/model/prompt/tool_visibility 最小字段。
- T-Intent-02：proposal/decision/exec 三类日志字段分离。

## T-LLM
- T-LLM-01：单个合法 JSON；禁止前后缀文本；字段完整。

## T-Audit
- T-Audit-01：intent/governor/skill_exec 三联可串联回放。
- T-Audit-RET-01: Retention/GC must preserve triptych replay chain keys and active undo windows; pruned rows and blocked GC actions must remain auditable.

- T-Audit-03: Request-level replay diagnostics must explain missing links across intent/governor/skill chains with stable reason_code mapping.
- T-Replay-01: Given a request_id, replay tool must output deterministic report fields (`status/reason_code_chain/evidence_refs`) and enumerate missing-link causes (`TRACE_MISSING_*`).

## T-Gov
- T-Gov-01：R1~R7 rule_trace 具备 gate_id/hit/threshold_summary/snapshot_summary/reason_hint 且同输入稳定。

## T-Meta
- T-Meta-01: Hard-state metabolism normalization/evolution must be deterministic, and governor rows must carry replayable snapshot_refs (`governance_snapshot` + `hard_state`) derived from the same runtime snapshot input.

## T-QA
- T-67：格式漂移触发一次重写/降级。
- T-68：伪执行宣告拦截。
- T-69：Canon-as-fact 拦截。
- T-70：故障降级仍输出合法 JSON。
- T-71：超时降级仍输出合法 JSON。

## T-Drill
- T-Drill-01：生产演练包必须对 `cold_start_no_key/provider_down/rollback_conflict` 输出可解析单 JSON 结果，并稳定包含 `scenario_id/status/reason_code_chain/replay_ref` 与故障分类处置口径。

## T-Canon

## T-WCanon
- T-WCanon-01：World Canon 文件同步索引到 `world_canon_doc` 且写 `canon_change_log`。
- T-WCanon-02：World Canon 仅以引用口径注入 authoritative（citation_only）。
- T-WCanon-03：World Canon 文件与索引 version/hash 漂移可检测，坏文件不得静默跳过（需可审计）。

## T-Mem
- T-Mem-01：Episodic 写入无 `evidence_refs` 必失败且有稳定 `reason_code`。
- T-Mem-02：`merge/decay/delete` 全生命周期必须写 `memory_ops_log` 且可回放。

## T-Vault
- T-Vault-01：`reversible=true` 的执行路径必须产出 `manifest_ref`，缺失时稳定拒绝。
- T-Vault-02：manifest digest 失配时撤销拒绝且可审计；GC 不得破坏有效 undo 窗口。

## T-Airlock
- T-39: Egress must include sanitization_report_json (removed_fields/redactions/payload_digest), otherwise reject with AIRLOCK_BLOCKED_EGRESS and log airlock_log.
- T-40: Ingress content must be quarantined and classified (FACT/DRAFT_CODE/DRAFT_INSTRUCTION/UNKNOWN) and never directly execute.

## T-Sandbox
- T-Sandbox-01: DRAFT_CODE sandbox execution must produce structured results (sandbox_id/status/exit_code/stdout/stderr/duration_ms/evidence_ref/code_digest), enforce timeout kill and resource limits with stable reason_code, maintain process isolation (no main-process side effects), and write sandbox_exec_log for audit replay; existing T-39/T-40 must not regress.

## T-Tool
- T-Tool-01: ToolMeta pack is metadata-only (no execution payload), with required fields and context-pack validation compatibility.
- T-Tool-02: Top-K selection only exposes a subset and hides WRITE tools by default; tool_visibility_hash is deterministic for same input.
- T-Tool-03：原始 `ToolBroker.execute()` 与 `execute_bound()` 对已登记/未知 READ/WRITE 均固定 `GOVERNED_TOOL_TASK_AUTHORITY_REQUIRED`，handler 与 TokenService 调用为 0，调用方旧 Token 原文不得进入 ledger 或 result。正向组件测试只能通过 app-local `ToolRuntimeBoundary` 签发的 governed execution port，并明确声明它不是 Task authority 证据；生产正向测试必须证明 Broker 本身没有端口工厂、只有 Frozen Tool Worker 取得该 port。handler 前，`TaskStore.assert_live_node_execution_authority` 必须在单个 SQLite 读事务内原子复验 Graph/Node/Attempt/Lease/Reservation 与 owner/fence/expiry；node grant 和 exact binding 随后分阶段 fail-closed 复验，不能把三段当成同一原子区间。只有全部检查通过后，`before_handler` 才能在实际 handler 前写入 `PENDING` dispatch marker，并绑定 `SAFE_LOCAL_REPLAY`。另需固定当前启动顺序边界：Tool seed readiness 早于 governed runtime activation；executor=0 时不得误报 `READY`，首个真实 executor 启用前必须由同一 readiness 状态机覆盖 activation。

- T-Tool-04：`before_handler` 必须位于 `SAFE_LOCAL_REPLAY` 和 WRITE child token 校验成功之后、handler 调用之前；Graph/Attempt/Lease/Reservation 失效或 WRITE token 无效时，dispatch marker 与 handler 调用都必须为 0。

## T-MCP
- T-MCP-01：MCP provider 只同步 metadata 到 registry/scorecard；process-owned `get_mcp_tool_broker` accessor 与 `mcp_tool_broker` container slot 必须不存在，`MCPAdapter`、未进入 ASGI 的兼容 `MCPToolBroker` 与 MCP Skill bridge 均不得保留 callback 或调用 provider，READ/WRITE/有效或伪造旧 Token/未知 tool 都固定 `MCP_TASK_AUTHORITY_REQUIRED`。bridge 默认关闭并 purge stale `mcp::*`；旧持久图必须在 registry lookup 前以同一 reason 拒绝。真实 MCP 执行只有编译为 governed Tool Task 后才可新增正向测试。

## T-Orch
- T-Orch-01: Graph orchestration runtime must validate graph config deterministically (`graph_id/version/entry_node/node_bindings/edges/fallback_edges`), emit stable execution envelope (`graph_status/reason_code/stage/replay_ref`), and classify node-missing/edge-invalid/timeout/dependency-down failures with replayable trace.

## T-Channel
- T-Channel-01: WeChat ingress/egress events are traceable by thread_key/message_id/request_id; duplicate ingress is ignored and failed egress includes reason_code/stage.

## T-Avatar
- T-Avatar-01: VTube Studio talk gate (TALK_START/TALK_END) and emotion_tag->expression_id mapping are deterministic and auditable; invalid tags or free-form action params must be rejected with stable reason_code and stage.
- T-Avatar-02: Host `resolve_vts_expression` must keep `DEGRADED + neutral` fallback stable when Edge is unavailable or emotion tags are unmapped.
- T-Avatar-03: Host/Edge `EMOTION_TO_EXPRESSION_ID` contracts must remain aligned for the active 16-tag mapping set.

## T-ClawdUI
- T-41: ClawdBot UI action with confidence < 0.80 must be blocked; no click side effect is produced and clawd_action_log records confidence/target_desc/reason_code.
- T-42: Redline scene and missing UI element must be rejected with stable reason_code (VISUAL_REDLINE/UI_ELEMENT_NOT_FOUND) and guard stage in clawd_action_log.
- T-43: Successful UI action must produce before/after artifacts and manifest linkage; clawd_action_log stores before_artifact_ref/after_artifact_ref/manifest_ref for replay.

## T-Svc
- T-Svc-01: Service registry health output must be parseable and deterministic; same unavailable kind maps to the same reason_code/stage, and each health row is auditable by request_id in service_health_log.
- T-Boot-01: Startup orchestration must output deterministic boot snapshot (`service_id/owner/required/status/reason_code/stage`), include dependency probe matrix, and keep config/probe failures auditable with stable reason_code mapping.
- T-Infra-01: Nine-node deployment inventory must remain deterministic (`node_id/region/role/services/health_endpoint/key_profile_id`), required-service coverage and role conflicts must be rejectable with stable reason_code, and node probe timeout must map to stable `reason_code/stage`.
- T-EnvReady-01: Environment readiness report must be deterministic (`env/node_count/key_profiles/services/graph_runtime/readiness_status`), include dependency matrix and drill summaries, and classify partial-ready vs critical-down with stable reason_code while preserving replay references.

## T-Config
- T-Config-01: Host/Edge layered config loading (env/default/local override) must produce deterministic stage/reason_code on invalid inputs, keep cold-start defaults parseable, and map runtime unavailability consistently into service health outputs.
- T-Config-02: Env snapshot export must produce deterministic redacted output (`env/version/digest/override_sources/redacted_keys`), support stable snapshot diff (`added/removed/changed`), and keep audit replay rows queryable by `request_id`.

## T-Key
- T-Key-01: Key-profile based secret loading must resolve `secret_ref` by `provider/node_id/scope`, reject scope mismatch/expired/missing secret with stable reason_code, and keep config snapshots redacted while exposing only `*_key_ref` linkage.

## T-Ret
- T-Ret-01: Embedding contract output is stable and auditable; unavailable/timeout/input-invalid must map to deterministic `reason_code` (`RETR_EMBED_UNAVAILABLE/RETR_EMBED_TIMEOUT/RETR_EMBED_INPUT_INVALID`) under `S_RET_EMBEDDING`, and each call must write `retrieval_event_log`.
- T-Ret-02: Reranker contract keeps evidence candidates shape stable and auditable; unavailable path degrades with deterministic reason_code/stage.
- T-Ret-03: Evidence pack fusion output is schema-stable and replayable by `request_id/intent_id/evidence_ref`, including degraded retrieval paths.

## T-Chroma
- T-Chroma-01: Chroma external runtime config/probe contract must be deterministic (`endpoint/tenant/database/collection_profiles/default_collection`), and endpoint-down/collection-missing/vector-dim-mismatch failures must map to stable `reason_code/stage`; collection governance must enforce configured vector dimensions.

## T-Embed
- T-Embed-01: Embedding runtime routing config must be deterministic (`provider/model_id/dim/timeout_ms/node_selector`), multi-provider route selection must be stable by node selector, and unavailable/timeout/dim-drift failures must map to stable `RETR_EMBED_*` reason codes with auditable expected-vs-actual dim fields.

## T-Rerank
- T-Rerank-01: Reranker runtime routing config must be deterministic (`provider/model_id/top_k/timeout_ms/node_selector`), score normalization and fallback must stay stable, and model-down/timeout/invalid-score-range failures must map to stable `RETR_RERANK_*` reason codes with auditable `expected_top_k` payload linkage.

## T-Bond
- T-50: Bond SSOT updates can only be applied through the single-writer path (`bond_dynamics`) and must create replayable `event_id` with `evidence_refs`.
- T-51: Bond update requests without `evidence_refs` must be rejected with stable reason_code and logged in `bond_update_log`.
- T-52: Runtime single-writer boundary for Neo4j must reject non-`bond_dynamics` writers with stable reason_code.
- T-53: Bond events and graph relations (`HAS_EVENT/ABOUT/AFFECTS`) must be replayable by `user_id` and `event_id`.
- T-54: Neo4j bond constraints/index assets must be loadable from migrations, and summarizer path must not perform SSOT writes.
- T-Neo4j-01: Neo4j external deployment config/probe must be deterministic (`uri/database/auth_ref/pool/tls/enabled`), and auth/schema/leader failures must map to stable reason_code with migration-log audit linkage.
- T-74: Bond summary must be derived-only and must not contain sensitive numeric values; only bucketed fields plus replay refs (`event_id/evidence_refs`) are allowed.
- T-75: Bond summary must inject into `prompt_context.bond_summary` slot without mixed-slot leakage and remain valid under `T-CP-01` isolation checks.

## T-SSOT
- T-SSOT-01: Static scan must detect and block non-single-writer Neo4j write paths (`write_bond_event` outside `bond_dynamics`) without relying on external services.

## T-Migrate
- T-Migrate-01: SQLite/Bond migration runners must enforce idempotent apply, detect catalog gaps and schema drift, reject rollback version regressions with stable reason_code, and emit auditable records containing `migration_id/status/reason_code/latency_ms`.

## T-Voice
- T-Voice-01: `asr_chunk` event schema is deterministic and auditable; missing fields, out-of-order seq, and throttled ingress are stably rejected, while low confidence is accepted with explicit marker and stable reason_code/stage for replay.
- T-Voice-02: `speak -> synth -> playback` queue is deterministic and auditable; synthesis/playback failures emit stable `reason_code/stage` while preserving replay linkage by `request_id/session_id`.
- T-Voice-03: `barge_in` interrupts active playback immediately and leaves auditable interruption events; ASR ingress remains unblocked and replayable in the same session.
- T-Voice-04: Edge fallback LLM endpoints (`presence/asr_compile/retrieval_hints/context_compress`) must keep `draft_only=true`, reject decision fields and false execution claims, and return stable fast-fail reason_code/stage when unavailable.
- T-Voice-05: Resource gate enforces priority `ASR > PLAYBACK > TTS_SYNTH > FALLBACK_LLM` with mutex under pressure, exposes `vram/queue` metrics in gate events, and keeps preempt/reject decisions replayable.

## T-RPC
- T-RPC-Event-01: Edge->Host event reporting protocol must keep deterministic schema (`event_type/source/ts_ms/payload/evidence_ref`), support cache/retry/backfill on network interruption, and preserve replay linkage after recovery.

## T-Edge
- T-Edge-RPC-01: Edge RPC envelope authentication/replay protection must enforce schema + HMAC + ts window + nonce uniqueness with stable reason_code mapping (`EDGE_RPC_SCHEMA_INVALID`/`EDGE_RPC_AUTH_INVALID`/`EDGE_RPC_AUTH_EXPIRED`/`REPLAY_DETECTED`), and support dev/test insecure-auth fallback without breaking schema checks.
- T-Edge-RPC-02: Edge RPC gateway endpoints must return deterministic response envelope (`success/status/reason_code/detail/event/ts_ms`) and reject disabled capability calls with auditable reason_code/stage.
- T-Edge-Health-01: Edge health endpoint must expose stable capability matrix + resource snapshot + component health + redacted config snapshot, consistent with config switches and fallback defaults.
- T-Edge-Start-01: `python -m edge_client` startup/shutdown must be deterministic (bind host/port, emit config snapshot, graceful close of ASR/TTS/VTS resources) and leave no stale runtime lock/process state.
- T-Edge-Voice-01: Edge ASR upstream pipeline must batch-report events to Host with retry/cache/backfill semantics, preserving deterministic schema and replay linkage during network interruption and recovery.
- T-Edge-Voice-02: Edge barge-in + VTS linkage must deterministically interrupt playback, produce `VOICE_INTERRUPTED`-class audit evidence, and restore talk gate/expression terminal state.
- T-Edge-Fixture-01: Edge fixture naming must follow `tests/fixtures/edge/{auth|gateway|health|voice}/{good|bad}/scenario_expectation.json` with expectation suffix in `{accept|reject|degraded}` and each `T-Edge-*` id mapped to success + reject/degraded samples.

## T-Vision
- T-VisionGate-01: Gaming mode must block screen-monitor/OCR requests with stable reason_code/stage and auditable event linkage; response must not claim unsupported visual observations.
- T-Vision-01: Non-gaming OCR output must remain schema-stable (`request_id/ts_ms/source_ref/engine_id/text_blocks[]` with `text/bbox/conf`) and source_ref must be replayable via vision_event_log.
- T-Vision-02: Camera recognition events must be replayable by evidence_ref, include explicit low-confidence marking in object outputs, and degrade/reject with stable reason_code/stage when evidence is missing or detector is unavailable.
- T-Vision-03: Multi-detector registration and aggregation must produce stable detections[] with per-detector evidence_ref, partial degradation (PARTIAL) when some detectors fail, and ALL_FAILED when none succeed; existing T-Vision-02 must not regress.

## T-CI
- T-CI-01: Unified CI delivery gate must classify failures across `contract/e2e/static_scan/doc_gate` with deterministic report fields (`job_id/check_id/reason_code/failed_artifact`).

## T-Release
- T-Release-01: Release checklist for `M0~M3` must remain deterministic (`milestone/assets/checksums/required_services`), and rollback mapping must resolve `release_id/rollback_target/impact_scope`.

## T-NextGen-Critical

- T-GR-RUNTIME-01：Host Chat/Input/Gateway、Scheduler、Internal Agent 与 Operator Command 的真实 route matrix 必须全部生成 `WorkOrderV3 + BudgetPolicy + TaskGraph + node-bound grant + Verification + Acceptance + Receipt`；至少执行每路首个真实 admission，startup-only 测试不能通过本 Gate。Operator Review continuation 还必须从 app-local OBS Outcome Journal 只读复验 outer receipt 的 checksum-chain 与 claim/audit/command/action/scopes/body/terminal/domain-result 绑定；lookup 缺失、抛错、无记录和篡改必须 fail closed。

  当前 NE-01 第三切片的受影响面集中回归覆盖 ReAct、Obs、Operator、Tool、Task composition、ASGI、迁移与文档合同，共 240 项通过；这是切片证据，不替代 NE-01 波次集成或 NE-07 真实服务栈验收。
- T-NE01-HOST-LIFECYCLE-01：每个 `create_app()` 必须构造独立 `HostLifecycleContainer`；ActivityMonitor、ResourceManager 与 ModeTransition 仅在所属 lifespan 的 factory/start 全部成功后发布，Activity factory 失败不得发布 Mode，部分启动失败立即回收。`aclose()` 必须先设置 closing fence 并撤销发布，closing 期间新的 `begin()` 失败，随后按 ResourceManager→ActivityMonitor exact-once 停止；ResourceManager startup sample 必须运行在唯一 owned loop，不得留下未跟踪线程。Chat/Input、Tool、Scheduler、Guardian heartbeat 与 lifecycle snapshot 必须读取选中的 app-local 实例；Obs 读路径不得懒启第二实例。Tribe application ports 必须在 Host lifespan 通过 process-global exact owner bind/unbind fence 管理：一个 live Host 可绑定，第二个同时存活的 Host 必须 fail closed，前者解绑后才可顺序重绑；不得把该测试写成并发 multi-app Tribe 隔离。`http_hooks.py` 直接 locator 导入保持 0，全局 monitor/resource setter、slot 与 lock 不得恢复。库存 Gate 必须分别报告：具名 `from process_services` 为 47 模块/152 导入名→45/124；所有 import 形式的 consumer modules 为 52→51；Container 为 56 fields/4 locks/51 mutable refs。本 Gate 不把仍存在的 process-wide locator、heartbeat cache/client、HardState、Tribe bus、共享 status file 或 Worker scope 记为完成，也不把 mixed-file Gate 通过写成 full architecture Gate PASS。
- T-NE01-FOCUS-01：两批 11 个原始 mixed/ceiling finding 必须低于 1,500 行或既有 cohesive ceiling；focused 子模块不得取得第二 SQLite connection/UoW、RuntimeEvent append、journal store、反向 owner import 或改变兼容 import identity。fresh 全仓 mixed-file Gate 必须为 `issues=[]`，禁止新增或放宽 allowlist 掩盖。
- T-NE01-RISK-01：TaskGraph admission 必须把每节点 1..5 `risk_level` 冻结到 `AdmittedTaskNode`，worker execution context 只消费该 immutable 值；缺失、布尔值或越界风险等级在 worker/副作用前失败关闭。
- T-NE01-BUDGET-PROJECTION-01：Chat route executor 必须在 terminal commit 前精确验证 frozen model budget guard 与 measurement；公开 WorkerProjection 不得包含已消费的 `governed_model_budget` 或其 `max_*_tokens`，但必须持久化 typed `budget_usage.measurements`。不得通过放宽敏感 token 字段规则使测试通过。
- T-NE01-PREVIEW-REOPEN-01：生产等价 Chat/Delegate/三节点 Plan + exact replay 必须构造 durable Host lease、Runtime Context composition 与 Production ActionPreview resolver；覆盖 Scene digest 确定性投影、同 lease expiry 单调延长、缩短与 scope/owner/lease/fence/revision 漂移拒绝，以及 terminal proof 注入后 boundary `action_args` 无循环别名。Gateway reply 只能在第三次 terminal reopen 成功后发送一次。
- T-NE01-TASK-WRITE-AUTHORITY-01：`DurableTaskStore` 不得暴露 public `create_graph`、`insert_graph_in_transaction` 或 `stage_acceptance_effect_intent`。生产 Graph 私有 hook 只能由 `HostCommandRepository._admit_normalized()` 以 `self._task_store + conn` 在同一 admission UoW 调用一次；Graph/Node `INSERT` SQL 只能位于该 hook。Effect 私有 hook 只能由 `transition_graph_and_stage_acceptance_effects()`、`commit_node_acceptance()`、`complete_parallel_graph_success()` 三个 canonical UoW 以当前 `conn` 调用，共 6 个静态调用；Effect `INSERT` SQL只能位于该 hook。production 不得导入 `tests/task_runtime_authority_fixtures.py`；该 fixture 只构造无 `host_command_journal` 的 raw/orphan Store 状态，不是 canonical admission。当前 Gate 只能将应用 Python 写 authority 定级为 `APPLICATION_PYTHON_WRITE_AUTHORITY_SEALED`；SQLite 仍允许 orphan/raw SQL且本轮无 migration/trigger，必须同时标记 `SQL_DIRECT_BYPASS_PRESENT`。
- T-NE01-SQLITE-BOUNDARY-01：0112 Runtime Context readiness 只能通过 `RuntimeEventStore` port 调用 `RuntimeEventRepository`，runtime 不得打开私有 SQLite；Service Catalog authority/runner 只能依赖 repository Protocol，连接、SQL 与事务只能由 `SQLiteServiceCatalogRolloutRepository` 持有。`ACTIVATION_STARTED` receipt 与 rollout head 必须同事务提交，任一写入故障必须共同回滚。World Observation 的 transition mutex 可以是 `RLock`，但不得以 `_write_lock` 等名称触发或掩盖 writer ownership；当前命名为 `_transition_lock`。global SQLite boundary 必须扫描生产 Python；`host_startup.py`/ASGI concrete wiring 作为已知边界保留，不能据 Gate 10 通过宣称 typed composition 或 NE-01 完成。
- T-NE01-OBS-LIFECYCLE-OWNER-01：`ObsApplicationContainer` 不得暴露泛化 `start()`，生产 `start_projection_jobs()` 必须恰好由 `obs_fastapi_app.py` 的嵌套 ASGI `lifespan` 调用一次。lifespan 顺序必须是 ports bundle 原子 single-use claim→compose app-local daemon→start→yield；finally 必须 stop/join/in-flight fence→释放 exact leader lease→close owned SQLite→clear app state。stop timeout 或 rollback close failure 必须保留原 container/ports 句柄供重试。两个 app 共享同一 bundle 必须 fail closed，`dataclasses.replace()` derived bundle 必须继承同一 claim；只有两个独立 ports/app 才可并行隔离。禁止 module-global daemon；本 Gate 不需要 migration。后台 daemon 未捕获 renew 等异常退出尚未由 `/health` 直接呈现 `running/last_failure`，因此 Gate 6 通过不得表述为 projection continuous-SLO 或 NE-01 完成。
- T-NE01-SERVICE-CATALOG-LAYERING-01：Service Catalog 启动协调器的 canonical owner 必须为 `service_catalog/startup_coordinator.py`，`composition/service_catalog_startup.py` 只能同对象重导出；稳定 DTO、校验与 port 必须归 `contracts/service_catalog.py`，旧类型路径只能同对象重导出；`rollout_authority.py` 只保留 Guardian observer，SQLite repository 只能依赖稳定 contracts。静态 Gate 必须证明 application→composition=0、Catalog persistence→application=0；该切片证据必须保留当时 persistence→application 11 条（PlanningSearch 7、Evolution/Lab/Skill/World 4）的快照，并由后续合同分层测试记录继续收敛，不能把本域清零写成 Composition 或 NE-01 完成。
- T-NE01-PLANNING-SEARCH-CONTRACT-LAYERING-01：core、benchmark、calibration、code、research 八组持久化共享合同/模型必须由纯 `contracts/planning_search/` 持有；旧 `planning_search` 同名模块只能同对象重导出，且 `__all__`、类型 identity、`isinstance`、序列化形状必须兼容。code benchmark、calibration、research shadow SQLite repository 不得导入 `host_brain.planning_search`。静态 Gate 必须证明本域 7 条 persistence→application 清零，同时如实报告 Gate 1 仍有 application/persistence SCC 与 Evolution/Lab/Skill/World 4 条反向依赖；不得把合同分层写成 PlanningSearch 功能、生产切流、收益、Composition 或 NE-01 完成。
- T-PERSONA-FENCE-01：NE-01 safety fence 后，Bond stage/milestone、interaction cycle、emergent weekly 和后续新增入口在 `PersonaEngine.apply_growth_delta()` 中央 authority 边界全部 fail closed，trait mutation 为 0；静态扫描证明不存在绕开中央门禁的新 writer，运行时负测覆盖全部已知入口。
- T-PERSONA-PROPOSAL-01：NE-06 replacement 后，旧 Persona growth caller 只生成 `FeedbackProposal`，并完成 proposal → Lab/shadow → Review → versioned activation → rollback；普通后台任务无直接 trait mutation authority。
- T-NE06-GOVERNANCE-01：0110 V1 ledger 只验证 Feedback/Knowledge/Identity 事件行的 append-only/replay/tamper；真实 Lab/Review authority resolution、version owner activation 和 exact previous-state rollback 必须由 V2 测试另行证明。
- T-DELIVERY-METADATA-01：Feature 分支新提交无 merge commit且标题为规范中文结果；PR 正文从 Markdown 文件读取，七章节完整有序、无字面量 `\\n`，远程额度状态只允许明确记为 `NOT_RUN_ACCOUNT_QUOTA`。
- T-NE07-FULL-STACK-01：最终验收使用独立 acceptance 数据根，真实构建 Monitor UI，并启动 Docker Neo4j/Chroma/Redis、Host、Obs、Nginx Monitor、完整 Edge 与 Unified Gateway；逐项完成真实读写、重启和端到端 receipt 对账，内存 fallback 不计通过。
- T-PLAN-PROD-01：Research/Code 使用真实 workload、真实 source/repository、真实 model/verifier、隔离 workspace、实际成本和延迟比较 baseline 与 PlanningSearch；synthetic fixture 不能计入，未执行候选不得复用 baseline outcome。
- T-PLAN-RESEARCH-D20A-01：Research pure-shadow catalog 只能从冻结的公开 source id/ref/digest 与有限 authority/relevance/read-cost metadata 动态生成 query、quick-answer、primary、independent、cross-check、conflict、stop；snapshot/payload 没有专用的本机 locator、source-body、answer-key 或 verifier-decision 字段，constraints 拒绝这些命名 key，但任务文字不是内容级 DLP。`virtual_transition_ready=false` 时标准 domain wiring 不得提供 L3 transition/reward。FrozenResearchCorpusLoader 只能读取可信实验根与 manifest 指定的 canonical repository-relative regular file，读前/读后必须在稳定受信输入状态中检测并拒绝绝对/遍历、ADS colon component、case alias、link/reparse、未列来源、类型/大小/digest 漂移，公开 receipt 不含路径/正文。它不是 hostile concurrent replacement 的原子 TOCTOU 防线，且本 Gate 不会把 snapshot 与 manifest 的 corpus/source id/ref/digest/tier 绑定或证明独立来源；该 handshake 属于 D20b。该 Gate 只证明 D20a 机械边界，不是 T-PLAN-PROD-01 的真实 source/model/verifier/paired-benefit 验收，也不是 OS/tenant sandbox。
- T-PLAN-CODE-D19-01：Code 实验的预注册 pytest argv 只能在 disposable detached worktree 中以 shell-free child 执行；必须拒绝 argv/path/budget drift，核验实际解释器后持久化 portable argv digest，强制 `PASS ↔ exit_code=0`，并把 command/exit/受限输出 digest/耗时与 exact run/arm/plan/budget、两 arm aggregate test/wall limit 和 `VERIFIED_CLEAN` 绑定。child 可能启动前必须预留其冻结 command budget，timeout/error 后同一 arm 不能重试；带 D19 plan 的通用 terminal ledger 也必须拒绝任何 FAIL arm。restart replay 必须拒绝损坏 receipt。该 Gate 不等于独立安全域、hermetic Python、模型/工具预算、crash cleanup recovery 或 `T-PLAN-PROD-01` 的收益验收。
- T-PLAN-CAL-01：Pentest/通用 L3 必须按虚拟状态动态生成后继，prediction/baseline/candidate/actual outcome/cost 跨 run durable；达到预注册样本、误差、安全与 uplift 门槛前 production influence 为 0。
- T-NE-FINAL-01：NE-07 聚合所有 Target Architecture Gate；任一 Target Gate 未满足时只能 `FAIL` 或精确的 `ACCEPTANCE_PENDING_EXTERNAL`，历史 receipt、shadow 完成或早期 wave 状态不能产生 `PASS`。

## T-NLE（统一 Runtime 与数字生命安全）

- T-NLE-01：架构基线可复现；Domain→Core、raw thought durable write 等禁止依赖可被静态检测，测试不得夹带任务范围外改动。
- T-NLE-02：Pentest 历史知识回填的计数、source hash、标准化内容和检索样本一致；domain mode 下 WorldCanon 直接读写为 0。
- T-NLE-03：Canon Apply 强制绑定 token/subject/version/expiry/once；缺失、过期、串用、版本漂移和重复消费均拒绝；direct writer 为 0。
- T-NLE-04：WorkOrder v2/v3 round-trip 兼容；TaskGraph/Verification/Review schema 稳定；非法版本和非法状态迁移 fail closed。
- T-NLE-05：每个 graph revision 只规划并冻结一次；失败重规划创建同一 graph 的 `revision+1`；顺序/并行/混合依赖正确；Child WorkOrder→SubAgentRequest lineage/授权完整；cycle 返回 `PLAN_CYCLE`。
- T-NLE-06：TaskStore 的 CAS、attempt、lease、heartbeat、重启恢复、业务幂等 reservation、budget reservation/usage 和 transactional outbox 可并发验证；Outbox 必须覆盖双进程唯一 claim、expired takeover、stale fence 拒绝、同 `aggregate_type + aggregate_id + graph_revision` 严格顺序、release backoff，以及 append 成功但 complete 前进程死亡后的幂等收敛。新进程在旧 claim 到期前启动时必须由后台续投线程在到期后自动接管；只要 canonical DB 仍有 PENDING，drain 就不得误报 `DRAINED`。完整 PENDING inventory 与 delivery-ready backlog 必须分离，逐条 claim/complete/release 不得重复执行 schema/全表 readiness 扫描。Publisher close 必须等待已进入的前台 publish；event 已提交后的 backlog 读取故障只能降级且不得泄漏底层错误详情或制造假失败。恢复结论必须按窗口标注：Outbox/coordination takeover、single-node post-budget Operation continuation 与 general/batch/pre-budget recovery不是同一能力。
- T-NLE-06A：0094 cancellation continuation 必须覆盖双进程唯一 coordination claim、heartbeat 续租、expected-version/owner/token/fence 拒绝 stale worker、defer deadline、expired takeover、migration receipt-loss replay 与 finalized cleanup；claim/heartbeat/defer/complete 必须在 `BEGIN IMMEDIATE` 取得写锁后才采样时钟，锁竞争不得吞掉 lease 或用旧时间授权 CAS。Worker 必须从 canonical HostCommandRepository 按 exact `graph_id + revision` 重建 command/work order/assignment/graph 与 `AdmittedExecution(replayed=true)`，只调用既有 GraphCancellationRuntime；0092 intent/decision 始终为唯一业务真相。Process container 必须向 Host/Internal/Scheduler Coordinator 与 worker 注入同一 `LocalGraphExecutionRegistry`，但不得将它当 durable authority；Runtime 未显式获得 `local_pending` 时必须 `cancel_and_join`，非空 pending 不得 advance/finalize。operation/effect/review 等事实未收敛时必须 durable defer，不得 complete 或伪报 Graph 已取消。worker start 失败必须尝试反向 stop/join 并 close Coordinator；只有全部回滚成功才 unbind，失败时保留 bound handle 供 `aclose` 重试，Runtime Group 也必须保留关闭失败的 child。shutdown 必须有界 grace+cancel+join，拒绝取消时必须 fail closed 且 Store 保持开放供重试。生产 Operator authority/入口即使已接线也必须默认 OFF；Operation/Effect 签名对账与 0098 历史 inventory/repair/quarantine 虽已接线，外部部署 inventory 仍未知。Budget contention 已由 T-NLE-06F 证明，但历史半状态、general recovery、部署凭据、完整 attestation key lifecycle 与真实 canary 未完成前不得改变 `PRODUCTION_OFF` 定级。跨进程业务证据由 `tests/integration/test_ne01_graph_cancellation_cross_process_continuation.py` 验收。
  同一 T-NLE-06A 还必须验证 0094 readiness/codec 只接受 exact frozen columns，额外 `status` 等业务字段 fail closed；`PRAGMA table_info` 的 name/type/notnull/default/pk 必须逐列精确一致，且 `foreign_key_list` 只能是单一 `id=0`、`seq=0/1/2` 三列复合外键，拆分/额外 FK 一律拒绝。`intent_ref` 必须是 `TEXT NOT NULL PRIMARY KEY`，所有必填 identity/counter/timestamp 列均保持 `NOT NULL`，readiness 必须拒绝 SQLite rowid PK NULL/复合 FK NULL 豁免幽灵行。
  Lifecycle 故障均必须返回 `TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED`；activation rollback 或 `aclose` quiescence 未完成后必须拒绝 bind/activate，只允许 `aclose` 重试。Coordinator admission close、drain、`aclose` 任一失败都必须保持 Store 开放；只有 Coordinator 和 worker 都已静默才能 drain Outbox/关闭 infrastructure。
- T-NLE-06B：0095 revisioned Graph Outbox migration 必须覆盖 0090/0091/0092 decision、0092 intent、Attempt、唯一 revision、HostCommand evidence 精确 CREATED，以及 deterministic `natsume_sha256_hex` 以 revision-bound command ID 重算 legacy governed `outbox_id + idempotency_key` 后精确恢复 ACTIVE 等非 attempt transition。升级后 ID/key 必须与升级前相同；冲突或不可解析自定义 producer 必须令 `BEGIN IMMEDIATE` 整笔失败。readiness/receipt-loss gate 必须冻结 `graph_revision = INTEGER/notnull0/defaultNULL/pk0`、column check、revision delivery index、Graph revision/payload insert+update trigger、`TASK_STORE_OUTBOX source_ref` unique partial index 与全表 binding。新 Graph payload ref 必须是 `taskstore://event/TASK_GRAPH/{graph_id}@{revision}/{version}`；旧 ref 只允许 durable codec/replay，任何新 insert/update 必须拒绝。所有生产 Graph producer 显式绑定 revision；decision getter 与 multi-revision marker scan 保持 exact。claim/head 同 revision 保序、不同 revision 独立。新 Runtime Event 为 `graph_id@revision`，旧 journal 不改写并经 source Outbox revision 查询；append 成功但 complete 未确认的 legacy 重投必须只在现存 event 与 current/legacy 重建结果全字段一致时返回原 journal，unique source fence 下不得重复投影，漂移必须拒绝。Operator/ReviewStore 必须按 canonical URL-escaped task-node subject ref 精确 revision 过滤。0098 只能把 `GRAPH_CANCELLATION_EFFECT_RECONCILIATION` 加入 revisioned Outbox allowlist；该 aggregate 的 positive `graph_revision` 与 payload ref 必须由 insert/update trigger 物理绑定到 immutable Effect case，签名 binding 与 trusted read 必须复验同一 revision，除 `TASK_GRAPH` 和该 aggregate 外的 Outbox 仍必须保持 revision null。证据至少覆盖 migration compat、RuntimeOutbox、Outbox durable claim、Runtime Event spine/contract、Review/Operator contract 与 cross-process delivery tests。
  同一 T-NLE-06B 还必须验证 `created_ts_ms=0` 不被替换为当前时钟；由于 current revisioned 与 legacy unrevisioned 两条历史 append 路径都可能以时钟替代零值，timestamp 容差适用于 source zero 的两种可重建 candidate，但 event ID/schema/type/source/aggregate/payload/evidence/dedupe/redaction 等其它字段必须 exact，source timestamp 非零时 timestamp 也必须 exact。`read_scheduler_snapshot` 默认不返回/扫描 `graph_outboxes`；PlanningRecovery 必须显式 `include_graph_outboxes=True`，在同一只读事务取得 exact revision inventory，并使用唯一 terminal status/version Outbox 的实际 payload ref。pre-0095 legacy ref 必须原样进入 failure evidence；只有旧版或替代 Store 未提供可选扩展字段时才合成 revisioned fallback，生产 DurableTaskStore 提供 inventory 后，类型错误、缺行或歧义必须 durable drift。证据由 `tests/unit/test_gr04_planning_recovery.py` 与 `tests/integration/test_gr04_planning_replan_sqlite.py` 覆盖。
- T-NLE-06C：中央 Task mutation Outbox insert/replay helper 必须只定义在 `TaskOutboxDeliveryStoreMixin`，`DurableTaskStore._insert_outbox` 与 `_assert_mutation_outbox_replay` 必须保持该 mixin 的 method identity；`task_repository.py` 不得保留重复定义。所有 mutation/replay 调用必须复用 owner transaction 的 `conn`，既有 MRO、CAS、response-loss、legacy/current 与 0095 exact revision 行为测试必须继续通过；`core_architecture_policy` 必须验证 `task_repository.py` 不超过 4,600 行。该结构证据不得被解释为新增 Store、事务或发布 authority。
- T-NLE-06D：`task_graph.cancel` 生产权限/入口必须覆盖以下矩阵：Operator command contract/path 只接受 exact `graph_id + positive graph_revision + canonical reason_code`，并要求 `obs:write + task-runtime:cancel`；Gateway/BFF 生成 server-owned structured authority，Application 拒绝调用方自报 `request_id/request_identity/source`；共享 `SecureIngressGraphCancellationAuthorityVerifier` 必须重读 committed audit/claim，逐项绑定 request/principal/tenant、认证 body digest、action/scopes、path/method 和 allow/complete 状态，任一漂移 fail closed；`claim_result_ref` 非空的 settled authority 必须拒绝复用为 bearer，公开 duplicate 只重放原 outcome。Host/Internal/Scheduler 必须接收同一个 verifier 实例，Runtime Group 按 immutable HostCommand surface 精确路由。下游异常后，无 exact intent 必须明确 `side_effects_may_have_run=false`，匹配本次 request/audit/source/reason/graph/revision 的 intent 才能为 true，读取失败或畸形事实必须保守 uncertain；只有 cancellation handler 显式翻译的 OBS 封闭端口异常可携带确定性，任意依赖异常即使 duck-type 同名字段也必须为 uncertain，`CancelledError` 原样传播。feature flag 默认 false；显式开启而 Secure Ingress 非 `ENFORCE` 或 Gateway 不可用时，ASGI 必须在路由开放前启动失败。UI 必须提交 exact revision，收到命令结果后刷新 durable detail，不得乐观显示 `CANCELLED`。证据至少包括 `tests/integration/test_ne01_graph_cancellation_secure_operator_path.py`、`tests/security/test_ne01_graph_cancellation_operator_authority.py`、`tests/unit/test_ne01_graph_cancellation_operator_routing.py`、`tests/unit/test_obs_domain_command_ports.py`、`tests/unit/test_aw01_asgi_security_composition.py`、`tests/contract/test_aw07_operator_command_gateway.py` 与 Monitor UI operator gateway/client tests。该 Gate 通过只允许定级 `WIRED_DEFAULT_OFF / PRODUCTION_OFF`。
- T-NLE-06E：Cancellation Acceptance Effect 不确定性必须覆盖四类既有合同：取消请求观察到一般 `FAILED` 时 fence 为 `RECONCILIATION_REQUIRED`；`CLAIMED` effect 在取消提交后发生 handler/transport `FAILED` 时仍不得解除 fence；带 durable receipt 的 `COMMITTED` 可成为 `COMMITTED_EVIDENCE` 并允许继续收口；历史 `FAILED_EVIDENCE` 行保持可读但新路径不得生成。测试必须证明 `advance_graph_cancellation()` 不会把 `FAILED` 自动升级为证据，`pending_effect_ids` 保持该 effect，且 status/reason code 不能替代签名 `NO_SIDE_EFFECT` attestation。PENDING classifier 还必须覆盖 exact virgin tuple（attempt_count/fencing_token/status_version 全 0，reason_code/last_error 均空）才可 `CANCELLED`，row-canonical retry tuple的最小及更高单调计数均为 `RECONCILIATION_REQUIRED`，五字段任一混合或弱化组合按 durable corruption fail closed。这里的 retry 分类是纯函数/legacy hardening合同，不代表正常可达竞态：public claim API必须证明未 ACCEPTED NODE effect无法 claim；accepted NODE上的真实 retry PENDING仍保留 acceptance authority、不会产生 cancellation fence，GRAPH effect只有 COMPLETED后才可 claim而该 Graph已终态。Store contract必须用注明为 direct-SQL 的 legacy/imported/cross-table corruption fixture构造 fenceable retry PENDING，并证明 request提交后响应丢失 exact replay、连续两次 advance仍 pending、canonical `taskcancel-effect://sha256/...` evidence ref精确且稳定、direct finalize返回 `TASK_STORE_OWNERSHIP_CONFLICT`。0098 必须把历史 pre-fix `FAILED_EVIDENCE` 与 retry-PENDING + CANCELLED fence收录为 append-only immutable case，按 `REPAIRABLE_REQUESTED`、`QUARANTINED_FINALIZED`、`QUARANTINED_STRUCTURAL` 分类；不得改写 0087 Effect、0092 fence/decision。`PENDING_CANCELLATION` proof 只能进入新 decision evidence；`LEGACY_FINALIZED_CORRECTION` 必须绑定旧 decision ref + payload digest，作为 trusted overlay 且绝不写入旧 evidence；structural quarantine 永远 fail closed。Effect 签名 binding 必须精确覆盖 case、intent、effect、fence、attempt、resolution、authority、Outbox、subject digest 与 positive graph_revision，验证伪签名、未知 key、版本/graph/fence/decision 漂移、response-loss replay、stale CAS；`CLAIMED` 还必须要求 expired lease 与 executor quiescence，`SIDE_EFFECT_CONFIRMED` 必须有 downstream receipt。Operation 与 Effect 对账都只定级 `WIRED_DEFAULT_OFF / CANARY_NOT_RUN`；Budget contention 已由 T-NLE-06F 证明，但外部部署 inventory、历史半状态、general recovery、部署凭据、完整 key lifecycle 与真实 canary 未完成前 durable cancellation 保持 `PRODUCTION_OFF`。证据至少包括 `tests/unit/test_ne01_effect_cancellation_classification.py`、`tests/contract/test_ne01_graph_cancellation_store.py`、`tests/unit/test_ne01_graph_cancellation_operation_reconciliation.py`、`tests/unit/test_ne01_operation_reconciliation_verifier_wiring.py`、`tests/unit/test_ne01_graph_cancellation_effect_reconciliation.py`、`tests/unit/test_ne01_effect_reconciliation_migration.py`、`tests/unit/test_ne01_effect_reconciliation_operator_reads.py`、`tests/integration/test_ne01_graph_cancellation_effect_reconciliation.py` 与 ASGI/OBS composition tests。
  同一 Gate 还必须验证 dedicated Obs 在 views 关闭时不触碰 0098，开启时只使用同 canonical SQLite 的 `mode=ro + query_only=ON` reader，不构造 `DurableTaskStore` 或 UoW。三条 Effect GET 路由必须精确要求 `obs:task-runtime:read`，并拒绝仅有 `obs:operator:read` 的 principal。privacy serializer 后必须保持零非预期 redaction：公开 summary 最小化，exact subject 以 canonical-JSON base64url + sha256 专用 signing material 交付；真实 Ed25519→service→SQLite roundtrip 必须成功且 raw error/signature 不泄漏。证据为 `tests/integration/test_ne01_obs_effect_reconciliation_read.py`、`tests/unit/test_aw07_operator_query_gateway.py` 与 `tests/unit/test_obs_fastapi_app.py`。
- T-NLE-06F：Budget 高竞争必须覆盖 8/16/32 线程及独立进程。只有一个 rejected usage 可以成为 `EXCEEDED` winner、推进 Budget version并拥有唯一 Outbox；其余 sibling必须记录 `ALREADY_EXCEEDED`并引用 winner。0090 Budget decision 写入、读取与 replay 必须在单一 SQLite 读快照中复核 terminal Reservation 精确版本、唯一 winner/Outbox、Attempt/Graph lineage和全部 sibling；篡改 Reservation version、插入第二条同版本 Outbox或改写 sibling authority必须 fail closed。`DurableBudgetRuntime._charge` 的 attempt 数必须为 `max(configured retries, policy concurrency + 1)`；`tests/unit/test_ne01_budget_runtime_contention_retry.py` 必须证明 concurrency=8、连续前8次 `TASK_STORE_CAS_CONFLICT` 后第9次成功。exact replay、restart、interleave、winner/sibling drift和非法 candidate必须 fail closed。该 Gate 仍不证明 concurrency charge、DEGRADE/REVIEW或 program-wide coverage。
- T-NLE-06G：0099 terminal reconciliation 只验收默认关闭的终态历史对账能力，不验收 general recovery 或生产启用。Migration 必须是 schema-only：只安装 immutable append-only case/proof/supersession、索引、约束与 trigger，不得扫描、inventory、backfill、repair 或改写 Graph/Budget/0090/0092 authority。Readiness 必须从受信 0099 migration 资产在隔离 SQLite 中生成基准，对 live `sqlite_master` 严格比较 3 张 table、4 个 index 与 10 个 trigger 的全部 canonical SQL；同名 no-op trigger、错列 index、table CHECK/constraint 漂移或多/少同前缀对象都必须 fail closed。应用层还必须以 Python validator 重验 case/subject/proof 的 exact schema、canonical JSON/digest、列绑定与 authority matrix，不能只信 SQL trigger。显式 inventory 必须排除 ACTIVE/CREATED，只接受五类 anomaly case：`FAILED_UNSETTLED_BUDGET`、`BLOCKED_FAILURE_NODE`、`CANCELLED_UNSETTLED_BUDGET`、`COMPLETED_INCONSISTENT`、`TERMINAL_STRUCTURAL_ANOMALY`；其中 `COMPLETED_INCONSISTENT` 与 `TERMINAL_STRUCTURAL_ANOMALY` 必须归为 `QUARANTINED_STRUCTURAL + NONE` 并永久禁止 repair。`EXACT_REPAIRABLE` 允许 `SYSTEM_DURABLE_EVIDENCE` 或 `SIGNED_OPERATOR`，`SIGNED_REPAIR_REQUIRED` 只允许 `SIGNED_OPERATOR`；`RELEASE_BUDGET`、`FAIL_GRAPH`、`FAIL_GRAPH_AND_RELEASE_BUDGET`、`NONE` 的 target status 与 Graph/Budget Outbox 必须逐项精确。伪签名、未知 key、stale case/subject version、action/target/outbox 漂移必须零副作用 fail closed。一次 proof 必须在同一 Store UoW 只改写 exact terminal Graph/唯一 graph-scope Budget 与 deterministic Outbox；不得创建 Attempt/Lease、重进 worker 或静默 resume，response-loss replay返回同一 proof，并发只能有一个 immutable winner，注入式中途失败必须整笔回滚。独立 Obs 与主 Host ASGI 的 Operator candidates/cases/challenge 都必须显式构造同 canonical SQLite 的 `mode=ro + query_only=ON` reader，不得回退到 DurableTaskStore 或 writable UoW；challenge 必须直接返回最终 Store-verifiable Ed25519 binding bytes，绑定 authenticated query principal、authority key、attestation ref/time、exact case/subject、action/targets 与 deterministic Graph/Budget Outbox，并返回同一 command request，HTTP→Ed25519→service→SQLite proof 必须 round-trip。`HOST_TASK_TERMINAL_RECONCILIATION_OPERATOR_ENABLED` 与 `HOST_TASK_TERMINAL_RECONCILIATION_AUTO_ENABLED` 必须独立默认 false；自动路径只处理 `EXACT_REPAIRABLE`。取消生产 gate 必须同时检查 0098 Effect 与 0099 terminal readiness；remediation Operator 单独启用不得触发或阻断 cancellation production gate，它只受自身 Secure Ingress/Gateway/key 启动条件约束。证据为 `tests/unit/test_ne01_terminal_reconciliation_migration.py`、`tests/unit/test_ne01_terminal_schema_codec_hardening.py`、`tests/unit/test_ne01_terminal_reconciliation_classification.py`、`tests/unit/test_ne01_terminal_reconciliation_service.py`、`tests/unit/test_ne01_terminal_reconciliation_operator_reads.py`、`tests/unit/test_terminal_reconciliation_query_repository.py`、`tests/integration/test_ne01_obs_terminal_reconciliation_read.py`、`tests/integration/test_ne01_terminal_reconciliation_store.py`、`tests/integration/test_ne01_terminal_reconciliation_decision_authority.py`、`tests/unit/test_ne01_operation_reconciliation_verifier_wiring.py`、`tests/unit/test_aw01_asgi_security_composition.py`、`tests/security/test_obs_secure_command_ingress.py`、`tests/unit/test_obs_domain_command_ports.py` 与 `tests/unit/test_obs_fastapi_app.py`。该 Gate 只能定级 `WIRED_DEFAULT_OFF_CANARY_NOT_RUN`；外部 deployment inventory、部署 active/retired/revoked key lifecycle 与真实 canary 为 `UNKNOWN/NOT_RUN`，general/multi-node/pre-budget recovery 仍为 `INCOMPLETE`，durable cancellation 仍为 `OFF`。
  分批 inventory 还必须证明已持久化的前一批不会挡住后一批，重复调用会在 10,000 个 terminal Graph 的显式扫描上限内确定性推进且不重复插入；`FAILED/CANCELLED + PENDING/CONSUMED Budget` 属于不可安全推断的结构异常，必须冻结为 `QUARANTINED_STRUCTURAL + NONE`，不得当作已收敛状态跳过。
  历史 Graph 重入还必须在 Runtime 快照层拒绝为 `BLOCKED/COMPLETED/FAILED/CANCELLED` Graph 创建或激活 Budget，并由 Store 在 `BEGIN IMMEDIATE` 写事务内再次校验 Graph status 仅为 `CREATED/ACTIVE`；测试必须证明拒绝路径不新增/改写 Budget 或 Outbox，同时不阻断终态 Graph 既有 ACTIVE/EXCEEDED Budget 的消费、释放以及无 Graph 绑定的 Budget。该防线只阻止重入制造新的半状态，不代表 pre-budget recovery 已完成。
  Budget authority 必须精确限定为恰好一条 `node_id IS NULL AND attempt_id IS NULL` 的 graph-scope Reservation；缺失、多条或 child-scoped Budget 都必须进入 `QUARANTINED_STRUCTURAL + NONE`，SQL trigger、classifier 与 proof mutation 需分别防御。终态 classifier 与生产 gate 必须使用完整 Graph/Node codec，校验 Review 的 canonical JSON/SQL、subject/revision/phase/attempt binding，终态 Attempt 与已释放 Lease 的 owner/fence/timestamp lineage，以及 0090/0091/0092 canonical parallel terminal decision、Budget after-state 和 live/conflicting authority。0090 必须恰好一条并通过完整 decision/outbox binding，不得从多条中取 latest；0091 必须复用 complete success codec/binding/latest-Attempt validator；0092 必须复用 trusted final-decision 与 Effect overlay validator。Exact Attempt failure bundle 必须同时精确校验 Attempt/Node/Graph/Lease 四条 Outbox 的 aggregate、event、version/revision、evidence/attempt/timestamp、idempotency/outbox ID 与 canonical `payload_ref`；任一漂移都必须零写入 fail closed。Outbox 的投递状态变化不得错误地使 authority snapshot 失效。
  同 Graph/revision 上已 inventory 且仍代表 current state 的未解决 case 必须为 graph-wide 写围栏；不只拒绝 `transition_budget(RELEASED)`，还必须拒绝普通 TaskGraph transition、Graph cancellation request、Budget transition/consume、BudgetUsage journal 与 retry Budget mutation。拒绝路径必须保持 Graph/Budget/Usage/Outbox 零写入；0099 proof mutation 在同一 Store UoW 中是唯一显式 `terminal_reconciliation_bypass`，其他 writer 不得暴露 bypass。后续状态变化必须冻结新的 replacement case，并用 immutable supersession 连接旧 case；旧 case 不得再直接 proof。readiness 必须验证每个 proof 和 supersession 的绑定，并要求所有历史 case 经零条或多条 supersession 最终终止于 trusted proof；断链、环、漂移、未证明的末端、current repairable/signed-required 或 quarantine 都必须阻断生产。
  默认关闭的自动路径必须在查询层只取 `EXACT_REPAIRABLE`，循环消费有界 batch 直至不存在 exact candidate；达到显式总量上限时必须返回 `TRUNCATED` 并报告 `remaining_exact_cases=true`，不得因前缀中的 signed/quarantine case 饿死后续 exact case，也不得伪报完成。`SYSTEM_DURABLE_EVIDENCE` 请求的 `issued_ts_ms` 必须取 immutable case `created_ts_ms`，使 request/proof ID 对重启和多进程时钟稳定；两个进程对同 exact case 必须收敛到同一 proof 或 exact replay，不能让 loser 因启动时钟不同而变成不确定错误。批处理若在前缀 case 已提交后读取后续 candidate、应用后续 case 或验证 completion 失败，异常必须带已提交数并且 `side_effects_may_have_run=true`；尚无任何 result 时才可为 false。一次 startup inventory 必须覆盖受支持的 10,000 个 terminal Graph 上限；该边界不是外部 deployment 已完成盘点的证明。
  上述五类是 anomaly case；第六类 `TERMINAL_HEALTHY_CLOSURE` 仅可在同 Graph/revision 存在 unresolved unsuperseded 历史 case 且完整 current classifier 当前返回 healthy 时冻结为 `SIGNED_REPAIR_REQUIRED + NONE`。它必须以 immutable supersession 接续旧 case，且只能由 `SIGNED_OPERATOR` proof 闭链；`SYSTEM_DURABLE_EVIDENCE` 对 healthy closure 必须被 SQL 与 Python 双层拒绝。Proof 必须固定 `target_graph_status=null + target_budget_status=null + outboxes=empty`，不得修改 Graph/Budget 或写 Outbox；healthy snapshot 再漂移必须继续追加 replacement + supersession，不得对 stale closure 直接 proof。`QUARANTINED_STRUCTURAL` 本身永远不可直接 proof。
- T-NLE-06H：Operation 自动 continuation 只验收 single-node post-budget checkpoint。Store 必须一次证明 exact COMMITTED Reservation、唯一 `OPERATION_COMMITTED` Outbox、原 Attempt、历史 Lease/owner/fence、hash receipt；runtime 再证明 canonical WorkerProjection完整 lineage和恰好一条 accepted worker BudgetUsage。旧 Lease 到期后新 owner/fence接管同一 Attempt，worker调用数保持 0，只继续 Verification/Acceptance。COMMIT 后缺 BudgetUsage、pre-budget、batch/multi-node、public/noncanonical projection或证据漂移必须在取得新 Lease/完成 Graph前 fail closed。另行覆盖 Node ACCEPTED/Graph ACTIVE/Effect空的进程退出：共享 finalizer重启后原子补写同一 Effect，重复 replay不新增 Effect，篡改 projection时 Graph不完成。证据为 `tests/contract/test_ne01_operation_checkpoint_store.py`、`tests/integration/test_ne01_operation_online_takeover.py`、`tests/unit/test_ne01_operation_recovery_runtime.py` 与 `tests/integration/test_ne01_accepted_effect_finalization_recovery.py`。该 Gate 只能定级 `SINGLE_NODE_POST_BUDGET_CHECKPOINT_PROVEN / GENERAL_RECOVERY_INCOMPLETE`。
- T-NLE-07：Task scheduler 的 READY、依赖、retry/backoff、cancel 和 recovery 决策可回放；重放/预览不产生真实副作用。
- T-NLE-08：required verification 的 `FAIL/ERROR/NOT_RUN` 均不得进入 `VERIFIED/ACCEPTED`；命令验证受 allowlist/timeout/workspace policy 约束。
- T-NLE-09：每条 acceptance criterion 都有 evidence/verdict；确定性 FAIL 不可被 LLM Critic 覆盖为 ACCEPT。
- T-NLE-10：PRE/POST Review 使用不同恢复阶段；重启、过期、串用和重复 resume 均 fail closed；POST resume 不重放副作用。
- T-NLE-11：canonical 切流只影响新任务；legacy drain 可对账；跨执行器 resume 和重复副作用为 0；canary 可回退。
- T-NLE-12：关键 Runtime Event 与状态同事务写入；cursor reconnect 无缺失，重复事件幂等；raw thought/secret/cookie/private body 为 0。
- T-NLE-13：Context refs 按角色授权；token 预算、实际消耗、截断原因和污染拒绝可复现。
- T-NLE-14：Life schema migration 幂等；expected-version OCC 在 20 个并发写入下无 silent overwrite。
- T-NLE-15：WorldProjection 无反写接口；source/confidence/observed/expires/conflict/epistemic type 完整且过期生效。
- T-NLE-16：事件可回放到 Appraisal、affect/drive 与表达策略；Bond 保持单写者，rift/repair/boundary 语义不丢失。
- T-NLE-17：SceneDecision 实际影响 Surface/proactive；ActionArbiter 只能收紧 Governor，不能覆盖用户显式任务。
- T-NLE-18：Capability risk adapter 输出稳定；自动只能降级；人工升级需要评测证据和 Review，不能绕过静态风险上限。
- T-NLE-19：DegradationState 可进入、恢复并携带证据与影响能力；核心故障注入不会开放受影响高风险能力。
- T-NLE-20：IdentitySnapshot 可比较、可审查、无私密正文；重大差异阻断发布并可回滚至前一 release ref。
- T-NLE-21：Execution/Trace/Review UI 能回放复杂任务并正确处理 approve/reject/expired，不提供绕过 token 的恢复入口；lint/typecheck/read-model unit 与既有 E2E 均通过。
- T-NLE-22：Life UI 正确区分事实、推断、过期和冲突，并展示 proactive dry-run 的来源与裁决。
- T-NLE-23：Capability/Health/Identity/Security Lab UI 不提供自动升级或未审批身份变更，不泄漏私密正文；非 Lab 模式不暴露可执行 Pentest 高风险入口。
- T-NLE-24：稳定测试、迁移、重启、SSE、故障注入、canary/drain 和 rollback drill 全部产生可审计 Seal 证据。

### NE-03 World / Scene / Intent / Guardian

- T-NE03-CONTEXT-01：0111 Observation writer 绑定 Host lease 与 canonical source verifier；SQLite 原子分配 workspace/subject revision，并覆盖 exact replay/drift、双 writer、captured head 截断、物理行漂移、future/non-monotonic time、伪造/重复 expiry、expired digest reactivation、重启与超期未 transition fail-closed。
- T-NE03-SCENE-01：Scene 只消费 app-local typed Guardian snapshot；同一 World/context/Guardian 返回相同 revision/digest，重复读取不追加，变更严格推进；覆盖八场景、1001+ replay、并发 CAS、旧 lease takeover fencing、fingerprint/digest/authority lineage 与 0112 schema drift。
- T-NE03-INTENT-01：IntentAuthorityPort 对 principal、goal、TaskGraph/node、ContextPack、Scene、action、Grant、policy 与全量约束返回短时效 typed attestation；过期/撤销/跨 principal Grant、任一 binding drift、约束遗漏/冲突和 detached arbitration 均 fail-closed，结果固定 `execution_authorized=false`。
- T-NE03-GUARDIAN-01：RuntimeGuardian 无 module-global singleton；0112 在 append 同一 SQLite 事务校验 Host scope/owner/lease/fence，并覆盖 1001+ replay、commit-then-raise、CAS loser fencing、lease takeover、deterministic health/recovery reducer replay、伪造 next-state、多 PREPARED Review recovery、maintenance/sleep、SLO 与 acknowledgement。
- T-NE03-GUARDIAN-02：Recovery Review 使用 durable PREPARED→consume→COMMITTED；consume 后 append 失败或进程重启必须按 deterministic result ref 对账，限制在 COMMITTED 前不得解除。
- T-NE03-PREVIEW-01：Action Preview 只能由 app-local authority 重开 immutable HostCommand/WorkOrder/TaskGraph、Attempt/Lease、node-bound Grant、ContextPack/Scene 与 Acceptance Effect claim；caller 自报、future issue、TTL、response-loss replay、旧 WorkOrder/effect、origin 超过 1000 条和 derived scope/args 扩大均 fail-closed。Derived terminal 必须再次递归重开 origin Attempt/Lease；Acceptance surface 只允许合同绑定的最小文本，任意 `raw_extra`、媒体、二次回复或内容漂移在网络前拒绝。Scheduler Proactive 必须把 typed proof、固定 request ID 和 exact message 贯穿到终端；EdgeCommandPusher 的构造/from_env/init/set 均不得关闭门禁。Raw Edge HTTP transport 与低层 Embodiment command port 必须为模块私有且不出现在 `__all__`。Scheduler-derived Life Edge/Gateway/VTS/MateEngine 与公开具身 submit/enqueue必须覆盖 exact terminal 正向、无 proof 零副作用和篡改拒绝；已 claim 的 durable outbox 只能经私有 submitter 执行。旧 reply-performance、Gateway voice、Avatar 直通以及 37 个尚未进入 Governed TaskGraph 的 Operator 写命令必须在副作用前失败关闭，且不得把安全阻断记为功能迁移。
- T-NE03-CATALOG-01：0113 Service Catalog 必须从 durable head 严格经历 AUDIT→SHADOW→PREFLIGHT→CANARY→ENFORCE；每阶段启动真实 socket/Uvicorn 并核验 Host/Guardian identity，中间候选关闭、最终候选保留。Pending listener 必须精确绑定 operation ID、snapshot、stage 与 candidate，其他健康 listener 不能推进 head；`REGISTERED` 崩溃必须以同 operation ID 恢复。候选停止失败或 listener 仍占用时保持待对账，不得写伪 `ROLLED_BACK`。覆盖 target 停点/恢复、双 coordinator、非法 operation receipt 状态机、terminal-first、append response-loss、错误/正确双 listener reconciliation、stage/rank 与 runtime-scope/activation identity SQL tamper、rollback_ref replay drift、stage skip/regression、exact replay 与 ENFORCE 重启。

当前执行记录（由 `artifacts/local-gates/<final-head-sha>/` 绑定 #84 最终 HEAD）：NE-03 wave `239 passed`；既有受影响回归 `101 passed`；raw transport 私有化后回归 `36 passed`；Service Catalog 关闭加固回归 `6 passed`；独立审计复跑 Guardian/Catalog `42 passed`、ActionPreview/Edge `47 passed`。production CLI Host 在隔离 runtime 环境和 fresh SQLite 上逐级完成 AUDIT→SHADOW→PREFLIGHT→CANARY→ENFORCE，生成 5 个 operation / 15 个 receipt，`/health` 为 HTTP 200、`status=OK`、`service_identity=host.main/http`，Guardian durability=`READY`、fencing token=5。Windows Ctrl+Break 清除进程/端口且没有伪造 lease release；旧 lease 到期后，同一 SQLite/端口/快照由 coordinator fence=2 接管，恢复 ENFORCE/health OK，Guardian fence=6，ledger 累计 6 operation / 18 receipt。该项是 crash-fenced recovery，不是 graceful-stop PASS；另以故障注入证明显式 `HostCatalogRuntime.close()` 即使 candidate stop 报错也继续释放 authority/reservation，Windows 优雅停止脚本留到 NE-07。commit-bound Gate 已成功退出；部署多实例 canary 未执行，NE-03 以 `LOCAL_ACCEPTANCE_PASSED_WITH_KNOWN_BOUNDARIES` 合入，当前活动波次为 `NE-01_IN_PROGRESS`，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

NE-01 第三个切片及其 Tool/MCP authority 收口的强制测试矩阵：Obs locator retirement 必须证明五个零参数 refresher/locator import 已删除、显式 store/root 注入仍可读；该切片 commit-bound 的 ASGI/http_hooks Tool seed/route locator 库存为不高于47 modules/147 names，process-owned `get_mcp_tool_broker` accessor 与 `mcp_tool_broker` slot 必须不存在，Container当时为59 fields/6 locks/52 refs。上述是历史切片基线，不是合并Host lifecycle后的当前值。ReAct 必须证明工具发现不再返回 `run_skill`/`dispatch_sub_agent`，即使注入旧 ToolSpec 也在 Skill executor/Delegate dispatcher 前以精确 governed-admission reason 失败且 `side_effects_executed=false`；Operator 必须用真实 SQLite 与 HMAC Secure Ingress 证明 `scheduler.task.trigger` 在 Review 前执行 0 次、一次性 resume 后执行 1 次、重复/重启不再执行，同时确认其他 36/37 命令继续在 handler 前拒绝。Tool HTTP 必须用真实 migration/SQLite/HMAC 证明 `HostCommand → immutable TaskGraph → app-local governed execution port`、exact TOOL claim、durable Review、一次性 resume、node grant、Budget/Attempt/Lease、exact binding、Verification/Acceptance/Receipt、重启 duplicate 与拒绝 replay；production seed 必须报告 `governed_executable_tools=0` 和 `FAIL_CLOSED_NO_GOVERNED_EXECUTOR`，不能把测试 adapter 写成生产能力。另需证明 `RECOVERY_REQUIRED/RETRY_PENDING` 不封 claim、unknown operation 不二次调 handler；`PENDING` marker 只能由 `before_handler` 在 canonical live-state、grant 和 binding 三段检查全部通过后、实际 handler 前写入。原始 `ToolBroker.execute()`/`execute_bound()`、`tools.MCPAdapter`、兼容 `MCPToolBroker` 与 MCP Skill bridge 必须 handler=0/Token=0/provider-call=0，并保留只读 MCP registry/scorecard；默认关闭后 stale `mcp::*` 为 0。普通 ReAct `GovernedToolNode → CapabilityBroker` 必须另行审计，不能由本矩阵外推为全部 Tool authority 已清零。Tool seed readiness 早于 runtime activation 是尚未修复的 P2；executor=0 当前使 `READY` 不可能误报，但首个真实 executor 启用前必须统一 readiness 状态机。该矩阵不改变 atomic/fan-out/cancellation/reconciliation 的默认关闭状态，也不把旧 authority 清零写成真实 Tool/MCP executor 已接线。

本次本地结果：旧 Tool/MCP authority 合同 78 项、ASGI/Capability/lifespan/locator 回归 75 项、governed Tool route/seed/真实 SQLite authority 回归 40 项、Frozen Tool Worker 边界回归 12 项、文档/metadata/coverage Gate 42 项均通过；`compileall`、JSON、`git diff --check` 与 fresh mixed-file Gate 也通过。证据由 `artifacts/local-gates/<commit-sha>/` 绑定，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

同一 rebase 后的 Host lifecycle 测试矩阵还必须证明：每个 `create_app()` 独立拥有 monitor/resource/mode container；只有 factory/start 全成功才发布；Activity factory failure 不发布 Mode；closing fence 阻断新 `begin()`；ResourceManager startup sample 只使用唯一 owned loop；Chat/Input/Tool/Scheduler/Guardian/snapshot读取同一 app-local instance；Obs只读不懒启；Tribe exact owner bind/unbind同一时刻只允许一个live Host并令第二个fail closed，且不得冒充并发multi-app隔离。原Host lifecycle commit-bound inventory为named 45/124、all-form 51、Container56/4/51；与Tool/MCP删除合并后fresh current必须精确为45 modules/123 names、51 consumer modules、55 fields/4 locks/50 refs。定向回归批次为`143 passed`，补充结构/单时钟批次为`22 passed`，两批仅重叠`test_aw08_tribe_dependency_direction.py`的4项，去重161项；原始与去重口径同时保留。波次integration为`NOT_RUN_PER_WAVE_POLICY`；full architecture静态Gate仍有独立失败项，不得由focused/mixed-file Gate推导为PASS。

本轮 TaskStore 写 authority 封口证据必须继续按原始批次报告，禁止相加成一个伪造总数：authority/structure `19 passed`；HostCommand admission `1 passed`；NLE/Operation/Heartbeat `77 passed`；Acceptance Effect Store `15 passed`；Parallel/Settlement/Atomic `45 passed`；Cancellation 首跑 `29 passed + 1 fixture failed`，修正后该 case `1 passed`；Integration 首跑 `46 passed + 3 fixture failed`，修正后 3 个 cases 通过；Terminal classification `32 passed`。前两个 fixture 失败来自旧测试在 Acceptance 后 standalone 补 Effect，与新 canonical fence 冲突；第三个 fixture 缺 `node_risk_level`，该失败已在干净 `origin/dev` 复现，修正只补当前必填测试输入。全部 fixture 修复/重命名后，authority、两份结构合同、两类 recovery、代表性 cancellation、durable artifact baseline、dispatcher semantic order 与 HostCommand admission 的最终关键回归为 `26 passed in 35.33s`；该结果不能用来隐藏首轮失败。波次 integration 仍为 `NOT_RUN_PER_WAVE_POLICY`；该切片完成时 full architecture 静态 Gate 为 `FAIL [1,6,10]`，不得由 focused/mixed-file/authority Gate 推导为 PASS。

后续 Gate 10 切片的证据独立记录为：Runtime Context targeted `7 passed`；Service Catalog unit/contract `15 passed`；Host socket integration `6 passed`；Repository/Global 独立复跑 `16 passed`，其中最后一组与 15 项有重叠，禁止相加。Global SQLite boundary=`PASS`；该切片完成时 full architecture static 为 `FAIL [1,6]`，而 `origin/dev` 基线是 `FAIL [1,6,10]`；因此只验收业务层私有 SQLite Gate 10，不把尚未完成的 Gate 6、Gate 1、typed composition 或 NE-01 写成完成。无新 migration；wave integration=`NOT_RUN_PER_WAVE_POLICY`，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

随后 Gate 6 切片的证据独立记录为：完整 Obs app 首跑 `13 passed + 1 test fixture failed`，失败来自旧 monkeypatch 仍指向已删除的 `start`；修正后该 case `1 passed`。最终生命周期关键批次 `12 passed`；daemon cadence/shutdown/serialization/start-single-flight `4 passed`；最终 Gate 6 定向集合 `19 passed`。这些集合有重叠，禁止相加。static contract 证明 Gate 6 issues=[]、生产 `start_projection_jobs()` 调用点恰好一个且属于嵌套 lifespan；full architecture static 当前 `FAIL [1,11]`，Gate 6/Gate 10 均 `PASS`，`origin/dev` 基线仍为 `FAIL [1,6,10]`。这里只验收明确的 app lifecycle owner；Gate 1 SCC 与 Gate 11 ToolBroker production-service inventory 双命中、continuous-SLO health、typed composition 和 NE-01 仍未完成。无新 migration；wave integration=`NOT_RUN_PER_WAVE_POLICY`，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

Service Catalog Gate 1 分层切片的证据独立记录为：contract/unit `61 passed`；真实 Catalog/Host socket integration `7 passed`；边界复跑 `7 passed`，集合可能重叠不得相加；`py_compile` 与 `git diff --check` 通过。静态库存为 application→composition 2→0、Catalog persistence→application 1→0，SCC 从 application/composition/persistence 缩为 application/persistence；Gate 1 仍 `FAIL`，因为 persistence→application 仍有 11 条（PlanningSearch 7、Evolution/Lab/Skill/World 4），Edge violation=0、reverse import=0。无 migration；wave integration=`NOT_RUN_PER_WAVE_POLICY`，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`；typed Composition 与 NE-01 继续 `IN_PROGRESS`。

PlanningSearch 持久化合同分层切片的证据独立记录为：边界合同 `5 passed`；D13/D18/D19/D20 回归 `60 passed, 1 skipped`，跳过项仅因 Windows 环境没有 symlink 权限，不计为通过；`py_compile` 与 `git diff --check` 通过。八组 core/benchmark/calibration/code/research 合同与模型已进入纯 `contracts/planning_search/`，旧路径保持同对象重导出兼容，code benchmark、calibration、research shadow 三个仓储的 7 条 persistence→application 已清零。full static 仍 `FAIL [1,11]`：Gate 1 的 application/persistence SCC 只剩 Evolution/Lab/Skill/World 4 条，Gate 11 仍为 ToolBroker production-service inventory 双命中；application→composition=0、Edge violation=0、reverse import=0，Gate 6/10 继续 `PASS`。本切片无 migration；wave integration=`NOT_RUN_PER_WAVE_POLICY`，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`；PlanningSearch 功能/收益、typed Composition 与 NE-01 均未完成。

## T-ARCH（完整架构发布 Gate）

说明：编号定义不代表代码已实现；每项必须同时具有自动化测试、可审计执行证据和回滚证据，才可标记为通过。执行顺序只由 `docs/30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md` 决定。

### Secure Ingress 与平台安全

- T-ARCH-01：Host、Obs、Gateway 与 Bridge 的所有写接口统一校验 `principal/scope/signature_or_token/rate_limit` 并写审计记录；缺失、过期、串用、重放和越权请求全部 fail closed，Obs 不再提供未治理写旁路；Pentest 必须使用真实 principal/绑定 token 且只能在批准 Lab scope 运行。
- T-ARCH-02：LINE、Feishu、WeCom 等 webhook 使用原始 body/header 完成平台验签；Gateway ingress/reply 具备双向认证、durable inbox/outbox、持久幂等、ack/retry/DLQ，重启和重复投递不产生重复外发。
- T-ARCH-03：Service Catalog/port registry 能静态检测端口冲突、公开绑定和未认证本地桥；MateEngine/VRM 等冲突消失，设备桥默认 loopback、协议版本明确、secret/config snapshot 全程脱敏；新增 `edge_client → host_brain.*` 实现导入由 CI 阻断并登记既有债务；配置的模型 provider 必须成功构造或以稳定原因失败，禁止缺失导入/宽泛异常触发静默伪降级；未具 digest/signature/SBOM/quarantine/sandbox 的 Skill/Provider bundle 不能热导入 Host。

### Contracts、Kernel、Composition 与依赖治理

- T-ARCH-04：生成目标依赖图/SCC 与批准矩阵；建立“不得新增双向边、底层→`app` 边或 Edge→Host 实现边”的 CI 护栏，既有债务逐项绑定 owner/remove-by；全量归零在 T-ARCH-22/T-ARCH-29 复验。
- T-ARCH-05：共享 contracts 的 command/event/error/config/risk/embodiment envelope 支持版本化 round-trip 和 consumer-driven compatibility；未知主版本、缺失必填字段及不兼容降级稳定拒绝。
- T-ARCH-06：Composition Root 对服务创建、启动、健康、降级和关闭只有一个 owner；Host/Worker/Query profile 可隔离装配，Edge 共享合同可在不导入 Host 实现时验证；同一配置输入产生确定且脱敏的 topology/config snapshot，Edge 完整独立安装由 T-ARCH-22 验收。

### Host Application 与 Task/Agent Runtime

- T-ARCH-07：HTTP/CLI/Channel adapter 不承载 domain、repository 或 provider 真相；每个写命令均通过 Application Port，同一 action 只产生一个 AuthorizationGrant、一个 Task/Attempt lineage 和一个副作用 receipt。
- T-ARCH-08：复杂任务只由 Canonical Task/Agent Runtime 规划和调度一次；legacy executor 仅作兼容 facade，shadow compare 不产生副作用，崩溃恢复、lease 竞争和重复提交保持 exactly-once business effect。
- T-ARCH-09：Host 主链巨石（Ingress、Conversation、Executive、Task/Agent Runtime、Surface facade）已按行为切片，其他 `>1500` 行文件均有 owner/remove-by；禁止新增 `>800` 行混合职责文件，legacy facade 具调用遥测和删除版本，最终 usage=0 由 T-ARCH-29 复验。

### Digital Life Kernel、Surface 与 Identity

- T-ARCH-10：Digital Life 使用单一时钟、版本化状态、OCC 和 journal；并发推进无 silent overwrite，相同事件可确定性重放到相同 affect/drive/appraisal/relationship 状态，长期字段 writer 唯一。
- T-ARCH-11：Surface Context 构造与 WorldProjection 查询保持只读；Scene/Proactive/Embodiment 决策只通过 durable outbox 产生动作，ActionArbiter 只能收紧 Governor 权限且不能覆盖用户显式任务。
- T-ARCH-12：Identity 采用 immutable baseline + versioned overlay，snapshot/diff/activate/rollback 可审查且不含私密正文；`RelationshipProjection` 是唯一写模型，Bond/Neo4j 仅为可重建投影，WorldProjection 无反写接口。跨版本 migration/regression 与发布阻断由 T-ARCH-28/T-ARCH-30 验收。

### Memory、Knowledge、Event 与 Persistence

- T-ARCH-13：Storage Catalog 为每张表、文件和索引声明 owner、writer、retention 与 migration scope；production 直接访问 Ledger 私有连接和 inline DDL 为 0，跨存储事务只经明确 UoW/outbox。
- T-ARCH-14：component-scoped migration catalog 无重复、缺口和 drift；空库、受支持旧库、expand/contract、备份恢复和 rollback drill 全通过，失败不会留下半迁移 schema。
- T-ARCH-15：关键状态与 Event/Outbox 同事务提交，projection 可幂等重建并对账；durable payload 不含 raw thought/secret/cookie/private body，领域知识隔离且 Canon promotion 校验审批内容 hash。

### Domain Pack 与 Capability Platform

- T-ARCH-16：Domain Pack manifest/SDK 的 command、tool、state、knowledge、policy、projection 与 UI contribution 可版本校验、隔离加载、停用和回滚；Domain 不反向依赖 Application/Infrastructure，也不拥有平台级存储或线程。
- T-ARCH-17：Pentest 仅在授权 Lab 隔离环境运行，并复用共享 WorkOrder/TaskGraph/RunStore/Governor；普通聊天检索不到 Pentest 知识，Pentest 对 WorldCanon 的直接读写为 0，知识提升必须经 owner review。
- T-ARCH-18：Skill/Tool/MCP/Pentest 等 registry 通过统一 Capability Catalog 和公开 Broker port 适配；风险尺度转换稳定且保留来源证据，Domain Pack 不得自建 policy/token/audit gate。全局唯一 Broker/Grant/Token 与旁路归零由 T-ARCH-21 验收。

### Model、Prompt、Governor 与 Security Plane

- T-ARCH-19：统一 Model Gateway 独占 provider routing、timeout/retry/circuit/cache/streaming/budget/usage；provider 故障和模型降级可观测且不被宽泛异常静默吞掉，同一请求的 attempt lineage 可完整回放。
- T-ARCH-20：Prompt/Context asset 具备版本、digest、owner、适用模型与注入边界；私有 helper 跨层导入为 0，污染输入、越权 context ref、PII/secret 泄漏和提示降级均被稳定检测并审计。
- T-ARCH-21：AuthorizationGrant 绑定 subject/action/scope/content_hash/version/expiry/once；所有执行经过唯一 Capability/Tool Broker、Grant/Token 与 audit gate，executor 旁路为 0；Skill/Provider bundle 必须经过 digest/signature/SBOM/quarantine/sandbox/owner approval/canary/rollback，未受信代码不能在 Host 主进程热导入。

### Edge、Gateway、Voice、Avatar 与 Device Runtime

- T-ARCH-22：Edge 是 Desktop Pet、MateEngine、VTS、VRM、Voice 等设备副作用的唯一 authority；Host 无直连旁路，`EmbodimentCommand` 版本和 capability handshake 一致，断线重试不重复动作。
- T-ARCH-23：ASR、TTS synthesis、audio playback、lip-sync 与 GPU/model worker 具有独立生命周期、资源预算和 watchdog；barge-in、worker 崩溃、显存压力与重启恢复保持队列/会话/审计语义一致。
- T-ARCH-24：VTS/VRM/MateEngine/Electron adapters 使用唯一端口和认证协议；emotion/expression profile 由中心 ontology 确定性编译，adapter 缺失或映射漂移时安全降级且不启用不安全浏览器配置。

### Query/Observability Plane 与 Operator Console

- T-ARCH-25：Query/Observability Plane 对 operational store 只读，写路由和私有 Ledger SQL 依赖为 0；read model 有 schema version、owner、cursor 和重建/对账证明，缓存具备明确失效与容量边界。
- T-ARCH-26：OpenAPI 不存在重复 method/path，生成的 UI client 与后端 schema 通过兼容测试；route visibility 和命令执行均按 role/capability 授权，高风险确认不能替代后端 token。
- T-ARCH-27：Task/run/event/trace/metric/log 共享 correlation IDs；Operator Console 的 replay、review、health、device 与 release 视图通过 lint/typecheck/unit/component/a11y/e2e，并在目标数据量下满足声明的查询 SLO。

### Evolution Lab、Decompat、Release 与最终封板

- T-ARCH-28：Evolution Lab 的 candidate/feedback/counterfactual/scenario/benchmark 默认隔离且无生产副作用；Skill 或 Identity migration/promotion 必须具备版本化 schema、可重复 regression/replay、风险证据、owner review、canary、rollback target 和自动回滚阈值。
- T-ARCH-29：所有 compatibility facade、旧 writer、重复 planner/runtime 和手工 UI contract 均有 usage=0 与删除证据；全部 production `>1500` 行混合职责巨石已拆分或具备批准 ADR，新增 `>800` 行混合职责文件为 0；源码树不跟踪易漂移 hashed build output，发布 artifact 可由锁定输入确定性重建。
- T-ARCH-30：ReleaseManifest v2 包含 commit、packages、schema、protocol、model/prompt/avatar assets、config digest、SBOM、gates 和 rollback compatibility；组件矩阵 CI、security/chaos/performance、backup/restore、升级/降级及最终 rollback drill 全部生成 Seal 证据。
