受众：MASTER
标题：数字生命运行验收 Findings（N0-T01）
版本：v2.0
日期：2026-04-04
状态：已复验

## 文档目的

按 `docs/35_quality/audits/DIGITAL_LIFE_RUNTIME_VERIFICATION_PLAN_20260403.md` 对当前分支做一轮真实运行复验，把旧版“代码已接线”的判断，收口成“哪些能力已在 runtime 成立、哪些断点仍未闭环”的正式 findings。

本文重点回答：

1. scheduler 与 personality tick 是否已经真实跑通。
2. `offline_state / silence_state / body_sense / motive / response_policy` 是否已经进入统一 runtime 行为链。
3. 当前 remaining gaps 究竟是环境阻塞，还是主链闭环仍未完成。

---

## 验证环境

- 仓库：`D:\Natsume_Ai`
- 分支：`codex`
- 验证 commit：`fc19409`（`feat: unify digital life runtime snapshot`）
- 验证时间：`2026-04-04 19:40` ~ `2026-04-04 23:03`（Asia/Shanghai）
- 宿主环境：Windows + `dev`
- 验证方式：使用 `fastapi.testclient.TestClient(app)` 启动真实 FastAPI lifespan，并在同一进程内结合 `/health`、`/scheduler/health`、`/obs/runtime-snapshot`、`/v1/chat`、直接 runtime state 读取与一次主动链手动 dispatch 复验。

### 本轮补充复验命令

```powershell
@'
import json
from fastapi.testclient import TestClient
from host_brain.app.fastapi_app import app

watch = {'inner_world_tick', 'silence_tick', 'body_sense_tick', 'proactive_talk_periodic'}
with TestClient(app) as client:
    before = client.get('/scheduler/health').json()
    print('BEFORE')
    for task in before.get('tasks', []):
        if task.get('task_id') in watch:
            print(json.dumps(task, ensure_ascii=False))
    import time; time.sleep(70)
    after = client.get('/scheduler/health').json()
    print('AFTER')
    for task in after.get('tasks', []):
        if task.get('task_id') in watch:
            print(json.dumps(task, ensure_ascii=False))
'@ | python -X utf8 -
```

```powershell
@'
import json
import logging
from fastapi.testclient import TestClient
from host_brain.app.fastapi_app import app
from host_brain.app.dependencies import get_ledger
from host_brain.app.http_hooks_runtime_helpers import run_proactive_intent_dispatch

system_intent = {
    'skill_name': 'proactive_talk',
    'sys_mode': 'normal',
    'mood_state': 'steady',
    'emotion_tag': 'NEUTRAL',
    'args': {'idle_threshold_ms': 0, 'topic_hint': '那个项目'},
    'request_id': 'verify-proactive-20260404',
    'intent_id': 'verify-proactive-20260404',
}

with TestClient(app):
    result = run_proactive_intent_dispatch(
        system_intent,
        logger=logging.getLogger('digital_life_verify'),
        get_ledger=get_ledger,
        retention_policy=None,
        ui_pusher=lambda message: {'status': 'mocked', 'type': message.get('type')},
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
'@ | python -X utf8 -
```

---

## 总体结论

当前数字生命运行状态判定为：

> **scheduler 与人格 tick 已经真实跑通，runtime snapshot 也已能解释“为什么这样回”；当前主阻塞已从旧版 `UNKNOWN_SKILL` 转移为环境级 `LLM_UNAVAILABLE / edge rpc unavailable`，以及 `session_end / awaken / proactive` 闭环证据仍不足。**

本轮已确认的事实：

- `NatsumeScheduler` 已真实启动，`/scheduler/health` 为健康态，默认启用任务数为 `9`。
- `inner_world_tick`、`silence_tick`、`body_sense_tick` 在 70 秒观察窗内从 `PENDING` 进入 `OK`，且 `last_reason_code = null`，旧版 findings 中“统一掉到 `UNKNOWN_SKILL`”的结论已失效。
- `/obs/runtime-snapshot` 已能稳定汇总 `decision_chain` 与 `core_slots`，并可观测 `offline_state / silence_state / body_sense / motive / response_policy`。
- 沉默引擎与身体感已不只是“有字段”，而是会真实改写 `motive`、`reply_posture` 与 `decision_chain`。
- `/v1/chat` 主回复入口可走通，但当前环境下会在 runtime 层降级为 `LLM_UNAVAILABLE`，因此主回复链尚不能算完整端到端 PASS。
- `session_end / on_user_awaken` 在本轮真实 `/v1/chat` 降级路径下仍缺少稳定闭环证据。
- 主动表达链的旧问题已不再是 `sys_mode` 误判；补测显示它现在卡在 `intent_log` 最新时间戳超前到 `2027-01-15T16:00:00.003`，导致 idle gate 直接拒绝。

---

## 验收结果总表

| 编号 | 验证项 | 结果 | 结论 |
|---|---|---|---|
| V1 | Scheduler 是否启动 | PASS | `/scheduler/health=200`，`scheduler_status=HEALTHY`，`enabled_tasks=9` |
| V2 | Personality Tick 是否注册并运行 | PASS | `inner_world_tick` / `silence_tick` / `body_sense_tick` 在 70 秒内进入 `OK`，`last_reason_code=null` |
| V3 | 离线时间流 / 等待感 | PASS | `offline_state`、`absence_band`、`offline_reconnect_style` 已进入 runtime snapshot 且可被 6.5h synthetic round 稳定观测 |
| V4 | 会话开始 / 结束闭环 | PARTIAL | `/v1/chat` 入口可走通，但本轮降级路径下未证明 `session_end / on_user_awaken` 稳定更新 inner world |
| V5 | 沉默引擎是否影响行为 | PASS | `willingness=SILENT` 与 `topic_resistance` 可把 `motive` 推到 `withdraw`，并把回复姿态压成 `withdrawn` |
| V6 | 身体感是否影响行为 | PASS | `fatigue=EXHAUSTED`、`drowsiness=DROWSY` 会进入 `response_policy.reason_tags` 并改写 `reply_posture` |
| V7 | 关系叙事层 | PARTIAL | snapshot 已能观测 `bond` 与 narrative 摘要，但本轮未把它与真实回复差异完整闭环到内容层 |
| V8 | 主动表达引擎 | PARTIAL | 70 秒窗口内 timed task 仍未到触发周期；future `intent_log.ts_ms` 已修复后，手动 dispatch 已可 `EXECUTED`，但当前仍走模板降级而非完整 LLM 内容 |
| V9 | 记忆连续性同步写回 | PARTIAL | 运行态可读到旧 `anticipation_topics`，但本轮 `/v1/chat` 降级路径未证明最新消息被稳定写回 |
| V10 | Findings 汇总与修正建议 | PASS | 本文已刷新为复验版 findings |

---

## 关键证据

### E1：Scheduler 与 personality ticks 已真实打通

`/scheduler/health` 返回：

- `status_code = 200`
- `scheduler_status = HEALTHY`
- `enabled_tasks = 9`

70 秒观察窗内：

- `inner_world_tick`: `PENDING -> OK`
- `silence_tick`: `PENDING -> OK`
- `body_sense_tick`: `PENDING -> OK`
- 三者均有 `last_run_ts`
- 三者均为 `last_reason_code = null`
- `proactive_talk_periodic` 在 70 秒内仍为 `PENDING`，与其 `180s` interval 一致

结论：

> 旧版 findings 中“ticks 被调度但统一掉到 `UNKNOWN_SKILL`”已经不成立。当前 scheduler → personality tick runtime dispatch 已经成立，V2 应从旧版 `PARTIAL` 升为 `PASS`。

### E2：统一 runtime snapshot 已能解释离线连续性与行为姿态

基线 `GET /obs/runtime-snapshot?current_topic=那个项目` 返回：

- `headline = "mode=normal | bond=STRANGER | motive=continuity | reply=focused"`
- `current_state.sys_mode = normal`
- `reply_posture.style = focused`
- `decision_chain = ["mode:normal", "self_reflection_guard:medium", "motive:continuity", "response_policy:focused"]`

构造 6.5 小时久别状态后再次观测：

- `headline = "mode=normal | bond=STRANGER | offline=6h_plus | motive=continuity | reply=focused"`
- `current_state.absence_band = 6h_plus`
- `current_state.offline_reconnect_style = soft_resume`
- `core_slots.offline_state.offline_hours = 6.5`
- `core_slots.offline_state.anticipation_topics` 含 `那个项目`

结论：

> `offline_state / continuity / runtime snapshot` 已经真实成立，V3 可判定为 `PASS`。

### E3：沉默引擎与身体感已进入真实行为链

在 runtime 中手动压低意愿并提高疲劳后，`GET /obs/runtime-snapshot?current_topic=那个项目` 返回：

- `headline = "mode=normal | bond=STRANGER | offline=6h_plus | motive=withdraw | reply=withdrawn"`
- `current_state.willingness_level = SILENT`
- `current_state.fatigue_level = EXHAUSTED`
- `current_state.drowsiness_level = DROWSY`
- `current_state.topic_resistance_active = true`
- `reply_posture.style = withdrawn`
- `reply_posture.max_chars = 33`
- `reply_posture.should_be_withdrawn = true`
- `decision_chain` 含：
  - `offline:6h_plus->soft_resume`
  - `silence:SILENT`
  - `silence:topic_resistance`
  - `body:EXHAUSTED/DROWSY`
  - `motive:withdraw`
  - `response_policy:withdrawn`
- `core_slots.response_policy.reason_tags` 含：
  - `willingness:SILENT`
  - `topic_resistance:active`
  - `body:fatigue=EXHAUSTED,drowsy=DROWSY`

结论：

> 沉默引擎与身体感已经不只是“tick 会执行”，而是会真实决定 reply posture 与 motive，V5 / V6 可判定为 `PASS`。

### E4：主回复入口可用，但当前被环境级降级截断

`/health?skip_dependency_probes=1` 返回：

- `status_code = 200`
- `status = OK`
- `required_unavailable = 1`

但 startup report 与日志持续出现：

- `OPENAI_API_KEY missing`
- `edge rpc unreachable: ConnectError; attempted=http://127.0.0.1:17800`
- Chroma / identity probing 仍有降级

真实 `POST /v1/chat` 结果：

- `status_code = 200`
- COMMIT：`decision = REJECT`
- `reason_code = LLM_UNAVAILABLE`
- 仍会返回降级 narrative reply

结论：

> 主回复链入口已连通，但当前环境无法把数字生命验证推进到完整 LLM 输出层。当前最大的端到端阻塞已从旧版 personality dispatch 断点，转移为环境级 `LLM_UNAVAILABLE / edge rpc unavailable`。

### E5：`session_end / awaken` 闭环证据仍不足

第一次 `/v1/chat` 后直接读取 inner world：

- `last_interaction_ts_ms` 未稳定更新为本次聊天时间
- `anticipation_topics` 仍保留旧残留：`["贪吃蛇", "continue", "remember me"]`

对 6.5 小时离线状态执行“唤醒式”聊天后再次读取：

- `loneliness` 未观察到明确回落
- 本轮未拿到 `on_user_awaken` 完整收口证据

结论：

> 本轮只能证明 `/v1/chat` 真实入口通了，还不能证明 `session_end` 与 `on_user_awaken` 在降级路径下稳定闭环，因此 V4 只能维持 `PARTIAL`。

### E6：主动表达链已修复 future timestamp 阻塞，并可模板降级执行

主动链补测使用：

- `skill_name = proactive_talk`
- `sys_mode = normal`
- `args.idle_threshold_ms = 0`
- `args.topic_hint = 那个项目`

第一轮补测返回：

- `status = REJECTED`
- `reason_code = SCHED_CONDITION_UNMET`
- `detail = "idle time -24685027034ms < threshold 0ms"`

进一步直接查询 `audit.sqlite3`：

- `SELECT ts_ms FROM intent_log ORDER BY ts_ms DESC LIMIT 1`
- 返回 `1800000000003`
- 对应时间：`2027-01-15T16:00:00.003`

修复后再次补测：

- `status = EXECUTED`
- `reason_code = null`
- `message.type = proactive_talk`
- `message.content = "主人夜深了，注意休息哦～"`
- `template_fallback_used = true`

结论：

> 主动链的 future `intent_log.ts_ms` 阻塞已经修复；当前 manual dispatch 已可执行，V8 保持 `PARTIAL` 的剩余原因不再是 idle gate，而是 timed scheduler 尚未补 180s 实窗复验，且内容层仍受 `LLM_UNAVAILABLE` 影响走模板降级。

---

## 根因与 Top Gaps

### Gap 1（P0）

**环境级 `LLM_UNAVAILABLE / edge rpc unavailable` 仍阻塞完整端到端验收。**

影响：

- `/v1/chat` 主回复链降级到 fallback narrative
- 主动表达链无法完成真实内容生成
- 无法把 V4 / V7 / V8 / V9 升到完整 PASS

建议优先动作：

- 恢复 `OPENAI_API_KEY`
- 恢复 `127.0.0.1:17800` Edge RPC
- 重新跑本 findings 中的 `/v1/chat` 与 proactive dispatch 验证

### Gap 2（P0）

**`session_end / on_user_awaken` 在真实 `/v1/chat` 降级路径下仍缺少稳定闭环证据。**

现象：

- `last_interaction_ts_ms` 未稳定追到当前轮
- `anticipation_topics` 未稳定写回当前话题
- 唤醒后 `loneliness` 未观察到明确收敛

建议优先动作：

- 增加可重复的 hook-level 验证样本
- 定位 `session_end` 与 `awaken` 是否被 LLM unavailable 分支绕过
- 补最小自动化回归

### Gap 3（P1）

**主动表达链仍缺少 180s 调度实窗复验，且当前内容层仅验证到模板降级。**

现象：

- future `intent_log.ts_ms` 已修复
- 手动 proactive dispatch 已可 `EXECUTED`
- 但 70 秒观察窗仍不足以证明 `proactive_talk_periodic` 定时任务真实触发
- 当前执行内容仍因 `LLM_UNAVAILABLE` 走模板 fallback

建议优先动作：

- 补一轮 `>=180s` 的 scheduler 真实观察窗
- 恢复 LLM/Edge 依赖后重跑主动链内容层验收
- 将 manual dispatch 与 timed task 结果并入同一轮 findings

---

## 历史结论修正

旧版 `DIGITAL_LIFE_RUNTIME_FINDINGS_20260404.md` 的核心结论是：

> `inner_world_tick / silence_tick / body_sense_tick` 已被 scheduler 触发，但统一落到 `UNKNOWN_SKILL`。

本轮复验后，这一结论应正式作废，修正为：

> scheduler 与 personality ticks 已真实打通；当前 remaining gaps 已转移到环境级 LLM/edge 依赖、会话闭环证据不足，以及主动链仅完成模板降级验证、尚未补足 timed scheduler 实窗证据。

---

## 结论与下一步建议

本轮 `N0-T01` 复验可以收口成一句话：

> **数字生命底盘已经不是“像在跑”，而是 scheduler、snapshot、silence、body_sense 都已在 runtime 成立；但完整体验仍卡在环境级降级与少数闭环断点。**

建议下一步执行顺序：

1. 先恢复或隔离 `LLM_UNAVAILABLE / edge rpc unavailable` 环境阻塞。
2. 再补 `session_end / on_user_awaken` 的可重复验收样本与回归。
3. 补 `>=180s` 的 proactive timed task 实窗复验，并与 manual dispatch 合并验收。
4. 修完后再刷新一次本 findings，并把 V4 / V8 / V9 重新判定。
