# 数字生命内分泌门控可观测变更留痕（2026-04-08）

## 1) 变更目标
- 把调度门控从“仅 mood_state”扩展为“mood + endocrine gate”。
- 明确“为什么被拦”：在 API、运行时快照、WebUI 三层可见。
- 为后续 TRIBE/EndocrineAgent 迁移保留稳定字段。

## 2) 后端变更范围
- `host_brain/scheduler/scheduler.py`
  - 新增主动任务门控原因：`ENDOCRINE_PROACTIVE_BLOCKED`。
  - 保留历史原因：`EMOTION_GATE_BLOCKED`、`ENDOCRINE_GATE_BLOCKED`。
  - 主动任务在 `gate_state in {cautious, protective}` 时阻断。
  - 低风险白名单任务可在 `protective` 态继续运行。
- `host_brain/scheduler/health.py`
  - 新增任务健康字段：`last_gate_state`、`last_mood_state`、`last_endocrine_cortisol`、`last_endocrine_energy`。
- `host_brain/app/http_hooks_runtime_helpers.py`
  - `runtime-snapshot` 增加：
    - 顶层 `endocrine_state`
    - `emotion_context.endocrine_state`
    - `background_runtime.proactive_task`
    - `background_runtime.gate_blocked_tasks`
- `host_brain/app/obs_routes_stats.py`
  - `/obs/scheduler/history` 增加门控聚合与任务级字段。

## 3) `/obs/scheduler/history` 新增字段

### 任务级（`tasks[]`）
- `last_reason_code`
- `last_gate_state`
- `last_mood_state`
- `last_endocrine_cortisol`
- `last_endocrine_energy`

### 聚合级（响应根级）
- `gate_blocked_total`
- `gate_blocked_rate`
- `blocked_reason_codes[]`
- `gate_states[]`

## 4) 配置项（`.env` / `.env.example`）
- `HOST_ENDOCRINE_LOW_RISK_TASK_ALLOWLIST`
  - 逗号分隔 `task_id`，默认：`inner_world_tick,silence_tick,body_sense_tick`
  - 语义：低风险任务在 protective 态不拦截
- `HOST_ENDOCRINE_PROACTIVE_TASK_IDS`
  - 逗号分隔 `task_id`，默认：`proactive_talk_periodic`
  - 语义：主动任务在 cautious/protective 态阻断

## 5) 前端可观测入口
- `host_brain/app/monitor_ui/src/pages/Automation.jsx`
  - 展示门控统计、任务级 reason/gate/mood/cortisol/energy。
- `host_brain/app/monitor_ui/src/pages/Insights.jsx`
  - 新增“调度门控统计”模块，并支持跳转至自动化页继续下钻。
- `host_brain/app/monitor_ui/src/components/BackgroundRuntimeCard.jsx`
  - 展示 `proactive_task` 与 `gate_blocked_tasks`。
- `host_brain/app/monitor_ui/src/pages/EmotionDashboard.jsx`
  - 展示 endocrine gate 与激素状态条。

## 6) 验收建议
- 单测/集成：
  - `tests/unit/test_scheduler_emotion_gate.py`
  - `tests/unit/test_scheduler_emotion_intent_fields.py`
  - `tests/unit/test_http_hooks_runtime_helpers.py`
  - `tests/integration/test_obs_scheduler_history_stats.py`
- 脚本化验收：
  - `scripts/runtime/verify_multi_agent_p0.ps1`
