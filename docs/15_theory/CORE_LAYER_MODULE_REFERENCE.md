受众：MASTER
标题：host_brain/core/ 层模块参考

# host_brain/core/ 层模块参考

版本：v1.0
日期：2026-03-19
归属：docs/15_theory/

---

## 概述

`host_brain/core/` 是整个系统的**基础数据契约层**，不包含业务逻辑，只定义：

- 数据结构（dataclass / TypedDict）
- 验证函数（validate_*）
- 归一化函数（normalize_*）
- 常量与枚举

上层模块（`integrations/`、`app/`、`lsp/`）均依赖此层，此层不得反向依赖上层。

---

## 模块一览

| 文件 | 职责 |
|---|---|
| `prompt_context.py` | Prompt 上下文槽位契约与验证 |
| `metabolism.py` | 硬状态（HardState）+ 情感向量（EmotionVector）+ 代谢机制 |
| `emotion_timeline.py` | 情感时间线（从 metabolism 拆分，独立模块） |
| `services.py` | 服务注册表、健康报告、启动编排 |
| `errors.py` | 全局错误码常量 |
| `ids.py` | ID 生成与时间戳工具 |
| `key_profiles.py` | 密钥配置文件管理 |
| `config_snapshot.py` | 配置快照工具 |
| `qa.py` / `qa_checks.py` | QA 检查契约 |
| `service_reporters.py` | 服务状态上报辅助 |

---

## prompt_context.py

### 槽位定义

`PromptContext` 是一个 `dict[str, Any]`，包含以下**必填槽位**：

| 槽位名 | 类型 | 语义 |
|---|---|---|
| `hard_snapshot` | `dict` | 当前硬状态快照（mood_state、energy_level、allowed_emotion_tags 等） |
| `bond_summary` | `dict` | 关系摘要（stage、trend、event_id、evidence_refs） |
| `authoritative` | `list` | 权威知识条目（world_canon、宪法约束等） |
| `retrieval` | `list` | 检索结果条目（RAG 召回内容） |
| `style_pack` | `dict` | 风格包（语气、说话风格等） |
| `constraints` | `dict` | 约束规则（安全约束、行为边界） |
| `tool_meta_pack` | `list` | 工具元数据（工具描述，不含执行参数） |

**可选槽位**：

| 槽位名 | 类型 | 语义 |
|---|---|---|
| `semantic_slot` | `list` | 语义记忆蒸馏结果（M10-C） |
| `emotion_pulse` | `dict` | 当前情感脉冲（emotion_tag、mood_state、valence、arousal、intensity、style_hint） |
| `bond_texture` | `dict` | 关系纹理（细粒度关系描述） |
| `memory_emotion_hint` | `dict` | 记忆情感提示 |

### 槽位隔离规则

- `authoritative` 槽位**禁止**包含 `tone`、`speaking_style`、`style_instruction` 等风格字段（防止 CP_SLOT_MIXED）
- `style_pack` 槽位**禁止**包含 `event_id`、`evidence_refs`、`fact`、`snapshot` 等审计字段
- `tool_meta_pack` 条目**禁止**包含 `args`、`command`、`input`、`result` 等执行字段（防止 CP_TOOL_META_FORBIDDEN_FIELD）
- `authoritative` 中 `kind=world_canon` 的条目**禁止**断言执行事实（防止 CP_CANON_AS_FACT）

### 动态槽位注册（M28-G OPT-16）

```python
from host_brain.core.prompt_context import register_slot

register_slot("my_slot", lambda ctx: ctx.get("my_data"))
```

注册后，`validate_prompt_context()` 会自动识别该槽位，不再报 `PC_UNKNOWN_SLOT`。

### 错误码

| 错误码 | 含义 |
|---|---|
| `PROMPT_CONTEXT_MISSING_SLOT` | 缺少必填槽位 |
| `PROMPT_CONTEXT_UNKNOWN_SLOT` | 出现未知槽位 |
| `PROMPT_CONTEXT_TYPE_INVALID` | 槽位类型不匹配 |
| `CONTEXT_SLOT_MIXED` | 槽位内容混入了其他槽位的字段 |
| `CANON_AS_FACT_VIOLATION` | world_canon 内容断言了执行事实 |
| `BOND_SUMMARY_NUMERIC_FORBIDDEN` | bond_summary 包含数值字段 |
| `BOND_SUMMARY_FIELD_INVALID` | bond_summary 字段非法 |
| `TOOL_META_INVALID` | tool_meta_pack 条目非法 |
| `TOOL_META_FORBIDDEN_FIELD` | tool_meta_pack 包含执行字段 |

---

## metabolism.py

### HardState（硬状态）

```python
@dataclass(frozen=True)
class HardState:
    energy_level: int      # [0, 100]，能量值
    mood_state: str        # calm / steady / engaged / tired / critical
    sys_mode: str          # normal / focus / gaming / maintenance / locked
    safety_flag: bool      # 安全标志
    last_event_id: str | None
    ts_ms: int             # 最后更新时间戳（毫秒）
```

**energy_bucket 映射**：

| energy_level | bucket |
|---|---|
| < 20 | `very_low` |
| 20–39 | `low` |
| 40–69 | `normal` |
| ≥ 70 | `high` |

### EmotionVector（VAD 情绪连续体，M28-A OPT-1）

```python
@dataclass
class EmotionVector:
    valence: float    # 效价 [-1.0, 1.0]，正=积极，负=消极
    arousal: float    # 唤醒度 [-1.0, 1.0]，正=兴奋，负=平静
    dominance: float  # 支配感 [-1.0, 1.0]，正=主导，负=顺从
    ts_ms: int
```

关键方法：
- `decay(rate=0.05)` — 情绪衰减，向中性靠近
- `blend(other, weight=0.3)` — 情绪混合
- `to_mood_state()` — 映射为离散 mood_state
- `to_emotion_tag()` — 映射为离散 emotion_tag（NEUTRAL/HAPPY/EXCITED/SAD/ANGRY/SURPRISED/CALM/THINKING）

### mood_state → emotion_tag 约束（M26-G）

| mood_state | 允许的 emotion_tag（第一个为降级默认值） |
|---|---|
| `critical` | NEUTRAL, SAD |
| `tired` | NEUTRAL, CALM, SAD |
| `calm` | NEUTRAL, CALM, THINKING |
| `steady` | NEUTRAL, CALM, HAPPY, THINKING, SAD, ANGRY |
| `engaged` | HAPPY, EXCITED, SURPRISED, THINKING, NEUTRAL, ANGRY |

### HardStateSnapshot（M28-B OPT-6）

`build_hard_snapshot(state)` 从 `HardState` 构建快照，包含：
- `mood_state`、`energy_level`
- `allowed_emotion_tags`（tuple）
- `emotion_range_hint`（中文情感范围提示）
- `emotion_ts_ms`（情感状态时间戳，M29-K OPT-18）

### 治理快照（governance_snapshot）

`build_governance_snapshot(state, overrides=None)` 产出供 Governor 使用的快照，包含：
- `trust_level`、`energy_level`、`sys_mode`
- `rate_limit_hit`、`airlock_blocked`、`safety_flag`
- `scope`、`scope_allowlist`
- `hard_state_summary`（含 energy_bucket、mood_state、emotion_ts_ms）
- `mood_state`、`emotion_tag`、`emotion_intensity`（M27-C OPT-22 透传）

### 事件语义扩展（M28-E OPT-13）

`evolve_hard_state()` 支持以下 `event_kind`：

| event_kind | 效果 |
|---|---|
| `emotion_spike` | 能量 +5，mood → engaged（energy≥60 时） |
| `emotion_calm` | mood → calm（energy≥40 时） |
| `bond_deepen` | 能量 +3 |
| 其他 | 按 energy 自动推断 mood |

---

## emotion_timeline.py

从 `metabolism.py` 拆分的独立模块，包含 `EmotionTimeline`、`EmotionTimelineEntry`、`compute_emotion_echo`。

### EmotionTimeline（M29-E OPT-9）

```python
timeline = EmotionTimeline(max_entries=50)
timeline.append(ts_ms=..., emotion_tag="HAPPY", valence=0.6, arousal=0.4, source="user_input")
summary = timeline.rebuild_summary(window_ms=300_000)  # 5 分钟窗口
```

`rebuild_summary()` 返回：
- `dominant_emotion`：窗口内出现最多的情感标签
- `trend`：`improving` / `stable` / `declining`（基于前后半段 valence 均值对比，阈值 0.15）
- `entry_count`、`entries`（最近 5 条）

### compute_emotion_echo（M29-H OPT-12）

情感回声：Natsume 的情感向用户情感靠近（共情），但保持独立性（不完全镜像）。

```python
echo = compute_emotion_echo("SAD", current_vector=vector, echo_weight=0.4)
# 返回: echo_emotion_tag, echo_valence, echo_arousal, response_hint
```

---

## services.py

### ServiceRegistry

服务注册表，单一真相来源。

```python
registry = ServiceRegistry()
registry.register(ServiceSpec(service_id="host.audit.sqlite_ledger", owner="host", required=True))
```

默认注册表（`build_default_service_registry()`）包含 8 个 host 必需服务：
- `host.audit.sqlite_ledger`
- `host.governor.decision`
- `host.skills.loader`
- `host.integrations.llm_gateway`
- `host.retrieval.embedding`
- `host.retrieval.reranker`
- `host.vision.screen_ocr`
- `host.vision.camera_recognizer`

Edge 服务为可选依赖，不可用时降级并记录 WARNING。

### ServiceHealthReporter

`build_report(request_id, runtime_statuses, ts_ms)` 产出服务健康报告：
- `overall_status`：UP / DEGRADED
- `services`：每个服务的 status（UP / DOWN / DEGRADED）、reason_code、detail

### ServiceStartupOrchestrator

`build_startup_report(...)` 合并 config/runtime/probe 三路状态，产出启动报告：
- `boot_status`：READY / DEGRADED
- `startup_issues`、`dependency_probes`、`services`

### NodeSpec 与节点清单

`NodeSpec` 字段：`node_id`、`region`、`role`、`services`、`health_endpoint`、`key_profile_id`

允许的 `role` 值：`orchestrator`、`audit`、`llm`、`retrieval`、`vision`、`edge_gateway`、`edge_voice`、`edge_realtime`、`standby`

`validate_node_inventory()` 检查：节点 ID 唯一性、角色唯一性、健康端点唯一性、必需服务覆盖率。

---

## 依赖关系

```
host_brain/core/
  ├── errors.py          ← 无依赖（纯常量）
  ├── ids.py             ← 无依赖
  ├── metabolism.py      ← 依赖 ids.py
  ├── emotion_timeline.py← 依赖 metabolism.py（TYPE_CHECKING only）
  ├── prompt_context.py  ← 依赖 tools/catalog.py、bond/summarizer.py、memory/canon_world_store.py
  └── services.py        ← 依赖 audit/sqlite_ledger.py、errors.py、ids.py
```

**禁止反向依赖**：`core/` 模块不得导入 `integrations/`、`app/`、`lsp/` 中的任何模块。
