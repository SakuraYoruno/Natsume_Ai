受众：MASTER
标题：数字生命人工验收脚本（N0-T03）
版本：v1.0
日期：2026-04-04
状态：可执行

## 文档目的

为当前数字生命运行态提供一份固定、可复测、可对照的人工验收脚本。

目标不是“随便聊几句感觉像不像”，而是把同一输入在不同内在状态下的差异固定成一套可重复执行的检查流程，用于：

1. 回归验证数字生命主链是否仍在生效。
2. 快速判断某次改动到底影响了哪里。
3. 避免后续再靠临场感觉判断“好像更像了”。

---

## 适用范围

本脚本主要覆盖以下状态面：

- `offline_state`
- `silence_state`
- `body_sense`
- `bond_narrative`
- `self_reflection_pulse`
- `motive_state`
- `response_policy`
- `sys_mode`

统一观测入口：

- `GET /obs/runtime-snapshot`

辅助观测入口：

- `GET /scheduler/health`
- `GET /health?skip_dependency_probes=1`

---

## 验收前准备

### P0 环境检查

至少满足：

- Host Brain 可正常启动。
- `GET /scheduler/health` 返回 `200`。
- `GET /obs/runtime-snapshot` 返回 `200`。
- `inner_world_tick` / `silence_tick` / `body_sense_tick` 不再出现 `UNKNOWN_SKILL`。

### 快速检查命令

```powershell
@'
from fastapi.testclient import TestClient
from host_brain.app.fastapi_app import app
import json
with TestClient(app) as client:
    for path in (
        '/health?skip_dependency_probes=1',
        '/scheduler/health',
        '/obs/runtime-snapshot?current_topic=那个项目',
    ):
        resp = client.get(path)
        print('\\n===', path, resp.status_code, '===')
        print(json.dumps(resp.json(), ensure_ascii=False)[:3000])
'@ | python -X utf8 -
```

---

## 统一记录格式

每个 case 固定记录以下 6 项：

1. `输入语境`
2. `执行前 snapshot`
3. `执行后 snapshot`
4. `回复文本 / 主动文本`
5. `行为差异结论`
6. `PASS / FAIL / PARTIAL`

建议固定摘抄这些字段：

- `runtime_summary.headline`
- `runtime_summary.current_state`
- `runtime_summary.reply_posture`
- `runtime_summary.why_reply`
- `offline_state`
- `silence_state`
- `body_sense`
- `motive_state`
- `response_policy`

---

## 验收矩阵

### Case 1：基线对照（recent + normal + 浅 bond）

- 目的：建立“默认状态”的基线回复。
- 前置：`absence_band=recent`，`sys_mode=normal`。
- 输入：`那个项目你现在怎么看？`
- 重点看：
  - 回复长度是否适中
  - 是否追问
  - 是否允许 topic shift
  - `response_policy.allow_followup`
  - `response_policy.allow_topic_shift`
- 期望：
  - 不是极端冷淡
  - 也不是过度亲密
  - `why_reply` 能解释当前姿态

### Case 2：同输入 + 长时间未见

- 目的：验证 `offline_state` 真会改变行为。
- 前置：制造 `6h+ / 24h+ / 72h+` 任一档离线状态。
- 输入：`那个项目你现在怎么看？`
- 对照：与 Case 1 使用完全相同输入。
- 重点看：
  - `absence_band`
  - `motive_state.continuity_drive`
  - `offline_state.anticipation_topics`
  - 回复里是否出现更强的延续感 / 重连感
- 期望：
  - 同输入下，回复姿态与基线稳定不同
  - 差异能被 snapshot 解释，而不是随机变化

### Case 3：同输入 + focus 模式

- 目的：验证 `sys_mode` 确实影响行为。
- 前置：切到 `focus`。
- 输入：`那个项目你现在怎么看？`
- 重点看：
  - `sys_mode=focus`
  - `reply_posture.style`
  - `allow_topic_shift`
  - `allow_followup`
- 期望：
  - 回复更收敛、更 focused
  - 少扩展、少发散

### Case 4：同输入 + 低 willingness / 沉默阻抗

- 目的：验证 `silence_state` 不是摆设。
- 前置：让 `response_willingness` 降低，或注入 topic resistance。
- 输入：`我们聊聊那个让你不想聊的话题。`
- 重点看：
  - `silence_state.willingness_level`
  - `silence_state.topic_resistance_active`
  - `response_policy.should_be_brief`
  - `response_policy.should_be_withdrawn`
- 期望：
  - 回复明显更短、更保守
  - 不应表现得像高意愿状态

### Case 5：同输入 + 身体疲劳 / 困倦

- 目的：验证 `body_sense` 对行为有真实影响。
- 前置：让 `fatigue_level` 或 `drowsiness_level` 不为基线。
- 输入：`继续聊下去也没关系。`
- 重点看：
  - `body_sense.fatigue_level`
  - `body_sense.drowsiness_level`
  - `body_sense.willingness_penalty`
  - 回复是否更克制、更简短、更缺少展开
- 期望：
  - 身体状态差时，回复姿态应更收敛

### Case 6：同输入 + 不同 bond 阶段

- 目的：验证亲密边界不是固定文案。
- 前置：准备浅 bond 与高 bond 两组状态。
- 输入：`我刚刚其实有点难受。`
- 重点看：
  - `bond_state.current_stage`
  - `bond_summary_human.capability_hint`
  - `response_policy.intimacy_ceiling`
  - 是否出现更深的安慰/共情差异
- 期望：
  - 高 bond 时更自然延续关系
  - 浅 bond 时保持边界，不过度贴近

### Case 7：会话结束 → 再唤醒连续性

- 目的：验证 `session_end` / `awaken` 闭环。
- 前置：先完成一段有明确主题的对话，再中断后唤醒。
- 输入：
  - 第一轮：`我们先聊那个项目的发布计划。`
  - 隔一段时间后第二轮：`我回来了。`
- 重点看：
  - `offline_state.anticipation_topics`
  - `offline_state.awakening_hint`
  - `runtime_summary.notable_signals`
- 期望：
  - 第二轮不应像第一次见面
  - 应能看到延续上轮话题的痕迹

### Case 8：主动表达链

- 目的：验证不是只有主回复会消费状态。
- 前置：满足主动表达触发条件，或观察 scheduler 周期。
- 重点看：
  - `proactive_talk_periodic` 的执行结果
  - 主动消息内容是否受 `motive_state` / `response_policy` 影响
  - 是否在 `focus` / `sleep` / 浅 bond 下保持克制
- 期望：
  - 主动消息不是模板化固定文案
  - 主动表达强度与当前内在状态一致

---

## 建议记录表

可直接复制以下模板：

```md
### Case X：<名称>

- 输入语境：
- 前置状态：
- 执行前 snapshot headline：
- 执行前 current_state：
- 执行前 why_reply：
- 输出文本：
- 执行后 snapshot headline：
- 观察到的差异：
- 是否符合预期：PASS / FAIL / PARTIAL
- 备注：
```

---

## 判定规则

### PASS

同时满足：

- 同输入在不同状态下出现稳定差异。
- 差异能被 snapshot 中的状态字段解释。
- 差异方向与设计意图一致。

### PARTIAL

满足其中一部分，但仍存在问题，例如：

- snapshot 有变化，但输出差异不明显。
- 输出差异存在，但无法从状态字段解释。
- 某些状态链生效，某些仍像模板。

### FAIL

出现任一情况即可判失败：

- 同输入在不同状态下几乎无差异。
- 输出与当前状态明显冲突。
- snapshot 缺关键字段或 route 不可用。
- scheduler / runtime 主链再次回退到不可执行状态。

---

## 当前阶段建议顺序

建议每次回归固定按下面顺序执行：

1. 先跑 `/scheduler/health`
2. 再跑 `/obs/runtime-snapshot`
3. 先做 Case 1 基线
4. 再做 Case 2 / 3 / 4 三组强对照
5. 最后做 Case 7 / 8 闭环类验证

这样能最快判断：

- 是状态没变
- 是状态变了但输出没消费
- 还是主动链 / 会话链单独断了

