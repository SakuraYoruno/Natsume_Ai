鍙椾紬锛欳ODEX
鏍囬锛歍ESTPLAN锛堟渶灏忓崰浣嶏級

鐗堟湰锛歷0.1
鏃ユ湡锛?026-02-28

璇存槑锛氭湰鏂囦欢浠呮彁渚涚紪鍙锋钀戒笌鏂█鍙ｅ緞鍗犱綅锛屽悗缁寜 DevTasks 閫愭潯琛ュ叏銆?
## T-Flow
- T-Flow-01锛歛ctive.pointer 鍚堟硶 JSON + 涓夌幆澧冮敭瀹屾暣銆?- T-Flow-02锛歛ctive.pointer 涓?docs/60_trace/flows/index.md 璺緞涓€鑷淬€?
## T-Intent
- T-18锛欼ntent 瀹芥澗瑙ｆ瀽 + 涓ユ牸鏍￠獙銆?- T-19锛氶噸鏀句繚鎶わ紙nonce/request_id锛夋嫆缁濄€?- T-20锛氶『搴忔牎楠屽け璐ユ嫆缁濄€?- T-21锛氳繃鏈熸嫆缁濄€?- T-22锛氬け璐ユ槧灏?reason_code 绋冲畾銆?- T-Intent-01锛歩ntent_log 鍏峰 proposer/model/prompt/tool_visibility 鏈€灏忓瓧娈点€?- T-Intent-02锛歱roposal/decision/exec 涓夌被鏃ュ織瀛楁鍒嗙銆?
## T-LLM
- T-LLM-01锛氬崟涓悎娉?JSON锛涚姝㈠墠鍚庣紑鏂囨湰锛涘瓧娈靛畬鏁淬€?
## T-Audit
- T-Audit-01锛歩ntent/governor/skill_exec 涓夎仈鍙覆鑱斿洖鏀俱€?- T-Audit-RET-01: Retention/GC must preserve triptych replay chain keys and active undo windows; pruned rows and blocked GC actions must remain auditable.

- T-Audit-03: Request-level replay diagnostics must explain missing links across intent/governor/skill chains with stable reason_code mapping.
- T-Replay-01: Given a request_id, replay tool must output deterministic report fields (`status/reason_code_chain/evidence_refs`) and enumerate missing-link causes (`TRACE_MISSING_*`).

## T-Gov
- T-Gov-01锛歊1~R7 rule_trace 鍏峰 gate_id/hit/threshold_summary/snapshot_summary/reason_hint 涓斿悓杈撳叆绋冲畾銆?
## T-Meta
- T-Meta-01: Hard-state metabolism normalization/evolution must be deterministic, and governor rows must carry replayable snapshot_refs (`governance_snapshot` + `hard_state`) derived from the same runtime snapshot input.

## T-QA
- T-67锛氭牸寮忔紓绉昏Е鍙戜竴娆￠噸鍐?闄嶇骇銆?- T-68锛氫吉鎵ц瀹ｅ憡鎷︽埅銆?- T-69锛欳anon-as-fact 鎷︽埅銆?- T-70锛氭晠闅滈檷绾т粛杈撳嚭鍚堟硶 JSON銆?- T-71锛氳秴鏃堕檷绾т粛杈撳嚭鍚堟硶 JSON銆?
## T-Drill
- T-Drill-01锛氱敓浜ф紨缁冨寘蹇呴』瀵?`cold_start_no_key/provider_down/rollback_conflict` 杈撳嚭鍙В鏋愬崟 JSON 缁撴灉锛屽苟绋冲畾鍖呭惈 `scenario_id/status/reason_code_chain/replay_ref` 涓庢晠闅滃垎绫诲缃彛寰勩€?
## T-Canon
- T-Canon-01锛歋tyle Canon 鐗堟湰鍖栧啓鍏ュ苟璁板綍 proposal/change锛堝惈鍓嶅悗 version/hash锛夈€?- T-Canon-02锛歋tyle Canon 浠呮敞鍏?style_pack 涓旂姝㈡墽琛屼簨瀹炲鍛娿€?
## T-WCanon
- T-WCanon-01锛歐orld Canon 鏂囦欢鍚屾绱㈠紩鍒?`world_canon_doc` 涓斿啓 `canon_change_log`銆?- T-WCanon-02锛歐orld Canon 浠呬互寮曠敤鍙ｅ緞娉ㄥ叆 authoritative锛坈itation_only锛夈€?- T-WCanon-03锛歐orld Canon 鏂囦欢涓庣储寮?version/hash 婕傜Щ鍙娴嬶紝鍧忔枃浠朵笉寰楅潤榛樿烦杩囷紙闇€鍙璁★級銆?
## T-Mem
- T-Mem-01锛欵pisodic 鍐欏叆鏃?`evidence_refs` 蹇呭け璐ヤ笖鏈夌ǔ瀹?`reason_code`銆?- T-Mem-02锛歚merge/decay/delete` 鍏ㄧ敓鍛藉懆鏈熷繀椤诲啓 `memory_ops_log` 涓斿彲鍥炴斁銆?
## T-Vault
- T-Vault-01锛歚reversible=true` 鐨勬墽琛岃矾寰勫繀椤讳骇鍑?`manifest_ref`锛岀己澶辨椂绋冲畾鎷掔粷銆?- T-Vault-02锛歮anifest digest 澶遍厤鏃舵挙閿€鎷掔粷涓斿彲瀹¤锛汫C 涓嶅緱鐮村潖鏈夋晥 undo 绐楀彛銆?
## T-Airlock
- T-39: Egress must include sanitization_report_json (removed_fields/redactions/payload_digest), otherwise reject with AIRLOCK_BLOCKED_EGRESS and log airlock_log.
- T-40: Ingress content must be quarantined and classified (FACT/DRAFT_CODE/DRAFT_INSTRUCTION/UNKNOWN) and never directly execute.

## T-Sandbox
- T-Sandbox-01: DRAFT_CODE sandbox execution must produce structured results (sandbox_id/status/exit_code/stdout/stderr/duration_ms/evidence_ref/code_digest), enforce timeout kill and resource limits with stable reason_code, maintain process isolation (no main-process side effects), and write sandbox_exec_log for audit replay; existing T-39/T-40 must not regress.

## T-Tool
- T-Tool-01: ToolMeta pack is metadata-only (no execution payload), with required fields and context-pack validation compatibility.
- T-Tool-02: Top-K selection only exposes a subset and hides WRITE tools by default; tool_visibility_hash is deterministic for same input.
- T-Tool-03: ToolBroker is the single execution entry; WRITE without valid token must reject (TOKEN_INVALID/CONFIRM_REQUIRED), and executed actions must carry evidence_ref.

## T-MCP
- T-MCP-01: MCP provider tools are synced as governed ToolMeta entries, and MCP WRITE without valid token must reject while successful calls remain auditable by request_id/intent_id/evidence_ref.

## T-Orch
- T-Orch-01: Graph orchestration runtime must validate graph config deterministically (`graph_id/version/entry_node/node_bindings/edges/fallback_edges`), emit stable execution envelope (`graph_status/reason_code/stage/replay_ref`), and classify node-missing/edge-invalid/timeout/dependency-down failures with replayable trace.

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
- T-Release-01: Release checklist for `M0~M3` must remain deterministic (`milestone/assets/checksums/required_services`), rollback mapping must resolve `release_id/rollback_target/impact_scope`, and flow pointer cross-check must remain consistent with `T-Flow-02`.

