受众：CODEX
标题：TESTPLAN（最小占位）

版本：v0.1
日期：2026-02-28

说明：本文件仅提供编号段落与断言口径占位，后续按 DevTasks 逐条补全。

## T-Flow
- T-Flow-01：active.pointer 合法 JSON + 三环境键完整。
- T-Flow-02：active.pointer 与 docs/60_trace/flows/index.md 路径一致。

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

## T-Canon
- T-Canon-01：Style Canon 版本化写入并记录 proposal/change（含前后 version/hash）。
- T-Canon-02：Style Canon 仅注入 style_pack 且禁止执行事实宣告。

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

## T-Tool
- T-Tool-01: ToolMeta pack is metadata-only (no execution payload), with required fields and context-pack validation compatibility.
- T-Tool-02: Top-K selection only exposes a subset and hides WRITE tools by default; tool_visibility_hash is deterministic for same input.
- T-Tool-03: ToolBroker is the single execution entry; WRITE without valid token must reject (TOKEN_INVALID/CONFIRM_REQUIRED), and executed actions must carry evidence_ref.

## T-MCP
- T-MCP-01: MCP provider tools are synced as governed ToolMeta entries, and MCP WRITE without valid token must reject while successful calls remain auditable by request_id/intent_id/evidence_ref.

## T-Channel
- T-Channel-01: WeChat ingress/egress events are traceable by thread_key/message_id/request_id; duplicate ingress is ignored and failed egress includes reason_code/stage.

## T-Avatar
- T-Avatar-01: VTube Studio talk gate (TALK_START/TALK_END) and emotion_tag->expression_id mapping are deterministic and auditable; invalid tags or free-form action params must be rejected with stable reason_code and stage.

## T-ClawdUI
- T-41: ClawdBot UI action with confidence < 0.80 must be blocked; no click side effect is produced and clawd_action_log records confidence/target_desc/reason_code.
- T-42: Redline scene and missing UI element must be rejected with stable reason_code (VISUAL_REDLINE/UI_ELEMENT_NOT_FOUND) and guard stage in clawd_action_log.
- T-43: Successful UI action must produce before/after artifacts and manifest linkage; clawd_action_log stores before_artifact_ref/after_artifact_ref/manifest_ref for replay.

## T-Svc
- T-Svc-01: Service registry health output must be parseable and deterministic; same unavailable kind maps to the same reason_code/stage, and each health row is auditable by request_id in service_health_log.
- T-Boot-01: Startup orchestration must output deterministic boot snapshot (`service_id/owner/required/status/reason_code/stage`), include dependency probe matrix, and keep config/probe failures auditable with stable reason_code mapping.

## T-Config
- T-Config-01: Host/Edge layered config loading (env/default/local override) must produce deterministic stage/reason_code on invalid inputs, keep cold-start defaults parseable, and map runtime unavailability consistently into service health outputs.

## T-Ret
- T-Ret-01: Embedding contract output is stable and auditable; unavailable/timeout/input-invalid must map to deterministic `reason_code` (`RETR_EMBED_UNAVAILABLE/RETR_EMBED_TIMEOUT/RETR_EMBED_INPUT_INVALID`) under `S_RET_EMBEDDING`, and each call must write `retrieval_event_log`.
- T-Ret-02: Reranker contract keeps evidence candidates shape stable and auditable; unavailable path degrades with deterministic reason_code/stage.
- T-Ret-03: Evidence pack fusion output is schema-stable and replayable by `request_id/intent_id/evidence_ref`, including degraded retrieval paths.

## T-Bond
- T-50: Bond SSOT updates can only be applied through the single-writer path (`bond_dynamics`) and must create replayable `event_id` with `evidence_refs`.
- T-51: Bond update requests without `evidence_refs` must be rejected with stable reason_code and logged in `bond_update_log`.
- T-52: Runtime single-writer boundary for Neo4j must reject non-`bond_dynamics` writers with stable reason_code.
- T-53: Bond events and graph relations (`HAS_EVENT/ABOUT/AFFECTS`) must be replayable by `user_id` and `event_id`.
- T-54: Neo4j bond constraints/index assets must be loadable from migrations, and summarizer path must not perform SSOT writes.
- T-74: Bond summary must be derived-only and must not contain sensitive numeric values; only bucketed fields plus replay refs (`event_id/evidence_refs`) are allowed.
- T-75: Bond summary must inject into `context_pack.bond_summary` slot without mixed-slot leakage and remain valid under `T-CP-01` isolation checks.

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

## T-Vision
- T-VisionGate-01: Gaming mode must block screen-monitor/OCR requests with stable reason_code/stage and auditable event linkage; response must not claim unsupported visual observations.
- T-Vision-01: Non-gaming OCR output must remain schema-stable (`request_id/ts_ms/source_ref/engine_id/text_blocks[]` with `text/bbox/conf`) and source_ref must be replayable via vision_event_log.
- T-Vision-02: Camera recognition events must be replayable by evidence_ref, include explicit low-confidence marking in object outputs, and degrade/reject with stable reason_code/stage when evidence is missing or detector is unavailable.
