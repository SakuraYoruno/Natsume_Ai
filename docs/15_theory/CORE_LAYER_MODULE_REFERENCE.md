受众：MASTER
标题：host_brain/core/ 层模块参考（当前代码实况）

版本：v3.1
日期：2026-06-30
归属：docs/15_theory/

---

定位：本文描述当前代码里的 `host_brain/core/` 层，而不是早期“纯 dataclass / validate_* / 无上游依赖”的理想化分层口径。
若与旧文档冲突，以本文和代码为准。

## 0. 代码证据入口

- `host_brain/core/prompt_context.py`
- `host_brain/core/metabolism.py`
- `host_brain/core/emotion_timeline.py`
- `host_brain/core/endocrine.py`
- `host_brain/core/services.py`
- `host_brain/core/mode_transition.py`
- `host_brain/core/activity_monitor.py`
- `host_brain/core/predictive_state.py`
- `host_brain/core/meta_learning_loop.py`
- `host_brain/core/runtime_helpers.py`
- `host_brain/core/errors.py`
- `host_brain/core/ids.py`
- `host_brain/core/config_snapshot.py`
- `host_brain/core/key_profiles.py`
- `host_brain/core/qa.py`
- `host_brain/core/qa_checks.py`
- `host_brain/core/emotion_constants.py`
- `host_brain/core/resource_manager.py`
- `host_brain/core/soft_fail.py`
- `host_brain/core/service_reporters.py`

---

## 一、当前层定位

当前 `host_brain/core/` 仍然承担“基础状态与通用契约”的角色，但它已经不只是一个零业务依赖的数据层。

当前现实更接近：

- 一部分模块是底层状态/类型契约
- 一部分模块是运行时辅助与监控工具
- 少数模块已经直接依赖 `audit/` 或 `app/`

典型例子：

- `services.py` 直接依赖 `host_brain.audit.sqlite_ledger`
- `runtime_helpers.py` 直接依赖 `host_brain.app.prompt_template_store`

所以当前不能再把 `core/` 简化理解为“完全不碰业务的纯基础层”。

---

## 二、当前主模块一览

| 文件 | 当前职责 |
|---|---|
| `prompt_context.py` | Prompt 上下文槽位契约、验证、动态槽位注册 |
| `metabolism.py` | `HardState` / `EmotionVector`、硬状态演化、治理快照 |
| `emotion_timeline.py` | 情感时间线与 `compute_emotion_echo` |
| `endocrine.py` | 从离散硬状态派生连续内分泌基调 |
| `services.py` | Service registry、健康报告、启动报告、节点清单校验 |
| `mode_transition.py` | sys_mode 切换服务 |
| `activity_monitor.py` | Host 本地活动/游戏/空闲监控 |
| `predictive_state.py` | 周期性情绪模式预测 |
| `meta_learning_loop.py` | 系统指标记录与参数调整建议 |
| `runtime_helpers.py` | 模板、SSE、Airlock、trace 强制转换等纯函数工具 |
| `errors.py` | 全局错误码常量 |
| `ids.py` | request/intent/event/timestamp 工具 |
| `config_snapshot.py` | 配置快照辅助 |
| `key_profiles.py` | 密钥配置文件管理 |
| `qa.py` / `qa_checks.py` | QA 契约与检查逻辑 |
| `service_reporters.py` | 服务状态上报辅助 |
| `emotion_constants.py` | 情绪常量（tag / mood）与常用枚举 |
| `resource_manager.py` | 运行时资源会计（该由 ANS / endocrine 上报） |
| `soft_fail.py` | 软限失败语义：在不推倒主链的前提下记录迍重错误 |

---

## 三、prompt_context.py 当前契约

### 3.1 必填槽位

`EXPECTED_SLOTS` 当前定义的必填槽位：

- `hard_snapshot`
- `bond_summary`
- `authoritative`
- `retrieval`
- `style_pack`
- `constraints`
- `tool_meta_pack`

### 3.2 可选槽位

`OPTIONAL_SLOTS`（以 `host_brain/core/prompt_context.py` 代码为准）：

- `semantic_slot`
- `emotion_pulse`
- `emotion_recall`
- `bond_texture`
- `memory_emotion_hint`
- `self_reflection_pulse`（Phase III：自省脉冲）
- `bond_narrative`（Phase V：关系叙事）

另有 `_DYNAMIC_SLOT_REGISTRY` 允许运行时通过 `register_slot(name, builder_fn)` 注册动态槽位，并由 `build_dynamic_slots(context_data)` 统一构建（M28-G OPT-16）。

这说明当前 prompt context 已经预留了：

- 情绪回忆补充
- 语义槽
- 自省脉冲
- 关系叙事
- 运行时动态扩展点

等更丰富的注入位。

### 3.3 动态槽位注册

当前仍支持：

```python
register_slot(name: str, builder_fn: Any)
```

注册后，`validate_prompt_context()` 会把该槽位当成合法槽位处理。

### 3.4 当前验证重点

当前验证仍重点防止：

- `authoritative` 混入风格字段
- `style_pack` 混入审计字段
- `tool_meta_pack` 混入执行参数/结果
- `world_canon` 条目断言执行事实

---

## 四、metabolism.py 当前契约

### 4.1 HardState

当前核心状态结构仍是：

```python
HardState(
    energy_level: int,
    mood_state: str,
    sys_mode: str,
    safety_flag: bool,
    last_event_id: str | None,
    ts_ms: int,
)
```

`sys_mode` 当前代码口径包括：

- `normal`
- `focus`
- `gaming`
- `maintenance`
- `locked`
- `sleep`

### 4.2 EmotionVector

当前连续情绪结构：

```python
EmotionVector(
    valence: float,
    arousal: float,
    dominance: float,
    ts_ms: int,
)
```

当前主能力仍包括：

- `decay()`
- `blend()`
- `to_mood_state()`
- `to_emotion_tag()`

### 4.3 快照函数

当前两个关键输出：

- `build_hard_snapshot(state)`
- `build_governance_snapshot(state, overrides=None)`

其中 `build_governance_snapshot()` 会产出 Governor 直接消费的：

- `trust_level`
- `energy_level`
- `sys_mode`
- `rate_limit_hit`
- `airlock_blocked`
- `safety_flag`
- `scope`
- `scope_allowlist`
- `hard_state_summary`
- `mood_state`
- `emotion_tag`
- `emotion_intensity`

所以 `core/metabolism.py` 现在不仅服务情绪层，也直接服务治理层。

---

## 五、emotion_timeline.py 与 endocrine.py

### 5.1 emotion_timeline.py

当前该模块负责：

- `EmotionTimeline`
- `EmotionTimelineEntry`
- `compute_emotion_echo()`

其中 `rebuild_summary()` 会给出：

- `dominant_emotion`
- `trend`
- `entry_count`
- 最近窗口条目

### 5.2 endocrine.py

这是旧文档没有覆盖、但当前代码已真实存在的基础状态模块。

核心接口：

```python
derive_endocrine_state(
    hard_state=...,
    emotion_tag: str | None = None,
    emotion_intensity: float | None = None,
) -> EndocrineState
```

`EndocrineState` 当前输出：

- `dopamine_level`
- `serotonin_level`
- `cortisol_level`
- `energy_reserve`
- `gate_state`

`gate_state` 当前为：

- `protective`
- `cautious`
- `open`

这套连续状态已经被 Scheduler/治理侧拿来做门控参考。

---

## 六、services.py 当前契约

### 6.1 当前定位

`services.py` 不只是“服务清单工具”，而是：

- Service registry
- 运行时健康汇总
- 启动报告收口
- 节点清单与依赖探针归一化

### 6.2 build_default_service_registry()

当前默认注册的 Host required 服务有 8 个：

- `host.audit.sqlite_ledger`
- `host.governor.decision`
- `host.skills.loader`
- `host.integrations.llm_gateway`
- `host.retrieval.embedding`
- `host.retrieval.reranker`
- `host.vision.screen_ocr`
- `host.vision.camera_recognizer`

同时：

- 会尝试从 `edge_client.rpc.edge_required_services()` 拉取 Edge 服务声明
- Edge 不可用时按降级处理，不会阻断 Host 启动

### 6.3 ServiceHealthReporter

`build_report()` 当前主要输出：

- `summary.overall_status`
- `required_total`
- `required_unavailable`
- `optional_unavailable`
- `services[]`

状态值主要为：

- `UP`
- `DOWN`
- `DEGRADED`

### 6.4 ServiceStartupOrchestrator

`build_startup_report()` 会合并：

- runtime statuses
- dependency probes
- startup issues

并给出：

- `boot_status`
- `issue_count`
- `probe_total`
- `services`
- `dependency_probes`
- `startup_issues`

### 6.5 NodeSpec / 节点清单

`NodeSpec` 当前仍包含：

- `node_id`
- `region`
- `role`
- `services`
- `health_endpoint`
- `key_profile_id`

`validate_node_inventory()` 负责检查：

- 节点 ID 唯一性
- health endpoint 唯一性
- 角色冲突
- 必需服务覆盖

---

## 七、mode_transition.py 与 activity_monitor.py

这两份也是当前 `core/` 的有效运行时模块，旧文档未覆盖。

### 7.1 ModeTransitionService

`mode_transition.py` 当前负责：

- 手动切换 sys_mode
- 自动规则切换
- 审计模式切换事件

主要来源：

- `manual`
- `auto_gaming`
- `auto_idle`
- `auto_wakeup`
- `auto_schedule`
- `skill`

### 7.2 ActivityMonitor

`activity_monitor.py` 当前负责：

- Windows 前台窗口/进程检测
- 游戏进程匹配
- 空闲检测
- 早间唤醒检查

它与 `ModeTransitionService` 共同组成当前 Host 端“模式切换自动化”的基础层。

---

## 八、predictive_state.py 与 meta_learning_loop.py

这两份同样说明 `core/` 已经不只是静态 schema 层。

### 8.1 PredictiveStateEngine

`predictive_state.py` 当前通过历史时段样本预测：

- 当前时段主导情绪
- 置信度
- 主动提示语

### 8.2 MetaLearningLoop

`meta_learning_loop.py` 当前负责：

- 记录模块运行指标
- 生成 `EvolutionReport`
- 给出 `ParameterAdjustment`

这两部分更接近“基础运行时能力”，而不是单纯数据结构。

---

## 九、runtime_helpers.py

这也是旧文档未覆盖的重要现实模块。

当前主要提供：

- 模板读取/格式化工具
- `_sse()` 输出封装
- Airlock 输入/输出净化工具
- reply preview 构造
- trace mapping/bool/score 强制转换

需要注意：

- 它当前会依赖 `host_brain.app.prompt_template_store`
- 因此它已经不是“完全脱离 app 层的纯基础工具”

---

## 十、当前依赖关系应如何理解

当前更准确的理解是：

```text
core/
  = 基础状态 + 通用约束 + 运行时辅助
```

而不是：

```text
core/
  = 绝对纯净、零上游依赖、只放 dataclass/常量
```

当前实际代码里至少存在这些上游依赖：

- `services.py -> audit.sqlite_ledger`
- `runtime_helpers.py -> app.prompt_template_store`

所以 `core/` 现在是“偏底层，但并非完全无业务耦合”的层。

---

## 十一、当前不再适用的旧口径

以下说法不再准确：

- `core/` 只定义 dataclass / validate_* / normalize_*，不含运行时模块
- `core/` 不依赖任何 `app/`、`audit/` 相关模块
- `core/` 只需要关注 `prompt_context / metabolism / services` 三四个老模块

当前正确口径应是：

1. `core/` 仍是基础层，但已经包含模式切换、活动监控、预测状态、元学习、内分泌基调等运行时辅助模块。
2. `prompt_context.py` 的可选槽位已经明显扩展。
3. `services.py` 已承担启动报告、服务健康和节点清单校验职责。
4. `core/` 当前并不是严格零上游依赖层。
