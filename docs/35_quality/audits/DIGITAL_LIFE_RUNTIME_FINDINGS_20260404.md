受众：MASTER
标题：数字生命运行验收 Findings（N0-T01）
版本：v1.0
日期：2026-04-04
状态：已验证

## 文档目的

按 `docs/35_quality/audits/DIGITAL_LIFE_RUNTIME_VERIFICATION_PLAN_20260403.md` 对当前分支做一轮真实运行验收，把“代码已接线”收口成“运行已证实 / 已发现断点”的正式 findings。

本文重点回答：

1. scheduler 是否真实启动。
2. 数字生命核心 tick 是否只是“注册到了 registry”，还是已经形成“触发 → 执行 → 状态更新”的闭环。
3. 当前运行环境里，哪些问题属于环境阻塞，哪些问题属于主链实现断点。

---

## 验证环境

- 仓库：`D:\Natsume_Ai`
- 分支：`codex`
- 验证 commit：`460ea0c`（`feat: add self reflection narrative guardrails`）
- 验证时间：`2026-04-04 19:40:45` ~ `2026-04-04 19:41:56`（Asia/Shanghai）
- 宿主环境：Windows + `dev`
- 验证方式：使用 `fastapi.testclient.TestClient(app)` 启动真实 FastAPI lifespan，并在同一进程内观察 `/health` 与 `/scheduler/health`

### 本轮使用的验证命令

```powershell
@'
from fastapi.testclient import TestClient
from host_brain.app.fastapi_app import app
import json
with TestClient(app) as client:
    health = client.get('/health?skip_dependency_probes=1')
    sched = client.get('/scheduler/health')
    print('HEALTH_STATUS', health.status_code)
    print(json.dumps(health.json(), ensure_ascii=False))
    print('SCHED_STATUS', sched.status_code)
    print(json.dumps(sched.json(), ensure_ascii=False))
'@ | python -X utf8 -
```

```powershell
@'
import json, time
from fastapi.testclient import TestClient
from host_brain.app.fastapi_app import app
watch = {'inner_world_tick', 'silence_tick', 'body_sense_tick', 'proactive_talk_periodic'}
with TestClient(app) as client:
    before = client.get('/scheduler/health').json()
    print('BEFORE')
    for task in before.get('tasks', []):
        if task.get('task_id') in watch:
            print(json.dumps(task, ensure_ascii=False))
    time.sleep(70)
    after = client.get('/scheduler/health').json()
    print('AFTER')
    for task in after.get('tasks', []):
        if task.get('task_id') in watch:
            print(json.dumps(task, ensure_ascii=False))
'@ | python -X utf8 -
```

---

## 总体结论

当前数字生命运行状态判定为：

> **基础调度已成立，但核心人格 tick 仍未形成真正可执行闭环。**

本轮已确认的事实：

- `NatsumeScheduler` 已真实启动，`/scheduler/health` 可返回健康快照，且默认已注册 8 个任务。
- `inner_world_tick`、`silence_tick`、`body_sense_tick` 在 70 秒观察窗内确实被 scheduler 触发。
- 但这 3 个 tick 的执行结果统一为 `last_reason_code="UNKNOWN_SKILL"`，说明当前仍停留在“任务被调度”而不是“skill 被成功执行”。
- `proactive_talk_periodic` 已注册，但 70 秒窗口内未触发；这与其 `180s` interval 配置一致，本轮不足以判定失败。

因此，`N0-T01` 的结论不是“数字生命已经跑通”，而是：

> **调度底盘是真的，注册也是真的，但 scheduler → runtime dispatch → personality skill execution 之间还有一个 P0 断点。**

---

## 验收结果总表

| 编号 | 验证项 | 结果 | 结论 |
|---|---|---|---|
| V1 | Scheduler 是否启动 | PASS | `/scheduler/health=200`，`scheduler_status=HEALTHY`，`enabled_tasks=8` |
| V2 | Personality Tick 是否注册并运行 | PARTIAL | `inner_world_tick` / `silence_tick` / `body_sense_tick` 已注册且被触发，但执行结果为 `UNKNOWN_SKILL` |
| V3 | 离线时间流 / 等待感 | NOT_RUN | 本轮未进入会话前后状态对照，且被 V2 断点阻塞 |
| V4 | 会话开始 / 结束闭环 | NOT_RUN | 未做 awaken / session_end 行为链验收 |
| V5 | 沉默引擎是否影响行为 | NOT_RUN | 仅观察到 tick 触发，未观察回复行为差异 |
| V6 | 身体感是否影响行为 | NOT_RUN | 仅观察到 tick 触发，未观察回复行为差异 |
| V7 | 关系叙事层 | NOT_RUN | 本轮未做 bond narrative 输入输出验证 |
| V8 | 主动表达引擎 | PARTIAL | `proactive_talk_periodic` 已注册，但 70 秒窗口内未触发，不足以下结论 |
| V9 | 记忆连续性同步写回 | NOT_RUN | 本轮未做多轮会话与写回验证 |
| V10 | Findings 汇总与修复建议 | PASS | 本文已产出 |

---

## 关键证据

### E1：Scheduler 启动证据成立

`/scheduler/health` 返回：

- `status_code = 200`
- `scheduler_status = HEALTHY`
- `total_tasks = 8`
- `enabled_tasks = 8`

已观察到的任务清单：

- `proactive_talk_periodic`
- `inner_world_tick`
- `silence_tick`
- `body_sense_tick`
- `gaming_companion_tick`
- `long_term_memory_distillation`
- `audit_retention_daily`
- `emergent_growth_weekly`

结论：**V1 通过，scheduler 不是“代码存在但未启动”的假接线。**

### E2：核心人格 tick 确实被触发，但没有执行成功

70 秒观察窗口前：

- `inner_world_tick = PENDING`
- `silence_tick = PENDING`
- `body_sense_tick = PENDING`
- `proactive_talk_periodic = PENDING`

70 秒观察窗口后：

- `inner_world_tick = OK`，`last_run_ts` 有值，`last_reason_code = UNKNOWN_SKILL`
- `silence_tick = OK`，`last_run_ts` 有值，`last_reason_code = UNKNOWN_SKILL`
- `body_sense_tick = OK`，`last_run_ts` 有值，`last_reason_code = UNKNOWN_SKILL`
- `proactive_talk_periodic = PENDING`

结论：

- `inner_world_tick` / `silence_tick` / `body_sense_tick` **确实被 scheduler timer 触发了**。
- 但触发后的执行链没有进入对应 personality skill handler。
- 当前只能证明“任务调度存在”，还不能证明“数字生命状态机真实运转”。

### E3：运行环境存在降级，但不等于本轮主断点

本轮同时观察到以下降级日志：

- `OPENAI_API_KEY missing`
- `edge rpc unreachable: ConnectError; attempted=http://127.0.0.1:17800`
- `StateArbiter: EpisodicStore 注册失败（降级）`
- `进化周期任务注册失败（降级）: NORMAL`
- `Personality 工具注册失败（降级）: No module named 'host_brain.skills.impl.inner_world_skill'`

其中需要区分两类问题：

1. **环境依赖缺失**
   - `OPENAI_API_KEY missing`
   - Edge RPC 不可达
   - 这些会影响完整端到端能力，但**不是**本轮 `UNKNOWN_SKILL` 的直接原因。
2. **主链实现断点**
   - `UNKNOWN_SKILL`
   - `personality_tools_catalog` 导入失败
   - 这是数字生命 tick 无法落到 handler 的主因带。

### E4：`/health?skip_dependency_probes=1` 与启动报告存在观测差异

`/health?skip_dependency_probes=1` 返回：

- `status_code = 200`
- `overall_status = DEGRADED`
- `required_unavailable = 1`
- 唯一明确 down 项为 `host.integrations.llm_gateway`

但同一轮 startup report 中同时记录：

- 5 个 `edge.rpc.*` probe 全部不可达
- `required_unavailable = 6`

结论：

> `skip_dependency_probes=1` 的健康快照适合看 host 本体是否存活，但不适合作为完整运行环境健康结论。

这不是数字生命主链 bug，但会影响验收时对“环境是否已就绪”的判断精度。

---

## 根因分析

### F1（P0）：scheduler 已能触发任务，但 runtime dispatcher 不认识这些 personality tick

本轮已经确认：

- `host_brain/app/http_hooks.py` 启动时把 `inner_world_tick`、`silence_tick`、`body_sense_tick` 注册进 scheduler。
- 但 `host_brain/app/http_hooks_runtime_helpers.py` 中的 `run_proactive_intent_dispatch(...)` 只显式处理：
  - `proactive_talk`
  - `evolution_cycle`
  - `audit_retention`
- 对其它 `skill_name` 直接返回：
  - `{"status": "SKIPPED", "reason_code": "UNKNOWN_SKILL"}`

这与 70 秒观察窗中的 `last_reason_code="UNKNOWN_SKILL"` 完全吻合。

结论：

> **当前的直接 root cause 不是“scheduler 没触发”，而是“dispatcher 根本不分发这些 tick skill”。**

### F2（P0）：personality registry 仍引用不存在的实现模块

`host_brain/skills/personality_registry.py` 仍尝试导入：

- `host_brain.skills.impl.inner_world_skill`
- `host_brain.skills.impl.silence_skill`
- `host_brain.skills.impl.body_sense_skill`

但当前 `host_brain/skills/impl/` 目录中并不存在这些模块，导致启动阶段出现：

- `Personality 工具注册失败（降级）: No module named 'host_brain.skills.impl.inner_world_skill'`

这说明即便 dispatcher 被补通，personality skill registry 这层仍需要同步修复，否则工具目录、技能注册与运行行为仍会持续不一致。

### F3（P1）：主动表达链本轮只验证到“已注册”，还未验证到“已执行”

`proactive_talk_periodic` 已出现在 scheduler health 中，但由于其 interval 为 `180s`，本轮 `70s` 观察窗内仍为 `PENDING`。

因此当前只能得出：

- 任务注册成立
- 默认启用成立
- 但是否在真实 idle 条件下成功触发、触发后是否生成有效主动消息，本轮尚未验证

---

## 当前 Top Gaps 排序

### Gap 1（P0）

**补通 scheduler → dispatcher → personality tick handler 的真实执行链。**

建议优先修复方向：

- 不再让 `run_proactive_intent_dispatch(...)` 只认 `proactive_talk`
- 改为接入统一 skill registry，或显式补齐 `inner_world_tick` / `silence_tick` / `body_sense_tick` / `gaming_companion_tick` 的分发表

### Gap 2（P0）

**修复 `personality_registry` 中失效的 skill 实现导入。**

建议优先修复方向：

- 对齐 `host_brain/skills/impl/` 的真实文件结构
- 删除过时导入路径，或补回缺失的 skill wrapper
- 让 catalog 注册状态与 runtime 可执行状态重新一致

### Gap 3（P1）

**补齐完整验收环境，避免“代码已修好但外部依赖仍降级”。**

包括但不限于：

- `OPENAI_API_KEY`
- Edge RPC（`127.0.0.1:17800`）
- EpisodicStore / evolution task 的降级原因

---

## 历史异常记录

上一轮观察中还出现过：

- `World canon startup sync degraded: table canon_change_log has no column named before_body`

本轮未再次复现，因此暂不列为当前主阻塞，但建议继续保留为迁移兼容性风险项。

---

## 结论与下一步建议

本轮 `N0-T01` 验收已经把当前数字生命运行现状收束成一句话：

> **数字生命底盘已经不是假功能，但当前还卡在“被 scheduler 触发 ≠ 被人格技能真实执行”的关键断点。**

建议下一步执行顺序：

1. 先修 `UNKNOWN_SKILL` 的 dispatcher 断点。
2. 再修 `personality_registry` 的失效导入。
3. 修完后重跑本验收文档中的两条命令。
4. 然后继续推进 `N0-T02`：补统一 runtime snapshot 观测面。

