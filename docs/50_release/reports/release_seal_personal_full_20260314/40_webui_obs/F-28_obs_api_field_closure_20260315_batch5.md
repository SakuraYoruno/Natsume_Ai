# F-28 Obs / API / 字段闭环记录（2026-03-15）

## 结论

- `F-28 Obs / API / 字段闭环`：**BLOCK**

说明：本轮以真实主链请求为样本，对 `/obs/*` 端点返回结果与 `audit.sqlite3` 源表进行逐项对照。结果确认当前至少有 3 处观测口径与源数据不一致，不能满足“API 可用；关键字段可追溯；页面观测与源数据一致”的封测要求。

## 一、取证样本

### 1. 主链真实样本

- 通过 `POST /v1/host/input` 触发主链请求：
  - `request_id = req_794776d1ef7a44f197dbbad3ad28727d`
  - `session_id = seal-f14-mainchain-asr`
- Host 返回：
  - `status = ACCEPTED`
  - 后续在 `voice_event_log` 中可见 `INPUT_ACCEPTED -> INPUT_DISPATCHED -> INPUT_COMPLETED`
- 同一请求在 `graph_run_trace` 中落盘：
  - `status = OK`
  - `total_latency_ms = 8549`
- 同一请求在 `error_log(stage=AUDIT_SNAPSHOT)` 中落盘：
  - `reason_code = AUDIT_SNAPSHOT_WRITTEN`

### 2. 源表统计（最近 60 分钟）

- `error_log` 中 `stage = AUDIT_SNAPSHOT`：`1`
- `voice_event_log` 中 `INPUT_ACCEPTED / INPUT_DISPATCHED / INPUT_COMPLETED / INPUT_FAILED / INPUT_NOT_WIRED`：`3`
- `voice_event_log` 中 `INPUT_COMPLETED` 的去重 `request_id` 数：`1`

## 二、Obs 端点与源表对照

### 1. `/obs/chain/stats`

端点返回：

- `total_requests = 0`
- 所有 node `total_calls = 0`

但源表事实：

- 最近 60 分钟 `error_log(stage=AUDIT_SNAPSHOT)` 已有 `1` 条真实主链审计快照。

代码核对：

- `obs_routes.py` 当前从 `audit_log` 读取 `AUDIT_SNAPSHOT`
- 但 `main_chain.AuditNode` 实际把审计快照写入的是 `error_log(stage='AUDIT_SNAPSHOT')`

判断：

- 当前 `/obs/chain/stats` 与真实主链审计数据不一致。

### 2. `/obs/input/dispatch-stats`

端点返回：

- `total_accepted = 0`
- `total_completed = 0`
- `sources = []`

但源表事实：

- 最近 60 分钟 `voice_event_log` 已存在：
  - `INPUT_ACCEPTED`
  - `INPUT_DISPATCHED`
  - `INPUT_COMPLETED`

代码核对：

- `obs_routes.py` 当前过滤的是：
  - `ACCEPTED / DISPATCHED / COMPLETED / FAILED / NOT_WIRED`
  - `ASR_ACCEPTED / ASR_COMPLETED / ASR_FAILED`
  - `WECHAT_ACCEPTED / WECHAT_COMPLETED / WECHAT_FAILED`
- 实际落地事件名带 `INPUT_` 前缀，因此被统计逻辑漏掉。

判断：

- 当前 `/obs/input/dispatch-stats` 与真实输入分发事件不一致。

### 3. `/obs/context-compressor/stats`

端点返回：

- `total_compressions = 0`
- `summary_generated_count = 0`

本轮补充核对发现：

1. `main_chain` 当前调用的 `get_context_compressor()` 在 `dependencies.py` 中不存在，压缩能力主链未接线。
2. 该 stats 端点当前读取的候选表为：
   - `context_compressor_log`
   - `audit_log`
   - `error_log`
3. 当前仓内不存在 `audit_log` / `context_compressor_log` 的有效落盘闭环，统计口径与真实审计落点不一致。

判断：

- 当前 `/obs/context-compressor/stats` 不能作为正式压缩观测依据。

## 三、当前结论

1. `/obs/chain/stats`、`/obs/input/dispatch-stats`、`/obs/context-compressor/stats` 与源表至少存在 3 处明确偏差。
2. 当前问题不是单纯“页面没展示”，而是 API 统计口径与真实落盘表/事件名不一致。
3. 在此状态下，`F-28` 不能通过，应判定为 **BLOCK**。

## 四、后续修复方向

1. 统一 `obs_routes.py` 的读表口径，优先对齐当前真实落盘表：
   - `AUDIT_SNAPSHOT -> error_log`
   - `INPUT_* -> voice_event_log`
2. 修正 `/obs/input/dispatch-stats` 对 `INPUT_ACCEPTED / INPUT_COMPLETED / INPUT_FAILED / INPUT_NOT_WIRED` 的事件名匹配。
3. 在 `F-14` 修复主链压缩依赖接线后，再决定 `context-compressor/stats` 应读取的最终权威源表。
