受众：MASTER
标题：数字生命运行验收 Findings（N0-T01）
版本：v2.4
日期：2026-04-05
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

> **scheduler、人格 tick、runtime snapshot、沉默/身体感、主动表达引擎、以及 `/v1/chat` 写回→下一轮 readback 链都已在 runtime 成立；当前主阻塞主要收缩为环境级 `LLM_UNAVAILABLE / edge rpc unavailable`，以及关系叙事层与完整 LLM 内容层验证。**

本轮已确认的事实：

- `NatsumeScheduler` 已真实启动，`/scheduler/health` 为健康态，默认启用任务数为 `9`。
- `inner_world_tick`、`silence_tick`、`body_sense_tick` 在 70 秒观察窗内从 `PENDING` 进入 `OK`，且 `last_reason_code = null`，旧版 findings 中“统一掉到 `UNKNOWN_SKILL`”的结论已失效。
- `/obs/runtime-snapshot` 已能稳定汇总 `decision_chain` 与 `core_slots`，并可观测 `offline_state / silence_state / body_sense / motive / response_policy`。
- 沉默引擎与身体感已不只是“有字段”，而是会真实改写 `motive`、`reply_posture` 与 `decision_chain`。
- `/v1/chat` 主回复入口可走通，但当前环境下会在 runtime 层降级为 `LLM_UNAVAILABLE`，因此主回复链尚不能算完整端到端 PASS。
- 已定位并修复 `LLM_UNAVAILABLE` 早退绕过 `_async_post_process` 的断点；降级路径现在仍会触发 `session_end` 与 episodic memory writeback。
- 已补齐 live `/v1/chat` 两轮真实样本：首轮 `WRITE/SUCCESS` 会落账，第二轮 prompt 可稳定读回 `offline_hours / offline_awakening_hint / offline_anticipation_topics`。
- 主动表达链已补修两处 runtime 断点：future `intent_log.ts_ms` 不再污染 idle gate，scheduler 形态的 `mood_state=steady` 也不再被误判成 `sys_mode=steady`。
- 在 seeded `>45m` idle 条件下，`proactive_talk_periodic` 已经过一轮真实 `195s` 观察窗触发成功，并通过模板 fallback 推送出主动消息。

---

## 验收结果总表

| 编号 | 验证项 | 结果 | 结论 |
|---|---|---|---|
| V1 | Scheduler 是否启动 | PASS | `/scheduler/health=200`，`scheduler_status=HEALTHY`，`enabled_tasks=9` |
| V2 | Personality Tick 是否注册并运行 | PASS | `inner_world_tick` / `silence_tick` / `body_sense_tick` 在 70 秒内进入 `OK`，`last_reason_code=null` |
| V3 | 离线时间流 / 等待感 | PASS | `offline_state`、`absence_band`、`offline_reconnect_style` 已进入 runtime snapshot 且可被 6.5h synthetic round 稳定观测 |
| V4 | 会话开始 / 结束闭环 | PASS | 已同时具备 degraded `/v1/chat` → `6.5h` synthetic `on_user_awaken` snapshot，以及 live `/v1/chat` 两轮样本下的真实 writeback → 下一轮 readback 证据 |
| V5 | 沉默引擎是否影响行为 | PASS | `willingness=SILENT` 与 `topic_resistance` 可把 `motive` 推到 `withdraw`，并把回复姿态压成 `withdrawn` |
| V6 | 身体感是否影响行为 | PASS | `fatigue=EXHAUSTED`、`drowsiness=DROWSY` 会进入 `response_policy.reason_tags` 并改写 `reply_posture` |
| V7 | 关系叙事层 | PARTIAL | snapshot 已能观测 `bond` 与 narrative 摘要，但本轮未把它与真实回复差异完整闭环到内容层 |
| V8 | 主动表达引擎 | PASS | future `intent_log.ts_ms` 与 timed scheduler 的 `mood_state→sys_mode` 误判都已修复；seeded `>45m` idle 下，`proactive_talk_periodic` 在真实 `195s` 观察窗内 `OK` 且成功推送主动消息 |
| V9 | 记忆连续性同步写回 | PASS | 真实 `/v1/chat` 两轮样本已验证 `memory_ops_log` 连续出现 `WRITE/SUCCESS`，且第二轮 prompt 可读回上一轮写入的 `offline_anticipation_topics` |
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

### E5：`/v1/chat` 写回→`awaken/readback` 链已补齐真实闭环证据

已定位到根因：

- `top_agent / chat_agent` 在 `LLM_UNAVAILABLE` 分支会直接 `return`
- 早退前没有调度 `_async_post_process`
- 导致 `persist_chat_episodic_memory`、`inner_world.on_session_end` 与 `body_sense.on_conversation_end` 被整段绕过

修复后新增自动化回归：

- `tests/unit/test_top_agent_sse.py` 新增降级链回归
- 在真实 `top_agent_sse_generator -> _async_post_process` 链路下，`COMMIT.reason_code = LLM_UNAVAILABLE` 仍成立
- 同时验证 `persist_chat_episodic_memory` 被调用 1 次
- 同时验证 `inner_world.on_session_end(... pending_topics=["remember me"])` 被调用
- 同时验证 `body_sense.on_conversation_end()` 被调用
- 同文件再补 `6.5h` synthetic gap 回归：degraded `/v1/chat` 写回后，`on_user_awaken()` 可读出 `offline_hours=6.5`、`absence_band=6h_plus`、`anticipation_topics=["那个项目"]`

本轮新增实窗回归：

- `tests/integration/test_chat_agent_soul_pipeline.py` 新增 `test_http_chat_route_multi_turn_writeback_rehydrates_next_round_prompt`
- 使用真实 `/v1/chat` 两轮请求，首轮验证 `COMMIT.decision = APPROVE`
- 首轮后处理真实落账：`memory_ops_log` 至少出现 1 条 `WRITE/SUCCESS`
- 首轮 `session_end` 会把 `anticipation_topics` 写入 inner world
- 第二轮请求前推进 `6.5h`，第二轮 prompt 可读回：
  - `- offline_hours: 6.5`
  - `- offline_awakening_hint:`
  - `- offline_anticipation_topics:`
  - `那个项目`
- 两轮结束后 `memory_ops_log` 至少出现 2 条 `WRITE/SUCCESS`

结论：

> degraded `/v1/chat` 写回、synthetic `on_user_awaken` snapshot、以及 live `/v1/chat` 两轮 writeback → 下一轮 readback 证据现在都已补齐，V4 / V9 可以同步升级为 `PASS`。

### E6：主动表达引擎已在 manual dispatch 与 timed scheduler 两条路径通过

主动链补测使用：

- `skill_name = proactive_talk`
- `sys_mode = normal`
- `args.idle_threshold_ms = 0`
- `args.topic_hint = 那个项目`

前序补测先确认了两个 runtime 断点：

- `status = REJECTED`
- `reason_code = SCHED_CONDITION_UNMET`
- `detail = "idle time -24685027034ms < threshold 0ms"`

进一步直接查询 `audit.sqlite3`：

- `SELECT ts_ms FROM intent_log ORDER BY ts_ms DESC LIMIT 1`
- 返回 `1800000000003`
- 对应时间：`2027-01-15T16:00:00.003`

其后 manual dispatch 补测返回：

- `status = EXECUTED`
- `reason_code = null`
- `message.type = proactive_talk`
- `message.content = "主人夜深了，注意休息哦～"`
- `template_fallback_used = true`

再补一轮 seeded `>45m` idle 的真实 `195s` scheduler 观察窗：

- `before.proactive_talk_periodic.status = PENDING`
- `after.proactive_talk_periodic.status = OK`
- `after.proactive_talk_periodic.last_reason_code = null`
- `push_count = 1`
- `last_push.content = "主人夜深了，注意休息哦～"`

说明：

- 本轮为了满足默认 `45m` idle gate，使用临时 ledger seed 了一条 `46m` 之前的 intent 记录。
- push client 使用内存 mock，只替换外部发送侧，不改变 scheduler / dispatcher / proactive skill 的真实执行链。
- 当前内容来源仍是模板 fallback，因为运行环境仍处于 `LLM_UNAVAILABLE`。

结论：

> 主动表达引擎已经在 manual dispatch 与 timed scheduler 两条 runtime 路径跑通，V8 可升级为 `PASS`；当前剩余的只是 LLM 不可用时的内容层降级，而不再是调度或执行闭环问题。

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

### Gap 2（P1）

**关系叙事层与真实回复内容层的联动仍缺少完整闭环证据。**

现象：

- `bond_narrative`、`response_policy`、`motive_state` 已进入 runtime snapshot
- 但当前环境仍被 `LLM_UNAVAILABLE` 降级，真实 LLM 内容层还没完成复验
- `V7` 仍停留在“可观测、可解释”，尚未完成“稳定改写真实回复内容”的端到端证据

建议优先动作：

- 恢复真实 LLM / Edge RPC 后重跑 `/v1/chat` 内容层验收
- 对照同输入下不同 `bond / motive / response_policy` 的回复差异
- 补关系叙事层进入真实 narrative 的最小自动化回归

### Gap 3（P2）

**live 长离线样本下的 `loneliness / waiting expression` 内容层验证仍不足。**

现象：

- synthetic `6.5h` 与 live 两轮 readback 证据已补齐
- 但更长离线样本下的 `loneliness` 收敛、waiting expression 与真实 narrative 差异仍主要停留在 snapshot / prompt 层

建议优先动作：

- 补 `24h+ / 72h+` 样本的真实 `/v1/chat` 内容层验收
- 对照 `offline_state`、prompt 注入与最终 narrative 的一致性
- 补长离线档位的最小自动化回归

---

## 历史结论修正

旧版 `DIGITAL_LIFE_RUNTIME_FINDINGS_20260404.md` 的核心结论是：

> `inner_world_tick / silence_tick / body_sense_tick` 已被 scheduler 触发，但统一落到 `UNKNOWN_SKILL`。

本轮复验后，这一结论应正式作废，修正为：

> scheduler、personality ticks、proactive engine、以及 `/v1/chat` 写回→下一轮 readback 链都已真实打通；当前 remaining gaps 已转移到环境级 LLM/edge 依赖，以及关系叙事层与完整内容层验证。

---

## 结论与下一步建议

本轮 `N0-T01` 复验可以收口成一句话：

> **数字生命底盘已经不是“像在跑”，而是 scheduler、snapshot、silence、body_sense 都已在 runtime 成立；但完整体验仍卡在环境级降级与少数闭环断点。**

建议下一步执行顺序：

1. 先恢复或隔离 `LLM_UNAVAILABLE / edge rpc unavailable` 环境阻塞。
2. 恢复真实 LLM / Edge RPC 后，补 `bond_narrative / response_policy / motive_state` 对真实回复内容的验收。
3. 补长离线档位（`24h+ / 72h+`）的 live 内容层观察与自动化回归。
4. 修完后再刷新一次本 findings，并把 `V7` 与内容层相关结论重新判定。
