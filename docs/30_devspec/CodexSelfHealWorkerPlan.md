受众：CODEX
标题：Codex Self-Heal and Self-Evolve Plan

# Codex Self-Heal and Self-Evolve Plan

> SUPERSEDED, 2026-04-24: historical design only. Runtime code has hard-cut to Codex Runtime v2 (`WorkOrder -> CodexRuntimeExecutor -> Studio/Plane -> ResultContract`) with `codex_self_heal` and `/internal/codex/immune/*`; legacy `codex_codegen`, `execute_codex_codegen`, `HOST_CODEX_SELF_HEAL_*`, and `/internal/codex/self_heal/*` paths are intentionally removed.

## Overview

## 0. 当前链路入口映射（2026-06-23）

本文件为历史设计稿（2026-04-24 标记为 `SUPERSEDED`），其执行入口与运行语义不再代表主链真相。当前代码链路应按以下口径读取：

- 统一入口：`POST /v1/chat` -> `host_brain.app.asgi:app` -> `app_factory` / `router_registry` -> `chat_routes.handle_v1_chat_route()` -> `chat_handler.handle_chat()` -> `thalamus_agent.classify_and_compile_intent_draft()` -> `Governor.evaluate_intent()` -> `cortex_orchestrator` / `agent_runtime_shared` -> `executive_runtime_core` -> `surface_reply_runtime` -> `SurfaceMainChain` -> `ReplyEnvelope`。
- Agent/Codex 写模式的当前契约是 `AgentWorkOrder` + `WorkspacePolicy` + `approval_policy`；历史 `codex_codegen` / `codex_self_heal` 术语不得作为运行入口依据。
- 审计事实入口：`intent_log`、`governor_log`、`skill_exec_log`、`sqlite_ledger`。
- 当前 Codex 自愈链路与运行态状态见：
  - `docs/30_devspec/DevSpec.md`
  - `docs/30_devspec/DevTasks.md`
  - `docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md`

本文中若有与现网冲突的实现假设，请以对应事实文档为准，不按本文件进行执行。

This document defines how `codex_codegen` evolves from a read-only answer generator into a durable worker that helps Natsume recover from failures and implement controlled improvements.

The design goal is not "let Codex casually write code". The goal is:

- `self_heal`: diagnose and repair failures with bounded, auditable changes
- `self_evolve`: extend tests, routes, skills, and runtime quality when telemetry shows capability gaps

## Current Problems

The current `codex_codegen` path is useful only as an advisory tool.

- It runs `codex exec` in `read-only` mode.
- It uses `--ephemeral`, so the session cannot be resumed.
- It stores only the last message in a temp file and then deletes it.
- It falls back to the current repository when no workspace is provided.
- It does not keep durable run artifacts.
- It does not expose a reusable thread id.
- It does not integrate the existing sandbox as a verification stage.

As a result, it cannot reliably support multi-step repair work across repositories.

## Scope

This plan covers:

- dynamic project/workspace resolution
- persistent Codex run artifacts
- resumable Codex runs
- worker/read-only execution modes
- structured execution results
- reuse of the existing sandbox for verification
- foundations for self-heal and self-evolve orchestration

This plan does not yet require:

- full automatic production rollout
- automatic git push or pull request creation
- non-Python generic command sandboxing

## Target Architecture

### 1. Trigger Layer

Codex work should be triggered by:

- explicit user requests
- failed tests
- degraded health probes
- runtime faults and repeated reason codes
- capability-gap telemetry

### 2. Intent Layer

`thalamus_agent` remains the canonical owner of intent compilation.

Expected canonical payload for Codex work:

```json
{
  "route": "skill",
  "target_id": "codex_codegen",
  "args": {
    "task": "fix the failing route",
    "workspace": "D:/repo",
    "mode": "worker",
    "resume_thread_id": null,
    "repo_alias": null,
    "file_path": null,
    "git_url": null,
    "run_checks": true
  }
}
```

### 3. Project Locator

Codex must not assume a single repository.

Resolution order:

1. explicit `workspace`
2. `file_path` and climb to repo root
3. `repo_alias` lookup from local alias registry
4. `git_url` clone into managed workspace area
5. explicit ad-hoc temp workspace for one-off scripts

If none resolves, the request must fail explicitly instead of silently falling back to the current repo.

### 4. Codex Worker

`codex_codegen` supports multiple run modes:

- `inspect`: read-only analysis
- `worker`: modify files in the target workspace
- `self_heal`: worker mode plus targeted repair framing
- `self_evolve`: worker mode plus improvement framing

The worker must:

- persist the Codex session
- capture JSON event streams
- preserve the final assistant message
- record changed files and git status when available
- return a structured payload instead of only plain text

### 5. Verification Layer

Existing `SandboxExecutor` is reused for Python verification work:

- generated script smoke checks
- small validation programs
- focused diagnostic snippets

Codex remains responsible for planning file edits. Sandbox remains responsible for safe verification execution.

### 6. Audit Layer

Each Codex run should produce durable artifacts:

- `metadata.json`
- `events.jsonl`
- `stdout.log`
- `stderr.log`
- `last_message.txt`
- `changed_files.json`
- `verification.json`

## Repository and Workspace Strategy

### Alias Registry

A local alias registry maps short names to repo roots.

Suggested default path:

- `D:/Natsume_Ai/runtime/codex/repo_aliases.json`

Example:

```json
{
  "natsume-host": "D:/Natsume_Ai",
  "natsume-edge": "D:/EdgeRuntime",
  "scratch": "D:/work/scratch"
}
```

### Managed Clone Area

When `git_url` is supplied and no local workspace exists, clone into:

- `D:/Natsume_Ai/artifacts/codex_clones/<slug>/`

This stays separate from the currently open workspace.

### Ad-Hoc Workspace

When the task is "write a script" and no repo is required, create:

- `D:/Natsume_Ai/artifacts/codex_runs/<request_id>/workspace/`

## Run Artifacts

Each run writes to:

- `D:/Natsume_Ai/artifacts/codex_runs/<request_id>/`

`metadata.json` should include:

- request id
- mode
- resolved workspace
- session/thread id
- resumed flag
- git info when available
- timestamps

## Structured Result Contract

Expected return shape from `execute_codex_codegen(...)`:

```json
{
  "status": "EXECUTED",
  "detail": "codex exec completed",
  "mode": "codegen",
  "run_mode": "worker",
  "workspace": "D:/repo",
  "thread_id": "uuid",
  "artifacts_dir": "D:/Natsume_Ai/artifacts/codex_runs/req-001",
  "result_text": "final assistant message",
  "changed_files": ["host_brain/app/foo.py"],
  "git_diff_stat": "1 file changed, 8 insertions(+), 2 deletions(-)",
  "verification": {
    "status": "NOT_RUN",
    "reason": "verification not requested"
  }
}
```

## Prompting Policy

The worker prompt should change by mode.

- `inspect`: do not modify files
- `worker`: allowed to modify files in the resolved workspace only
- `self_heal`: prioritize minimal repair, root cause, and relevant tests
- `self_evolve`: prioritize additive improvements with tests and auditability

## Verification Policy

### Phase 1

- collect workspace and run artifacts
- support Python-only sandbox verification placeholders
- return structured verification metadata even when checks are not executed

### Phase 2

- run focused Python verification via `SandboxExecutor`
- capture verification output into `verification.json`

### Phase 3

- add broader command-safe verification with explicit allowlists

## Rollout Plan

### Phase A: Persistent Worker Foundation

- add project locator
- remove implicit workspace fallback
- add alias registry support
- add resumable Codex runs
- persist artifacts
- add worker/inspect mode handling
- add structured return payload

### Phase B: Verification Integration

- connect Python verification through existing sandbox
- persist verification artifacts
- expose verification status in final payload

### Phase C: Self-Heal Orchestration

- convert health/test failures into Codex repair jobs
- map evidence refs into Codex context
- run targeted verification automatically

### Phase D: Self-Evolve Orchestration

- use capability gaps and repeated failures as evolution triggers
- let Codex add tests and low-risk improvements

## Files Expected to Change

### Phase A

- `host_brain/skills/impl/ide_executor.py`
- `host_brain/skills/ide_registry.py`
- `host_brain/skills/project_locator.py` or equivalent helper module
- `tests/unit/test_ide_executor_codex.py`
- `tests/unit/test_skill_ide_registry.py`
- new tests for project location and resume behavior

### Phase B+

- orchestration and audit modules
- health/runtime drill entrypoints
- additional integration tests

## Acceptance Criteria

### Phase A

- `codex_codegen` no longer silently defaults to the current repo
- a request can target a repo by workspace or alias
- a request can resume a prior thread id
- artifacts persist after execution
- returned payload includes `thread_id` and `artifacts_dir`
- worker mode uses writable Codex sandbox settings

### Phase B

- Python verification can run through the existing sandbox
- verification artifacts are persisted and referenced

### Phase C

- a failed probe/test can trigger a repair job and report a structured outcome

## Risks

- writable Codex runs are more dangerous than advisory runs
- incorrect workspace resolution could target the wrong repo
- clone-based workflows need careful cleanup and path control
- verification commands must remain bounded and auditable

## Risk Controls

- no implicit repo fallback
- explicit artifact storage for every run
- mode-based permission policy
- keep worker scope to the resolved workspace only
- add unit tests for repo resolution, resume, and artifact persistence

## Implementation Status

### Phase A: Persistent Worker Foundation

- [x] detailed plan documented
- [x] project locator added
- [x] implicit workspace fallback removed
- [x] alias-based resolution added
- [x] resumable Codex runs added
- [x] persistent artifacts added
- [x] structured return contract added
- [x] tests updated and passing

### Phase B: Verification Integration

- [x] optional Python verification hook added via `verification_code`
- [ ] automatic verification orchestration from Codex-generated checks

### Phase C: Self-Heal Orchestration

- [x] natural-language self-heal intent now compiles into a Codex task with runtime health evidence
- [x] degraded `/health` and `/ready` reports can now be compiled into structured Codex self-heal proposals
- [x] internal self-heal proposal endpoint added at `/internal/codex/self_heal/proposal`
- [x] explicit internal dispatch endpoint added at `/internal/codex/self_heal/dispatch`
- [x] explicit internal dispatch now supports background job submission plus `/internal/codex/jobs/{job_id}` polling
- [x] `codex_codegen` now uses run-mode tiered timeout defaults so `self_heal` and `self_evolve` are not forced into short inspect-style timeouts
- [x] controlled auto-dispatch endpoint added at `/internal/codex/self_heal/auto_dispatch` with consecutive-failure thresholding and cooldown
- [x] current auto-dispatch policy state is queryable through `/internal/codex/self_heal/auto_state`
- [x] optional background monitor now starts from FastAPI lifespan and periodically calls auto-dispatch when `HOST_CODEX_SELF_HEAL_AUTODISPATCH_ENABLED=true`
- [x] monitor runtime status is queryable through `/internal/codex/self_heal/monitor_status`
- [x] two-stage auto-dispatch policy now supports `inspect -> self_heal` escalation for repeated failures of the same fingerprint
- [x] HostTaskScheduler can now register `codex_self_heal_auto_dispatch_tick` and run the same auto-dispatch policy when `HOST_CODEX_SELF_HEAL_SCHEDULER_ENABLED=true`
- [ ] runtime faults automatically dispatch Codex execution without an operator, scheduler, or caller explicitly invoking the auto-dispatch endpoint

### Phase D: Self-Evolve Orchestration

- [x] natural-language self-evolve intent now compiles into a Codex task with runtime repo targeting
- [ ] capability-gap driven evolution flow implemented

## Current Execution Notes

### Background Monitor Controls

The automatic monitor remains opt-in. Enable it only when the host should actively watch health state and submit Codex inspection jobs on its own.

- `HOST_CODEX_SELF_HEAL_AUTODISPATCH_ENABLED=true`
- `HOST_CODEX_SELF_HEAL_AUTODISPATCH_SOURCE=health|readiness`
- `HOST_CODEX_SELF_HEAL_AUTODISPATCH_INTERVAL_SECONDS=300`
- `HOST_CODEX_SELF_HEAL_AUTODISPATCH_MIN_CONSECUTIVE=3`
- `HOST_CODEX_SELF_HEAL_AUTODISPATCH_COOLDOWN_SECONDS=900`
- `HOST_CODEX_SELF_HEAL_AUTODISPATCH_TIMEOUT_SECONDS=120`
- `HOST_CODEX_SELF_HEAL_AUTODISPATCH_RUN_MODE=inspect|self_heal|worker|self_evolve`
- `HOST_CODEX_SELF_HEAL_AUTODISPATCH_ESCALATION_POLICY=inspect_then_self_heal|inspect_only`
- `HOST_CODEX_SELF_HEAL_AUTODISPATCH_SKIP_DEPENDENCY_PROBES=false`
- `HOST_CODEX_SELF_HEAL_SCHEDULER_ENABLED=true`
- `HOST_CODEX_SELF_HEAL_SCHEDULER_INTERVAL_SECONDS=300`

When `HOST_CODEX_SELF_HEAL_SCHEDULER_ENABLED=true`, the explicit monitor thread stands down and exposes `scheduler_managed=true` through `/internal/codex/self_heal/monitor_status`. In that mode, the same auto-dispatch policy is driven by the HostTaskScheduler task `codex_self_heal_auto_dispatch_tick`, which is visible through `/host-scheduler/tasks`.

Implemented in this pass:

- dynamic workspace resolution through explicit workspace, file path, repo alias, git url, or ad-hoc temp workspace
- persistent run artifacts under `artifacts/codex_runs/<request_id>/...`
- support for resumable runs through `resume_thread_id`
- separate `run_mode` policy (`inspect`, `worker`, `self_heal`, `self_evolve`)
- structured result payloads for success, timeout, and reject paths
- optional Python verification using the existing `SandboxExecutor`
- `workspace_strategy=runtime_repo` for self-heal/self-evolve tasks targeting the current runtime repository
- `thalamus` direct routing for natural-language `self_heal` / `self_evolve`
- automatic runtime health evidence packing for Codex self-heal tasks
- `host_brain/app/codex_self_heal.py` builder for compiling degraded health/readiness reports into canonical `codex_codegen` self-heal proposals
- `/internal/codex/self_heal/proposal` for live proposal introspection from health or readiness state
- `/internal/codex/self_heal/dispatch` for explicit proposal-to-execution handoff into `codex_codegen`
- optional `include_self_heal_proposal=true` support on `/health` and `/ready` so degraded runtime snapshots can expose a ready-to-dispatch Codex repair task without changing the default contract
- explicit dispatch keeps read-only endpoints safe: `/health` and `/ready` can expose repair intent without directly mutating the repository
- explicit dispatch now supports `background=true`, allowing long-running self-heal jobs to run asynchronously and be polled via `/internal/codex/jobs/{job_id}`
- `codex_codegen` timeout defaults are now tiered by `run_mode`: `inspect=120s`, `worker=300s`, `self_heal=600s`, `self_evolve=900s`
- controlled auto-dispatch is now available through `/internal/codex/self_heal/auto_dispatch`; it tracks repeated degraded proposals by fingerprint, waits for a configurable consecutive-failure threshold, enforces cooldown, and only then submits a background job
- controlled auto-dispatch now supports an escalation policy: repeated failures can stay in `inspect` mode or escalate from `inspect` to `self_heal`
- current auto-dispatch counters, last fingerprint, cooldown, and last submitted job id are exposed via `/internal/codex/self_heal/auto_state`
- optional background monitor is controlled by env vars and starts during `runtime_lifespan`; it repeatedly calls the same auto-dispatch logic instead of introducing a second dispatch path
- current monitor process state, last cycle result, and thread status are exposed via `/internal/codex/self_heal/monitor_status`
- HostTaskScheduler can now register `codex_self_heal_auto_dispatch_tick` and drive the same auto-dispatch cycle on an interval; monitor mode now yields to this scheduler-managed mode instead of running a second loop
- HostTaskScheduler startup now primes periodic tasks instead of running them immediately on process boot; explicit `/host-scheduler/tasks/{id}/trigger` remains the fast path for immediate execution
- `start_host_side.ps1` now materializes `runtime/host/host.runtime.env` so runtime launch overrides survive the host bootstrap chain instead of being lost between wrapper processes
- `host_brain.__main__` now loads `runtime/host/host.runtime.env` as a runtime override layer after `.env`, and `stop_host_side.ps1` removes that file on shutdown
- `start_host_side.ps1` now preserves explicitly provided process env overrides over `.env` defaults, which allows live drills to force `HOST_BACKGROUND_TASKS_ENABLED=true` and self-heal scheduler settings without editing repository config

Validated in this pass:

- unit tests: project locator, ide executor, ide skill registry
- integration tests: `test_http_skill_route_smoke.py`, `test_http_chat_persona_acceptance.py`
- integration route smoke covers natural-language self-heal -> `codex_codegen`
- real smoke: timeout path now still returns `artifacts_dir` and `workspace`, proving failed runs remain inspectable
- unit tests: `test_codex_self_heal.py`
- integration tests: `test_internal_codex_routes.py`
- health/readiness contract regressions remain green after proposal attachment support
- live internal run: proposal and dispatch both execute, with explicit short-timeout inspect mode returning a durable `CODEX_EXEC_TIMEOUT` result and artifacts for inspection
- integration route smoke covers background self-heal submission and `/internal/codex/jobs/{job_id}` polling
- live internal background run: `background=true` submission succeeded, produced a real job id, and reached a terminal `FAILED` state with `CODEX_EXEC_TIMEOUT`, confirming that asynchronous dispatch and status polling are both live
- integration tests now cover `auto_dispatch` thresholding, cooldown behavior, healthy reset, and `/internal/codex/self_heal/auto_state`
- live internal auto-dispatch run: first call stayed at `WAITING_THRESHOLD`, second call crossed the configured threshold and submitted a background job, and the resulting job reached a terminal `FAILED` state with `CODEX_EXEC_TIMEOUT`, confirming the full threshold -> dispatch -> poll loop is live
- integration tests now cover monitor status defaults plus enabled/disabled monitor startup behavior
- live monitor run: enabling `HOST_CODEX_SELF_HEAL_AUTODISPATCH_ENABLED=true` during lifespan startup produced a running monitor, a real auto-submitted job id, and a terminal failed job with durable artifacts, confirming the startup monitor -> auto-dispatch -> job poll loop is live
- integration tests now cover `inspect -> self_heal` escalation when the same degraded fingerprint persists across auto-dispatch cycles
- live escalation run: two consecutive `auto_dispatch` calls for the same degraded fingerprint first submitted an `inspect` job, then escalated to a `self_heal` job on the next cycle, confirming the two-stage policy is live
- integration tests now cover scheduler-managed monitor backoff plus `/host-scheduler/tasks` visibility for `codex_self_heal_auto_dispatch_tick`
- unit tests now cover HostTaskScheduler startup semantics so periodic tasks do not all fire once at boot, while `trigger_now()` still forces an immediate run
- integration tests now cover scheduler-triggered real auto-dispatch flow, including `monitor_status`, `auto_state`, and background self-heal job completion
- runtime probe script `scripts/runtime/test_codex_self_heal_scheduler.py` now provides a reusable live drill entrypoint, and `generate_full_run_report.py` now includes this scheduler self-heal probe in the aggregate report
- runtime probe now derives an effective polling window from live `monitor_status.timeout_seconds`, preventing false negatives when Codex jobs are still within their configured execution budget
- live host drill now passes end-to-end through `/host-scheduler/tasks/{id}/trigger` -> `/internal/codex/self_heal/auto_state` -> `/internal/codex/jobs/{job_id}` on `http://127.0.0.1:18080`, with a real terminal Codex job and durable artifacts report in `artifacts/release/reports/codex_self_heal_scheduler_probe_1776420547.json`
- `/internal/codex/self_heal/auto_state` now reflects scheduler runtime defaults even before the first live cycle runs, so `min_consecutive_failures`, `cooldown_seconds`, and escalation policy no longer misleadingly fall back to the old idle defaults during live drills
- `/internal/codex/self_heal/monitor_status` now exposes scheduler-managed auto-dispatch settings (`source`, `interval_seconds`, `timeout_seconds`, `run_mode_override`, `escalation_policy`), which lets the runtime probe wait for the real Codex execution budget instead of a fixed short timeout
- the runtime probe now preserves the last successful `monitor_status`, `auto_state`, and `job` snapshots across transient polling timeouts, preventing a late slow response from overwriting an otherwise valid `DISPATCHED` state in the final report
- the runtime probe now infers the effective self-heal `source` from live `monitor_status` when `--source` is omitted, dynamically switches its `auto_state` polling to that source during long polls, and falls back to later successful monitor snapshots when the first readiness probe times out
- Codex inspect artifact attribution is now clean on dirty worktrees: `changed_files.json` reports only the delta introduced during the Codex run and no longer blames the entire pre-existing repo diff on the current self-heal attempt
- self-heal inspect prompts now include stronger Windows command constraints plus explicit priority files for `SERVICE_UNAVAILABLE_EDGE_RPC` / readiness degradations (`host_brain/app/health_routes.py`, `host_brain/app/startup.py`, `host_brain/channels/edge_rpc_client.py`, `host_brain/app/internal_codex_routes.py`, `scripts/runtime/test_codex_self_heal_scheduler.py`), which materially reduced Codex stdout volume during live runs
- self-heal context now carries a compatibility note explaining that `host_brain.app.dependencies.collect_dependency_probes` is a re-export shim of `host_brain.app.startup.collect_dependency_probes`, which prevents Codex from misreading compatibility imports in `health_routes.py` as stale probe ownership drift
- latest live scheduler drill now passes end-to-end with a 210s effective polling window and a real terminal Codex job in `artifacts/release/reports/codex_self_heal_scheduler_probe_1776425354.json`; the latest observed terminal state is `COMPLETED / EXECUTED`, `auto_state.source=readiness`, `last_dispatch_success=true`, and `last_artifacts_dir=D:\Natsume_Ai\artifacts\codex_runs\req_49c1edaecb124e8388d489eef58f5223`
- self-heal proposals now mark pure external Edge RPC outages as `dispatch_allowed=false` with `dispatch_blocked_reason=EXTERNAL_EDGE_RPC_OUTAGE`, so scheduler auto-dispatch can stop burning Codex cycles on host-only drills when the upstream edge endpoint is simply unreachable
- runtime probe acceptance now treats `EXTERNAL_DEPENDENCY_BLOCKED` as a valid scheduler outcome and no longer requires a fake terminal job when the system correctly decides to observe instead of dispatch
- `host_brain/app/startup.py::collect_dependency_probes_sync()` no longer returns `{}` inside an active event loop; it now runs the async dependency probe in a dedicated worker thread so synchronous wrappers like `system_handler` and `codex_task_builder` preserve real probe data
- latest live Codex inspect run on the scheduler path (`artifacts/codex_runs/req_d1a9fb228578431593b595f5660049e0`) now produces a materially sharper diagnosis: `edge.rpc.asr` and `edge.rpc.tts` are identified as the primary failing dependencies, `graph_runtime` is treated as transitive, and `collect_dependency_probes_sync()` was surfaced as a concrete risk instead of a generic “edge is down” fallback
- after a transient startup-window probe failure (`artifacts/release/reports/codex_self_heal_scheduler_probe_1776428185.json`), the stable rerun passed again in `artifacts/release/reports/codex_self_heal_scheduler_probe_1776428466.json`, confirming the current scheduler/self-heal drill is healthy once the live host finishes bootstrapping
- `host_brain/app/startup.py` now normalizes config-disabled edge components as non-faulting dependency probes, so runtime states like `edge.rpc.asr` with `enabled=false` / `disabled by config` no longer poison host health and readiness as hard dependency failures
- the same startup normalization now preserves real enabled-but-down component failures with richer detail, including component `health.state`, `load_error`, and `last_recovery_detail`; current live readiness now pinpoints `edge.rpc.tts` with `[Errno 22] Invalid argument` instead of collapsing everything into a generic edge dependency outage
- `host_brain/app/codex_self_heal.py` now treats `graph_runtime.status=SKIPPED` as a non-faulting state, so readiness self-heal proposals and scheduler fingerprints no longer include `graph_runtime` when the only real degraded dependency is `edge.rpc.tts`
- latest live readiness on `http://127.0.0.1:18080/ready` now degrades on exactly one dependency probe (`edge.rpc.tts`), while `edge.rpc.asr` is reported `UP` with `disabled by config`; the live self-heal proposal at `/internal/codex/self_heal/proposal?source=readiness` now compiles to `affected_components=["edge.rpc.tts"]`
- latest live scheduler drill passed again in `artifacts/release/reports/codex_self_heal_scheduler_probe_1776430311.json`, and the resulting `auto_state` fingerprint is now `readiness|SERVICE_UNAVAILABLE_DEPENDENCY|edge.rpc.tts`, proving the self-heal loop is finally keyed to the single real runtime fault instead of a mixed `edge + graph_runtime` bundle
- `edge_client/actuators/tts_engine.py` now upgrades fresh-subprocess recovery from a one-shot synth fallback into a persistent backend mode: when the in-process GPT-SoVITS backend hits `Invalid argument` during load or reload, the manager can switch to `backend_mode="subprocess"`, clear stale `load_error`, stay in `READY`, and keep subsequent synth calls on the fallback path instead of oscillating back to `ERROR`
- targeted edge regressions now cover both load-time and reload-time invalid-argument recovery for the persistent subprocess backend (`tests/unit/test_tts_invalid_argument_fallback.py`, `tests/contract/test_tts_hw_contract.py`, `tests/contract/test_edge_health_contract.py`)
