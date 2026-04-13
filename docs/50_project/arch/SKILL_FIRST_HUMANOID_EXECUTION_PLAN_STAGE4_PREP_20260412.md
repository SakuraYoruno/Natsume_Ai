# Skill-First 人体架构执行补充方案（P4-Pre）

日期：2026-04-12  
状态：`COMPLETED`

## 1. 目标
- 将 P3 输出的 `forge_candidates` 接到可执行的 `proposal artifact` 流程。
- 接入 governor 审批 token 门控，并显式建模 `shadow/canary/full/rollback` 发布状态机。

## 2. 本轮落地
- 新增服务：`host_brain/app/capability_forge.py`
  - `CapabilityForgeService`（进程内）
  - 功能：
    - 从 candidate 生成 proposal artifact
    - 签发 approval token（`TokenService`）
    - 审批校验并推进 stage（shadow/canary/full）
    - rollback 标记
- 新增依赖注入：
  - `get_capability_forge_token_service()`
  - `get_capability_forge_service()`
- 扩展观测 API（`obs_routes_stats.py`）：
  - `POST /obs/capability-gaps/forge-proposals/preview`
  - `POST /obs/capability-gaps/forge-proposals`
  - `GET /obs/capability-gaps/forge-proposals`
  - `GET /obs/capability-gaps/forge-proposals/{proposal_id}`
  - `POST /obs/capability-gaps/forge-proposals/{proposal_id}/approve`
  - `POST /obs/capability-gaps/forge-proposals/{proposal_id}/rollback`

## 3. Proposal Artifact Schema（当前版）
- 核心字段：
  - `proposal_id / candidate_key / status / created_ts_ms / updated_ts_ms`
  - `candidate_metrics`（detected/executed/failed/failure_rate/impact）
  - `artifact`（family/tags/summary/implementation_blueprint/test_blueprint）
  - `governor_gate`（required/approval_token_ref/token_skill_name/token_seq）
  - `release_plan`（shadow/canary/full/rollback）
  - `promotion`（stage/history）

## 4. 与 P4 正式阶段衔接
- 可直接把 `proposal` 作为 P4 Forge pipeline 的输入工件。
- 下一步建议：
  - 持久化 proposal 到审计库
  - 接入 canary 真实流量路由与自动回滚触发器
  - 把 `APPROVED/FULL/ROLLED_BACK` 对接前端执行面板与告警

